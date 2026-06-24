# market-edge-intraday — single image used by the worker, api and dashboard.
# Paper trading only: this image never connects a real broker or sends real orders.
FROM python:3.11-slim

# Build deps for psycopg / pyarrow wheels + curl for the api healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Application code.
COPY app/ ./app/
COPY dashboard/ ./dashboard/
COPY scripts/ ./scripts/

# Writable dirs for cache + generated reports.
RUN mkdir -p /app/data /app/reports

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Container-level healthcheck reuses the operational health script.
HEALTHCHECK --interval=60s --timeout=15s --start-period=30s --retries=3 \
    CMD python scripts/health_check.py || exit 1

# Default command runs the paper worker. Compose overrides this per service.
CMD ["python", "scripts/run_live_paper.py", "--yes"]
