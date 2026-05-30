FROM python:3.11-slim

# Install system dependencies for curl_cffi (needs libcurl and openssl)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libcurl4-openssl-dev \
        libssl-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/

# Create data and logs directories with proper permissions
RUN mkdir -p /app/data /app/logs && chmod 777 /app/data /app/logs

# Expose Flask port
EXPOSE 5050

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/api/health')" || exit 1

# Run the web server
CMD ["python", "-m", "src.web_server"]