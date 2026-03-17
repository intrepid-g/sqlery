FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Build dependencies for psycopg
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install third-party deps (rq, redis, psycopg)
COPY stress_test/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Install sqlery from source with standalone + postgres extras
RUN pip install --no-cache-dir hatchling
COPY pyproject.toml README.md /sqlery_pkg/
COPY src /sqlery_pkg/src
RUN pip install --no-cache-dir "/sqlery_pkg[standalone,postgres]"

# Copy stress test application code
COPY stress_test/ /app/

ENV PYTHONPATH=/app
