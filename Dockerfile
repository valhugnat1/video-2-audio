# 1. Base Image
# Use an official Python runtime as a parent image
FROM python:3.10-slim

# 2. Install System Dependencies
# FFmpeg is a critical system dependency required by pydub to read MP4s
# and write MP3s.
RUN apt-get update && apt-get install -y ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. Set Working Directory
WORKDIR /app

# 4. Install Python Dependencies
# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy Application Code and Credentials
# This copies all files from your project folder into the /app directory
# in the container.
COPY . .

# 6. Expose Port
# Tell Docker the container listens on port 8000
EXPOSE 8000

# 7. Run the Application
# This is the command to start the FastAPI server when the container launches
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]