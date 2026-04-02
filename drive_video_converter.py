import os
import re
import json
import io
import tempfile
import shutil
import logging
from pydub import AudioSegment
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2 import service_account


SCOPES = ["https://www.googleapis.com/auth/drive"]

MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB hard limit
ALLOWED_DRIVE_HOSTS = {"drive.google.com"}
OUTPUT_BITRATE = "32k"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("drive_converter")
logger.setLevel(logging.INFO)



# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def authenticate_google_drive():
    try:
        sa_json = os.environ.get("GOOGLE_SA_CREDENTIALS")
        if not sa_json:
            logger.error("GOOGLE_SA_CREDENTIALS env var not set")
            return None

        sa_info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=SCOPES
        )
        service = build("drive", "v3", credentials=creds)
        return service
    except Exception:
        logger.exception("Authentication error")
        return None


# ---------------------------------------------------------------------------
# URL validation & ID extraction
# ---------------------------------------------------------------------------
def validate_drive_url(url: str) -> bool:
    """
    Ensure the URL points to Google Drive and nothing else (SSRF prevention).
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.hostname not in ALLOWED_DRIVE_HOSTS:
        logger.warning("Rejected non-Drive URL: host=%s", parsed.hostname)
        return False
    if parsed.scheme not in ("https",):
        logger.warning("Rejected non-HTTPS URL: scheme=%s", parsed.scheme)
        return False
    return True


def extract_id_from_url(url: str) -> str | None:
    """
    Extract the Google Drive file/folder ID from various URL formats.
    Returns None if the URL is invalid or the ID cannot be found.
    """
    if not validate_drive_url(url):
        return None

    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"/drive/folders/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    logger.warning("Could not extract ID from URL")
    return None


# ---------------------------------------------------------------------------
# Filename sanitisation (path-traversal safe)
# ---------------------------------------------------------------------------
def sanitize_filename(filename: str) -> str:
    """
    Remove / neutralise dangerous characters and prevent path traversal.
    """
    # Replace non-breaking spaces
    filename = filename.replace("\xa0", " ")

    # Strip directory components first (path traversal prevention)
    filename = os.path.basename(filename)

    # Remove remaining invalid chars
    invalid_chars = r'[\\/:*?"<>|]'
    safe = re.sub(invalid_chars, "_", filename)

    # Remove leading dots (hidden files / traversal)
    safe = safe.lstrip(".")

    safe = safe.strip()
    if not safe:
        safe = "unnamed_file"

    return safe


# ---------------------------------------------------------------------------
# Download with size check
# ---------------------------------------------------------------------------
def download_file(service, file_id: str, work_dir: str):
    """
    Download a file from Google Drive into *work_dir*.
    Returns (local_path, original_filename) or (None, None).
    """
    try:
        file_metadata = (
            service.files()
            .get(fileId=file_id, fields="name, mimeType, size")
            .execute()
        )
        original_filename = file_metadata.get("name", "video")
        mime_type = file_metadata.get("mimeType", "")
        file_size = int(file_metadata.get("size", 0))

        logger.info(
            "File: '%s' (mimeType: %s, size: %d bytes)",
            original_filename,
            mime_type,
            file_size,
        )

        # --- Size guard ---
        if file_size > MAX_FILE_SIZE_BYTES:
            logger.error(
                "File too large: %d bytes (limit %d)", file_size, MAX_FILE_SIZE_BYTES
            )
            return None, None

        # --- Google-native files cannot be exported as video ---
        if mime_type.startswith("application/vnd.google-apps"):
            logger.error("Google-native file cannot be exported as video")
            return None, None

        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()
            logger.info("Download %d%%", int(status.progress() * 100))

        # Write to isolated work directory with a fixed name (no user input)
        local_filename = os.path.join(work_dir, "input.mp4")
        with open(local_filename, "wb") as f:
            f.write(fh.getvalue())

        return local_filename, original_filename

    except HttpError:
        logger.exception("Google Drive API error during download")
        return None, None
    except Exception:
        logger.exception("Unexpected error during download")
        return None, None


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def convert_to_mp3(mp4_filepath: str, original_filename: str, work_dir: str):
    """
    Convert an MP4 file to MP3. Output is written inside *work_dir*.
    Returns the path to the MP3 file or None.
    """
    if not mp4_filepath:
        return None

    safe_original_filename = sanitize_filename(original_filename)
    base_filename = os.path.splitext(safe_original_filename)[0]
    mp3_filename = os.path.join(work_dir, f"{base_filename}.mp3")

    logger.info("Converting '%s' → '%s'", mp4_filepath, mp3_filename)

    try:
        audio = AudioSegment.from_file(mp4_filepath, format="mp4")
        audio.export(mp3_filename, format="mp3", bitrate=OUTPUT_BITRATE)

        logger.info("Conversion OK: %s (bitrate: %s)", mp3_filename, OUTPUT_BITRATE)
        return mp3_filename

    except Exception:
        logger.exception("Conversion error")
        return None


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
def upload_to_folder(service, mp3_filepath: str, folder_id: str):
    """
    Upload MP3 to a Google Drive folder.
    Returns (file_id, web_view_link) or (None, None).
    """
    if not mp3_filepath:
        return None, None

    try:
        file_metadata = {
            "name": os.path.basename(mp3_filepath),
            "parents": [folder_id],
        }
        media = MediaFileUpload(mp3_filepath, mimetype="audio/mpeg")

        logger.info("Uploading '%s' to Drive folder %s", mp3_filepath, folder_id)

        uploaded = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, webViewLink")
            .execute()
        )

        file_id = uploaded.get("id")
        web_link = uploaded.get("webViewLink")
        logger.info("Upload OK — file ID: %s", file_id)
        return file_id, web_link

    except HttpError:
        logger.exception("Google Drive API error during upload")
        return None, None
    except Exception:
        logger.exception("Unexpected error during upload")
        return None, None


# ---------------------------------------------------------------------------
# Safe temp-dir cleanup helper
# ---------------------------------------------------------------------------
def cleanup_work_dir(work_dir: str | None):
    """Remove the entire temporary working directory."""
    if work_dir and os.path.isdir(work_dir):
        try:
            shutil.rmtree(work_dir)
            logger.info("Cleaned up work dir: %s", work_dir)
        except Exception:
            logger.exception("Could not clean up work dir %s", work_dir)


# ---------------------------------------------------------------------------
# Main orchestration (used by both FastAPI and serverless handler)
# ---------------------------------------------------------------------------
def main_process(video_url: str, folder_url: str | None = None):
    """
    Orchestrate download → convert → (optional upload).
    Returns (success: bool, result: dict, work_dir: str).
    The caller is responsible for calling cleanup_work_dir(work_dir).
    """
    work_dir = tempfile.mkdtemp(prefix="drive_conv_")
    logger.info("Work directory: %s", work_dir)

    # --- Auth ---
    service = authenticate_google_drive()
    if not service:
        return False, {"message": "Google Drive authentication failed."}, work_dir

    # --- Extract IDs ---
    video_id = extract_id_from_url(video_url)
    if not video_id:
        return False, {"message": "Invalid or disallowed video URL."}, work_dir

    folder_id = None
    if folder_url:
        folder_id = extract_id_from_url(folder_url)
        if not folder_id:
            return False, {"message": "Invalid or disallowed folder URL."}, work_dir

    # --- Download ---
    mp4_file, original_name = download_file(service, video_id, work_dir)
    if not mp4_file:
        return False, {"message": "Download failed."}, work_dir

    # --- Convert ---
    mp3_file = convert_to_mp3(mp4_file, original_name, work_dir)
    if not mp3_file:
        return False, {"message": "Conversion failed."}, work_dir

    result_data = {
        "message": f"Successfully converted: {sanitize_filename(original_name)}",
        "mp3_path": mp3_file,
    }

    # --- Optional upload ---
    if folder_id:
        new_file_id, new_file_url = upload_to_folder(service, mp3_file, folder_id)
        if not new_file_id:
            return False, {"message": "Upload failed."}, work_dir
        result_data["file_id"] = new_file_id
        result_data["file_url"] = new_file_url

    return True, result_data, work_dir