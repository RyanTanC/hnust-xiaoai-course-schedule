FROM python:3.13-slim

LABEL maintainer="aischedule"
LABEL description="小爱课程表 HNUST 教务助手"

WORKDIR /app

# Install system deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium

# Copy application
COPY . .

# Create data directory
RUN mkdir -p /app/data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/api/config', timeout=3)" || exit 1

CMD ["waitress-serve", "--host=0.0.0.0", "--port=8080", "app:app"]
