import os
import io
import logging

import uvicorn
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel, HttpUrl
from fastapi.responses import StreamingResponse

import drive_video_converter

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
)
logger = logging.getLogger("api")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Google Drive Video-to-MP3 Converter",
    description="Secure API to convert Google Drive video files to MP3.",
    docs_url=None,   # disable Swagger UI in production
    redoc_url=None,   # disable ReDoc in production
)

# ---------------------------------------------------------------------------
# Authentication dependency
# ---------------------------------------------------------------------------
API_SECRET = os.environ.get("API_SECRET")


async def verify_token(authorization: str = Header(..., alias="Authorization")):
    """
    Require a Bearer token that matches the API_SECRET env var.
    If API_SECRET is not set the service refuses to start serving requests.
    """
    if not API_SECRET:
        logger.error("API_SECRET env var is not configured — rejecting all requests")
        raise HTTPException(status_code=503, detail="Service not configured")

    # Expect "Bearer <token>"
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    if parts[1] != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class ConversionRequest(BaseModel):
    video_url: HttpUrl


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/", summary="Health Check", include_in_schema=False)
async def root():
    """Simple health check — does not require auth."""
    return {"status": "ok"}


@app.post(
    "/convert",
    summary="Convert a Google Drive video to MP3",
    dependencies=[Depends(verify_token)],
)
async def convert_video(request: ConversionRequest):
    """
    Downloads a video from Google Drive, converts it to MP3,
    and streams the audio file back in the response.
    """
    video_url = str(request.video_url)
    logger.info("Conversion request received")

    # Run the full pipeline; work_dir is always cleaned up in `finally`.
    work_dir = None
    try:
        success, result, work_dir = drive_video_converter.main_process(
            video_url=video_url,
            folder_url=None,  # no upload — we stream the result back
        )

        if not success:
            # Return a generic message; details are in server logs
            raise HTTPException(status_code=500, detail="Conversion pipeline failed")

        mp3_path = result.get("mp3_path")
        if not mp3_path or not os.path.isfile(mp3_path):
            raise HTTPException(status_code=500, detail="Conversion pipeline failed")

        # Read into memory so we can clean up the temp dir immediately after
        with open(mp3_path, "rb") as f:
            mp3_bytes = f.read()

        safe_name = drive_video_converter.sanitize_filename(
            os.path.splitext(os.path.basename(mp3_path))[0] + ".mp3"
        )

        logger.info("Streaming back %d bytes as '%s'", len(mp3_bytes), safe_name)

        return StreamingResponse(
            io.BytesIO(mp3_bytes),
            media_type="audio/mpeg",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
        )

    except HTTPException:
        raise  # re-raise known HTTP errors as-is
    except Exception:
        logger.exception("Unhandled error in /convert")
        # Generic message — never leak internals
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        drive_video_converter.cleanup_work_dir(work_dir)


# ---------------------------------------------------------------------------
# Local dev entry-point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)