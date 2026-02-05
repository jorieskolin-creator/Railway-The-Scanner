FROM python:3.10-slim

WORKDIR /app

# Adjusted for Root Build Context
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# Expose port 8080
EXPOSE 8080

# Run FastAPI with Uvicorn
# Listens on $PORT if available (Cloud/Railway), otherwise defaults to 8080
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}