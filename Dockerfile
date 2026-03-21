FROM python:3.11-slim

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.29" \
    "anthropic>=0.25" \
    "boto3>=1.34" \
    "tiktoken>=0.7" \
    "python-dotenv>=1.0"

WORKDIR /app

# App dirs
RUN mkdir -p /app/data

# Copy app source
COPY . /app/

RUN chmod +x /app/manage.sh /app/docker-push.sh

ENV TOKEN_FLOW_DB=/app/data/token_flow.db
ENV WORKSPACE=/app
ENV MEMORY_DIR=/app/memory
ENV SESSIONS_DIR=/app/sessions
ENV S3_BUCKET=smart-memory
ENV TOKEN_FLOW_PORT=8001
ENV PORT=8001

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["python3", "/app/main.py"]
