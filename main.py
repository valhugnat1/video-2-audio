import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, HttpUrl

# Assuming the file above is saved as 'drive_video_converter.py'
import drive_video_converter
import os, io

app = FastAPI(
    title="Google Drive Video-to-MP3 Converter",
    description="An API to convert video files in Google Drive to MP3 format.",
)



class ConversionRequest(BaseModel):
    video_url: HttpUrl
@app.get("/", summary="Health Check", include_in_schema=False)
async def root():
    """A simple health check endpoint."""
    return {"message": "Converter API is running."}

from fastapi.responses import StreamingResponse

@app.post("/convert", summary="Convert Google Drive video to MP3")
async def convert_video(request: ConversionRequest):
    """
    Downloads a video from Google Drive, converts to MP3,
    and returns the audio file directly in the response.
    """
    print(f"Received request to convert: {request.video_url}")

    service = drive_video_converter.authenticate_google_drive()
    if not service:
        raise HTTPException(status_code=500, detail="Google Drive auth failed")

    video_id = drive_video_converter.extract_id_from_url(str(request.video_url))
    if not video_id:
        raise HTTPException(status_code=400, detail="Could not extract video ID from URL")

    mp4_file = None
    mp3_file = None
    try:
        mp4_file, original_name = drive_video_converter.download_file(service, video_id)
        if not mp4_file:
            raise HTTPException(status_code=500, detail="Download failed")

        mp3_file = drive_video_converter.convert_to_mp3(mp4_file, original_name)
        if not mp3_file:
            raise HTTPException(status_code=500, detail="Conversion failed")

        # Lire le MP3 en mémoire avant de cleanup
        with open(mp3_file, "rb") as f:
            mp3_bytes = f.read()

        safe_name = drive_video_converter.sanitize_filename(
            os.path.splitext(original_name)[0] + ".mp3"
        )

        return StreamingResponse(
            io.BytesIO(mp3_bytes),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}"'
            },
        )

    finally:
        drive_video_converter.cleanup_files(mp4_file, mp3_file)

if __name__ == "__main__":
    # This allows you to run locally for testing without Docker
    # Run with: python main.py
    uvicorn.run(app, host="0.0.0.0", port=8000)
