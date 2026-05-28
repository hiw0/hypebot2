FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Allow overriding connection details at build/run time without baking secrets
ARG REDIS_URL
ENV REDIS_URL=${REDIS_URL}

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Lightweight healthcheck to verify Redis connectivity using REDIS_URL
COPY docker/healthcheck.py /usr/local/bin/redis_healthcheck.py
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
  CMD python /usr/local/bin/redis_healthcheck.py || exit 1

# Default to running the trading bot; BOT_MODE is read from env
CMD ["python", "-m", "src.cli", "run"]
