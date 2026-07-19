FROM python:3.13-slim

# Install build dependencies for psycopg3
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy source from repo root
COPY pyproject.toml README.md /app/
COPY src/ /app/src/

# Install sqlery with postgres extras
RUN pip install --no-cache-dir "/app[all,postgres]"

# Copy Django project and scripts
COPY examples/run_modes_lab/django_project/ /app/django_project/
COPY examples/run_modes_lab/scripts/ /app/scripts/
COPY examples/run_modes_lab/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Create non-root user
RUN useradd --create-home --uid 1000 app && chown -R app:app /app

USER app
WORKDIR /app/django_project
ENTRYPOINT ["/app/docker-entrypoint.sh"]
