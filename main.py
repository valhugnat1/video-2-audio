import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, HttpUrl

# Assuming the file above is saved as 'drive_video_converter.py'
import drive_video_converter

app = FastAPI(
    title="Google Drive Video-to-MP3 Converter",
    description="An API to convert video files in Google Drive to MP3 format.",
)


class ConversionRequest(BaseModel):
    """Defines the expected JSON body for the conversion request."""

    # Using HttpUrl provides basic validation that the input is a URL
    video_url: HttpUrl
    folder_url: HttpUrl


@app.get("/", summary="Health Check", include_in_schema=False)
async def root():
    """A simple health check endpoint."""
    return {"message": "Converter API is running."}


@app.post("/convert", summary="Start Video to MP3 Conversion")
async def convert_video(request: ConversionRequest):
    """
    Takes a Google Drive video URL and a target folder URL.

    The server will then:
    1. Download the video.
    2. Convert it to a low-bitrate MP3.
    3. Upload the MP3 to the specified folder.

    Returns the new file's ID and URL on success.
    """
    print(f"Received request to convert: {request.video_url}")
    try:
        success, result_data = drive_video_converter.main_process(
            str(request.video_url), str(request.folder_url)
        )

        if success:
            return {"status": "success", **result_data}
        else:
            error_message = result_data.get("message", "Unknown server error")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message
            )

    except Exception as e:
        # Catch-all for any other unexpected errors in the API layer
        print(f"Critical API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected API error occurred: {str(e)}",
        )


if __name__ == "__main__":
    # This allows you to run locally for testing without Docker
    # Run with: python main.py
    uvicorn.run(app, host="0.0.0.0", port=8000)
