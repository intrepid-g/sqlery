FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/standalone_project

WORKDIR /app

# Build dependencies for psycopg
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install sqlery from source with standalone and postgres extras
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --no-cache-dir "/app[standalone,postgres]"

# Copy standalone project
COPY examples/run_modes_lab/standalone_project /app/standalone_project

# Create non-root user
RUN useradd --create-home --uid 1000 app && \
    chown -R app:app /app

USER app

WORKDIR /app/standalone_project
