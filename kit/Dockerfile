FROM python:3.12-slim
# OCR + PDF rasterizer for the upload pipeline
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
ENV DATA_DIR=/var/data
CMD ["sh","-c","uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
