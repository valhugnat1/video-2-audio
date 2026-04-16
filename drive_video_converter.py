import os
import re
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

# Required fields in a valid Service Account JSON
REQUIRED_SA_FIELDS = {
    "type",
    "project_id",
    "private_key_id",
    "private_key",
    "client_email",
    "token_uri",
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("drive_converter")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Credentials validation
# ---------------------------------------------------------------------------
class InvalidCredentialsError(Exception):
    """Raised when the provided SA credentials are malformed."""


def validate_sa_credentials(sa_info: dict) -> None:
    """
    Validate the structure of a Service Account JSON before handing it to
    Google's library. Raises InvalidCredentialsError on failure.

    IMPORTANT: never log the content of sa_info — it contains a private key.
    """
    if not isinstance(sa_info, dict):
        raise InvalidCredentialsError("credentials must be a JSON object")

    missing = REQUIRED_SA_FIELDS - set(sa_info.keys())
    if missing:
        raise InvalidCredentialsError(
            f"credentials missing required fields: {sorted(missing)}"
        )

    if sa_info.get("type") != "service_account":
        raise InvalidCredentialsError("credentials type must be 'service_account'")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def authenticate_google_drive(sa_info: dict):
    """
    Build a Drive client from a Service Account JSON dict.
    Returns the service object, or None on failure.
    """
    try:
        validate_sa_credentials(sa_info)
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=SCOPES
        )
        return build("drive", "v3", credentials=creds)
    except InvalidCredentialsError as e:
        # Safe to log: only mentions which fields were missing, never values
        logger.warning("Invalid SA credentials: %s", e)
        return None
    except Exception:
        # Do NOT use logger.exception here — Google's traceback may include
        # parts of the malformed key. Log a generic message only.
        logger.error("Failed to build Drive client from provided credentials")
        return None


# ---------------------------------------------------------------------------
# URL validation & ID extraction
# ---------------------------------------------------------------------------
def validate_drive_url(url: str) -> bool:
    """Ensure the URL points to Google Drive (SSRF prevention)."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.hostname not in ALLOWED_DRIVE_HOSTS:
        logger.warning("Rejected non-Drive URL: host=%s", parsed.hostname)
        return False
    if parsed.scheme != "https":
        logger.warning("Rejected non-HTTPS URL: scheme=%s", parsed.scheme)
        return False
    return True


def extract_id_from_url(url: str) -> str | None:
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
# Filename sanitisation
# ---------------------------------------------------------------------------
def sanitize_filename(filename: str) -> str:
    filename = filename.replace("\xa0", " ")
    filename = os.path.basename(filename)
    invalid_chars = r'[\\/:*?"<>|]'
    safe = re.sub(invalid_chars, "_", filename)
    safe = safe.lstrip(".").strip()
    return safe or "unnamed_file"


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def download_file(service, file_id: str, work_dir: str):
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
            original_filename, mime_type, file_size,
        )

        if file_size > MAX_FILE_SIZE_BYTES:
            logger.error("File too large: %d bytes (limit %d)", file_size, MAX_FILE_SIZE_BYTES)
            return None, None

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

        local_filename = os.path.join(work_dir, "input.mp4")
        with open(local_filename, "wb") as f:
            f.write(fh.getvalue())

        return local_filename, original_filename

    except HttpError as e:
        # Log status only, not full response (could leak token info in headers)
        logger.error("Google Drive API error during download: status=%s", e.resp.status)
        return None, None
    except Exception:
        logger.exception("Unexpected error during download")
        return None, None


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def convert_to_mp3(mp4_filepath: str, original_filename: str, work_dir: str):
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

    except HttpError as e:
        logger.error("Google Drive API error during upload: status=%s", e.resp.status)
        return None, None
    except Exception:
        logger.exception("Unexpected error during upload")
        return None, None


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
def cleanup_work_dir(work_dir: str | None):
    if work_dir and os.path.isdir(work_dir):
        try:
            shutil.rmtree(work_dir)
            logger.info("Cleaned up work dir: %s", work_dir)
        except Exception:
            logger.exception("Could not clean up work dir %s", work_dir)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main_process(
    video_url: str,
    credentials: dict,
    folder_url: str | None = None,
):
    """
    Orchestrate download → convert → (optional upload).
    `credentials` is the parsed Service Account JSON provided by the caller.
    Returns (success: bool, result: dict, work_dir: str).
    Caller is responsible for calling cleanup_work_dir(work_dir).
    """
    work_dir = tempfile.mkdtemp(prefix="drive_conv_")
    logger.info("Work directory: %s", work_dir)

    service = authenticate_google_drive(credentials)
    if not service:
        return False, {"message": "Invalid Google credentials."}, work_dir

    video_id = extract_id_from_url(video_url)
    if not video_id:
        return False, {"message": "Invalid or disallowed video URL."}, work_dir

    folder_id = None
    if folder_url:
        folder_id = extract_id_from_url(folder_url)
        if not folder_id:
            return False, {"message": "Invalid or disallowed folder URL."}, work_dir

    mp4_file, original_name = download_file(service, video_id, work_dir)
    if not mp4_file:
        return False, {"message": "Download failed."}, work_dir

    mp3_file = convert_to_mp3(mp4_file, original_name, work_dir)
    if not mp3_file:
        return False, {"message": "Conversion failed."}, work_dir

    result_data = {
        "message": f"Successfully converted: {sanitize_filename(original_name)}",
        "mp3_path": mp3_file,
    }

    if folder_id:
        new_file_id, new_file_url = upload_to_folder(service, mp3_file, folder_id)
        if not new_file_id:
            return False, {"message": "Upload failed."}, work_dir
        result_data["file_id"] = new_file_id
        result_data["file_url"] = new_file_url

    return True, result_data, work_dir