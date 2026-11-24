# Stage 1: export the detection model (heavy tools, thrown away after the build)
FROM python:3.12-slim AS model
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir ultralytics onnx
WORKDIR /build
COPY tools/export_model.py .
RUN python export_model.py /build/yolo11n.onnx

# Stage 2: small runtime image
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 DATA_DIR=/app/data CONFIG_PATH=/app/config.yaml MODEL_PATH=/app/models/yolo11n.onnx
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=model /build/yolo11n.onnx /app/models/yolo11n.onnx
COPY app /app/app
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
