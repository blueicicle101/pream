# ── Pream Container Image ──
FROM python:3.12-slim

# Install system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app/ ./app/

# Create persistent data directory
RUN mkdir -p /app/data

# Environment defaults
ENV HOST=0.0.0.0 \
    PORT=8000 \
    SOURCE_TIMEOUT=60.0 \
    DATABASE_URL=sqlite:///./data/pream.db

EXPOSE 8000

# Docker healthcheck querying /health (respects dynamic PORT env var)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Launch FastAPI with uvicorn (exec ensures PID 1 receives SIGTERM/SIGINT)
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000}"]
