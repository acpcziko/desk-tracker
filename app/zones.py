"""Desk zones: polygons drawn per camera, stored with coordinates normalised to 0..1."""
import json
import threading

import cv2
import numpy as np


class Zones:
    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        self._masks: dict = {}
        self._data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def all(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def desks(self, cam_id: str) -> list[dict]:
        with self._lock:
            return list(self._data.get(cam_id, {}).get("desks", []))

    def set_camera(self, cam_id: str, desks: list[dict]) -> None:
        clean = []
        for d in desks:
            pts = [[min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)] for x, y in d["points"]]
            if len(pts) < 3:
                raise ValueError(f"desk {d.get('name')!r} needs at least 3 points")
            clean.append({
                "id": str(d["id"]),
                "name": str(d.get("name", "")).strip(),
                "employee": str(d.get("employee", "")).strip(),
                "points": pts,
            })
        with self._lock:
            self._data[cam_id] = {"desks": clean}
            self._masks = {k: v for k, v in self._masks.items() if k[0] != cam_id}
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)

    def roi(self, cam_id: str, w: int, h: int, margin: float = 0.08):
        """Pixel box around all desks of a camera (plus margin), or None when no desks are drawn."""
        pts = [p for d in self.desks(cam_id) for p in d["points"]]
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, x1 = max(0.0, min(xs) - margin), min(1.0, max(xs) + margin)
        y0, y1 = max(0.0, min(ys) - margin), min(1.0, max(ys) + margin)
        return int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)

    def _desk_masks(self, cam_id: str, w: int, h: int):
        key = (cam_id, w, h)
        with self._lock:
            if key not in self._masks:
                masks = []
                for d in self._data.get(cam_id, {}).get("desks", []):
                    m = np.zeros((h, w), np.uint8)
                    pts = np.array([[x * w, y * h] for x, y in d["points"]], np.int32)
                    cv2.fillPoly(m, [pts], 1)
                    masks.append((d["id"], m))
                self._masks[key] = masks
            return self._masks[key]

    def occupancy(self, cam_id: str, w: int, h: int, boxes, params) -> dict[str, tuple[bool, float, float]]:
        """Returns desk_id -> (occupied, score, overlap).

        Each detected person is assigned to the one desk their box overlaps most. A desk is occupied when an
        assigned person meets that desk's own thresholds (params(desk_id) -> confidence, min_overlap).
        score/overlap are those of the assigned person closest to meeting the thresholds (0, 0 if nobody).
        """
        masks = self._desk_masks(cam_id, w, h)
        assigned: dict[str, list[tuple[float, float]]] = {desk_id: [] for desk_id, _ in masks}
        for x1, y1, x2, y2, score in boxes:
            area = max((x2 - x1) * (y2 - y1), 1)
            best, best_ratio = None, 0.0
            for desk_id, m in masks:
                ratio = np.count_nonzero(m[y1:y2, x1:x2]) / area
                if ratio > best_ratio:
                    best, best_ratio = desk_id, ratio
            if best is not None:
                assigned[best].append((score, best_ratio))

        result = {}
        for desk_id, people in assigned.items():
            p = params(desk_id)
            best, best_fit = (0.0, 0.0), 0.0
            for score, ratio in people:
                fit = min(score / p["confidence"], ratio / p["min_overlap"])
                if fit > best_fit:
                    best, best_fit = (score, ratio), fit
            result[desk_id] = (best_fit >= 1.0, best[0], best[1])
        return result

    def draw(self, cam_id: str, img, boxes, occupied: dict[str, bool], show_conf: float = 0.0):
        h, w = img.shape[:2]
        out = img.copy()
        overlay = img.copy()
        for d in self.desks(cam_id):
            pts = np.array([[x * w, y * h] for x, y in d["points"]], np.int32)
            color = (60, 200, 60) if occupied.get(d["id"]) else (160, 160, 160)
            cv2.fillPoly(overlay, [pts], color)
            cv2.polylines(out, [pts], True, color, 2)
            label = d["employee"] or d["name"] or d["id"]
            x, y = pts.min(axis=0)
            cv2.putText(out, label, (int(x) + 4, int(y) + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
            cv2.putText(out, label, (int(x) + 4, int(y) + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        out = cv2.addWeighted(overlay, 0.25, out, 0.75, 0)
        for x1, y1, x2, y2, score in boxes:
            # weak detections (only usable by desks tuned to a lower confidence) are drawn thin and grey
            color, thick = ((0, 140, 255), 2) if score >= show_conf else ((150, 150, 150), 1)
            cv2.rectangle(out, (x1, y1), (x2, y2), color, thick)
            cv2.putText(out, f"{score:.2f}", (x1, max(y1 - 4, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
        return out
