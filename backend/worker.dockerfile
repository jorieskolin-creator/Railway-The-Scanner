
FROM python:3.11-slim

WORKDIR /app

# Prevent Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Install dependencies first (caching layer)
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# COPY THE ENTIRE PROJECT (Frontend + Backend)
COPY . /app

# Switch to backend directory to run the server
WORKDIR /app/backend

# Run FastAPI using the PORT environment variable (defaulting to 8080)
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"
