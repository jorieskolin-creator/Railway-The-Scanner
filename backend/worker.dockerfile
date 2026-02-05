FROM python:3.11-slim

WORKDIR /app

# Prevent Python from buffering stdout and stderr (helps with logs)
ENV PYTHONUNBUFFERED=1
# Prevent Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE=1
# Ensure the app directory is in the python path
ENV PYTHONPATH=/app

# Adjusted for Root Build Context
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# Expose port 8080
EXPOSE 8080

# Run FastAPI with Uvicorn
# Explicitly invoke shell to ensure PORT environment variable is expanded correctly
CMD ["/bin/sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]