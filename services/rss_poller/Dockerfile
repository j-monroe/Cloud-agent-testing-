FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY shared/ /app/shared/
COPY services/rss_poller/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/rss_poller/app/ /app/app/
COPY config/ /app/config/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "app.main"]
