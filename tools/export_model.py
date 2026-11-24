"""Download YOLO11n and export it to ONNX for CPU inference. Usage: python export_model.py OUTPUT.onnx"""
import shutil
import sys
from pathlib import Path

from ultralytics import YOLO

out = Path(sys.argv[1] if len(sys.argv) > 1 else "models/yolo11n.onnx")
out.parent.mkdir(parents=True, exist_ok=True)
exported = YOLO("yolo11n.pt").export(format="onnx", imgsz=640, opset=12, simplify=False, dynamic=False)
shutil.move(exported, out)
print(f"model written to {out}")
