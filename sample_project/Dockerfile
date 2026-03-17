# Use Python 3.13 slim image
FROM python:3.13-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set work directory
WORKDIR /app

# Install Python dependencies
COPY sample_project/requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy django-sql-jobs source
COPY src /src

# Copy sample project files
COPY sample_project /app

# Add src to Python path
ENV PYTHONPATH=/src:$PYTHONPATH

# Create data directory for SQLite
RUN mkdir -p /app/data

# Make entrypoint script executable
COPY sample_project/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Expose port
EXPOSE 8855

# Run entrypoint script
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8855"]
