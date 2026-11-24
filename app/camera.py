"""Background RTSP readers. Each camera keeps only its latest decoded frame in memory."""
import logging
import os
import threading
import time

# TCP is far more reliable than UDP for NVR streams; 5 s socket timeout so dead streams reconnect.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|timeout;5000000")

import cv2  # noqa: E402

log = logging.getLogger("camera")

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


class Camera:
    def __init__(self, cam_id: str, name: str, url: str, decode_every: float):
        self.id = cam_id
        self.name = name
        self._url = url  # contains credentials: never log it
        self._decode_every = decode_every
        self._lock = threading.Lock()
        self._frame = None
        self._frame_ts = 0.0
        self._stop = threading.Event()
        self.error: str | None = "connecting"

    def start(self) -> None:
        threading.Thread(target=self._run, name=f"cam-{self.id}", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def online(self) -> bool:
        return self._frame is not None and time.time() - self._frame_ts < max(30, self._decode_every * 4)

    @property
    def frame_age(self) -> float | None:
        return None if self._frame is None else time.time() - self._frame_ts

    def latest(self, max_age: float):
        with self._lock:
            if self._frame is None or time.time() - self._frame_ts > max_age:
                return None
            return self._frame.copy()

    def _set(self, frame) -> None:
        with self._lock:
            self._frame = frame
            self._frame_ts = time.time()
        self.error = None

    def _run(self) -> None:
        if self._url.lower().endswith(IMAGE_EXTS):  # test mode: static image
            while not self._stop.is_set():
                img = cv2.imread(self._url)
                if img is None:
                    self.error = "image not found"
                else:
                    self._set(img)
                self._stop.wait(self._decode_every)
            return

        backoff = 2
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                self.error = "cannot open stream (check NVR address, channel, user/password)"
                log.warning("[%s] %s; retrying in %ss", self.id, self.error, backoff)
                cap.release()
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)
                continue
            log.info("[%s] stream connected", self.id)
            backoff = 2
            last_decode = 0.0
            while not self._stop.is_set():
                # grab() keeps the stream flowing; the costlier retrieve() runs only once per interval.
                if not cap.grab():
                    self.error = "stream lost"
                    log.warning("[%s] stream lost; reconnecting", self.id)
                    break
                now = time.time()
                if now - last_decode >= self._decode_every:
                    ok, frame = cap.retrieve()
                    if ok:
                        self._set(frame)
                        last_decode = now
            cap.release()
            self._stop.wait(2)
