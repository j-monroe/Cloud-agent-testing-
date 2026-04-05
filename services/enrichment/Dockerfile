FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY shared/ /app/shared/
COPY services/enrichment/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/enrichment/app/ /app/app/
COPY config/ /app/config/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "app.main"]
