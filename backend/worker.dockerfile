
FROM python:3.11-slim

WORKDIR /app

# Prevent Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# 1. Install system dependencies required for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. Install dependencies (Caching Layer)
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r backend/requirements.txt

# --- CACHE BUSTER ---
# Changing this value forces Docker to re-execute all commands below it.
# Updated to v3 to clear stuck Railway cache.
ENV FORCE_REFRESH_DATE=2024-05-21_v3_FORCE_DEPLOY

# 3. Copy Application Code
COPY . /app

# 4. Run Server
WORKDIR /app/backend
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"
