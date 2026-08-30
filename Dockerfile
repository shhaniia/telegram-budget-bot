FROM python:3.11-slim

# tesseract-ocr is required by pytesseract for reading receipt/screenshot photos.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite database lives here — mount a persistent volume at this path in
# production (see README) so data survives redeploys/restarts.
RUN mkdir -p /data
ENV DATABASE_PATH=/data/budget.db

CMD ["python", "main.py"]
