FROM python:3.11-slim

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps from requirements.txt (includes psycopg2-binary)
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy app source
COPY . /app/

RUN chmod +x /app/manage.sh /app/start.sh /app/docker-push.sh 2>/dev/null || true

ENV WORKSPACE=/app
ENV MEMORY_DIR=/app/memory
ENV SESSIONS_DIR=/app/sessions
ENV S3_BUCKET=smart-memory
ENV PORT=8001

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["/app/start.sh"]
