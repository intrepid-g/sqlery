FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Build dependencies for psycopg
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install build tool first (layer cached independently of source)
RUN pip install --no-cache-dir hatchling

# Install sqlery from source with all extras + dev deps
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --no-cache-dir "/app[all,dev]"

# Copy test suite and project config (for pytest.ini_options)
COPY tests /app/tests
COPY pyproject.toml /app/pyproject.toml

CMD ["pytest", "tests/", "-v", "--tb=short"]
