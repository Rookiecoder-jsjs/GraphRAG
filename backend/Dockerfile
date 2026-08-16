# Build from the REPOSITORY ROOT:  docker build -f backend/Dockerfile .
# (requirements.txt is at the repo root, app code under backend/app — no
# single backend-only context can satisfy both.)
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download spacy model
RUN python -m spacy download zh_core_web_sm || true

# Copy application code
COPY backend/app/ ./app/

# Create data directories
RUN mkdir -p /app/data/sqlite /app/data/uploads

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
