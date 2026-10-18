FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus poppler-utils && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY start.sh .
RUN useradd --system --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /app /data && chmod +x /app/start.sh
USER appuser
EXPOSE 8000
CMD ["sh", "/app/start.sh"]
