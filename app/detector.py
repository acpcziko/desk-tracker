"""CPU person detector: YOLO11n exported to ONNX, run with onnxruntime."""
import math
import threading

import cv2
import numpy as np
import onnxruntime as ort

PERSON_CLASS = 0


class PersonDetector:
    def __init__(self, model_path, confidence: float = 0.35, threads: int = 2):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        inp = self._session.get_inputs()[0]
        self._input = inp.name
        self._size = inp.shape[2] if isinstance(inp.shape[2], int) else 640
        self.confidence = confidence
        self._lock = threading.Lock()

    def detect(self, img) -> list[tuple[int, int, int, int, float]]:
        """Return person boxes as (x1, y1, x2, y2, score) in image pixels."""
        h, w = img.shape[:2]
        s = self._size
        r = min(s / w, s / h)
        nw, nh = int(round(w * r)), int(round(h * r))
        px, py = (s - nw) // 2, (s - nh) // 2
        canvas = np.full((s, s, 3), 114, np.uint8)
        canvas[py:py + nh, px:px + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255.0

        with self._lock:
            out = self._session.run(None, {self._input: blob})[0]

        pred = out[0].T  # (anchors, 4 + classes): cx, cy, w, h, class scores
        scores = pred[:, 4 + PERSON_CLASS]
        keep = scores >= self.confidence
        pred, scores = pred[keep], scores[keep]
        if not len(pred):
            return []

        cx, cy, bw, bh = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        x1 = np.clip((cx - bw / 2 - px) / r, 0, w)
        y1 = np.clip((cy - bh / 2 - py) / r, 0, h)
        x2 = np.clip((cx + bw / 2 - px) / r, 0, w)
        y2 = np.clip((cy + bh / 2 - py) / r, 0, h)
        rects = [[float(a), float(b), float(c - a), float(d - b)] for a, b, c, d in zip(x1, y1, x2, y2)]
        idx = cv2.dnn.NMSBoxes(rects, scores.tolist(), self.confidence, 0.45)
        return [
            (int(x1[i]), int(y1[i]), int(x2[i]), int(y2[i]), float(scores[i]))
            for i in np.array(idx).flatten()
        ]

    def detect_region(self, img, roi=None, tile: int = 960, overlap: float = 0.2, max_tiles: int = 6):
        """Detect inside roi=(x0, y0, x1, y1) pixels, splitting it into overlapping tiles so small or
        partly hidden people in a high-resolution frame keep their detail (each tile is scaled to the model
        input on its own instead of the whole frame being shrunk at once)."""
        h, w = img.shape[:2]
        x0, y0, x1, y1 = roi if roi else (0, 0, w, h)
        rw, rh = x1 - x0, y1 - y0
        nx, ny = max(1, math.ceil(rw / tile)), max(1, math.ceil(rh / tile))
        while nx * ny > max_tiles:
            tile = int(tile * 1.2)
            nx, ny = max(1, math.ceil(rw / tile)), max(1, math.ceil(rh / tile))
        tw = min(rw, math.ceil(rw / nx * (1 + overlap)))
        th = min(rh, math.ceil(rh / ny * (1 + overlap)))
        found = []
        for iy in range(ny):
            ty = y0 + (round((rh - th) * iy / (ny - 1)) if ny > 1 else 0)
            for ix in range(nx):
                tx = x0 + (round((rw - tw) * ix / (nx - 1)) if nx > 1 else 0)
                for bx1, by1, bx2, by2, s in self.detect(img[ty:ty + th, tx:tx + tw]):
                    found.append((bx1 + tx, by1 + ty, bx2 + tx, by2 + ty, s))
        return merge_boxes(found) if nx * ny > 1 else found


def merge_boxes(boxes, iou_thr: float = 0.45, contain_thr: float = 0.7):
    """Remove duplicates from overlapping tiles: a box is dropped when it overlaps a stronger one (IoU) or lies
    mostly inside it (the half-person a tile edge cuts off)."""
    keep = []
    for b in sorted(boxes, key=lambda b: -b[4]):
        area_b = max((b[2] - b[0]) * (b[3] - b[1]), 1)
        dup = False
        for k in keep:
            iw = min(b[2], k[2]) - max(b[0], k[0])
            ih = min(b[3], k[3]) - max(b[1], k[1])
            if iw <= 0 or ih <= 0:
                continue
            inter = iw * ih
            area_k = (k[2] - k[0]) * (k[3] - k[1])
            if inter / area_b >= contain_thr or inter / (area_b + area_k - inter) >= iou_thr:
                dup = True
                break
        if not dup:
            keep.append(b)
    return keep
