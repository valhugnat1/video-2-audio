# 1. Base Image
FROM python:3.10-slim

# 2. Install system dependencies (FFmpeg for pydub)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. Non-root user for defense in depth
RUN useradd --create-home --shell /bin/bash appuser

# 4. Working directory
WORKDIR /app

# 5. Install Python dependencies (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy application code
COPY . .

# 7. Own the app dir, then drop privileges
RUN chown -R appuser:appuser /app
USER appuser

# 8. Expose the port uvicorn will listen on
EXPOSE 8080

# 9. Start the server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]