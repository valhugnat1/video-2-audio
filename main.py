import os
import io
import logging
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel, HttpUrl, Field
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
    description="Multi-tenant API to convert Google Drive video files to MP3.",
    docs_url=None,
    redoc_url=None,
)

# ---------------------------------------------------------------------------
# Auth (shared bearer to gate the service itself)
# ---------------------------------------------------------------------------
API_SECRET = os.environ.get("API_SECRET")


async def verify_token(authorization: str = Header(..., alias="Authorization")):
    if not API_SECRET:
        logger.error("API_SECRET env var is not configured — rejecting all requests")
        raise HTTPException(status_code=503, detail="Service not configured")

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    if parts[1] != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class ConversionRequest(BaseModel):
    """
    The caller provides the Google Service Account JSON in `google_credentials`,
    so the same image can serve any number of tenants without server-side storage.
    """
    video_url: HttpUrl
    google_credentials: dict[str, Any] = Field(
        ...,
        description="Parsed Service Account JSON (the full key contents).",
    )

    # Pydantic v2: prevent accidental logging of the model
    def __repr__(self) -> str:
        return f"ConversionRequest(video_url={self.video_url}, google_credentials=<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/", summary="Health Check", include_in_schema=False)
async def root():
    return {"status": "ok"}


@app.post(
    "/convert",
    summary="Convert a Google Drive video to MP3",
    dependencies=[Depends(verify_token)],
)
async def convert_video(request: ConversionRequest):
    video_url = str(request.video_url)
    logger.info("Conversion request received")  # never log the body

    work_dir = None
    try:
        success, result, work_dir = drive_video_converter.main_process(
            video_url=video_url,
            credentials=request.google_credentials,
            folder_url=None,
        )

        if not success:
            # Surface the safe message produced by main_process
            # (e.g. "Invalid Google credentials.", "Download failed.", etc.)
            raise HTTPException(status_code=400, detail=result.get("message", "Failed"))

        mp3_path = result.get("mp3_path")
        if not mp3_path or not os.path.isfile(mp3_path):
            raise HTTPException(status_code=500, detail="Conversion pipeline failed")

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
        raise
    except Exception:
        # Generic message to client; full trace stays server-side.
        # NOTE: avoid logging the request object — it would leak the SA key.
        logger.exception("Unhandled error in /convert")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        drive_video_converter.cleanup_work_dir(work_dir)


# ---------------------------------------------------------------------------
# Local dev entry-point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)