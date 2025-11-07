import os
import re
import json
import io
from pydub import AudioSegment
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# If modifying these scopes, delete token.json.
SCOPES = ["https://www.googleapis.com/auth/drive"]


def authenticate_google_drive():
    """
    Handles Google Drive authentication.
    Looks for token.json, and if not present or invalid,
    runs the OAuth 2.0 flow using credentials.json.
    Returns the authenticated service object.
    """
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Error refreshing token: {e}")
                print("Deleting old token.json and re-authenticating...")
                os.remove("token.json")
                return authenticate_google_drive()  # Retry auth
        else:
            if not os.path.exists("credentials.json"):
                print("Error: credentials.json not found.")
                print(
                    "Please follow the README.md to set up your Google Cloud credentials."
                )
                return None
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        service = build("drive", "v3", credentials=creds)
        return service
    except HttpError as error:
        print(f"An error occurred building the service: {error}")
        return None


def extract_id_from_url(url):
    """
    Uses regular expressions to extract the Google Drive
    file/folder ID from various URL formats.
    """
    # Regex to find the ID in common Google Drive URL patterns
    # e.g., /file/d/FILE_ID/edit
    # e.g., /drive/folders/FOLDER_ID
    # e.g., ?id=FILE_ID
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"/drive/folders/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    print(f"Warning: Could not extract ID from URL: {url}")
    return None


def download_file(service, file_id):
    """
    D    downloads a file from Google Drive given its ID.
        Returns the local filepath and the original filename.
    """
    try:
        # Get file metadata to find the name
        file_metadata = service.files().get(fileId=file_id, fields="name").execute()
        original_filename = file_metadata.get("name")

        if not original_filename:
            print("Could not get file metadata. Aborting.")
            return None, None

        print(f"Starting download for: '{original_filename}'...")

        request = service.files().get_media(fileId=file_id)

        # Use a memory buffer to hold the downloaded file
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while done is False:
            status, done = downloader.next_chunk()
            print(f"Download {int(status.progress() * 100)}%.")

        # Save the downloaded file locally
        local_filename = f"temp_video_{file_id}.mp4"
        with open(local_filename, "wb") as f:
            f.write(fh.getvalue())

        print(f"Successfully downloaded to: {local_filename}")
        return local_filename, original_filename

    except HttpError as error:
        print(f"An error occurred during download: {error}")
        return None, None
    except Exception as e:
        print(f"An unexpected error occurred during download: {e}")
        return None, None


def sanitize_filename(filename):
    """
    Removes invalid characters from a filename
    to make it safe for saving locally.
    """
    # Replace non-breaking space with regular space
    filename = filename.replace("\xa0", " ")

    # Define a set of invalid characters
    # (slashes, colons, and others common on Windows/Mac/Linux)
    invalid_chars = r'[\\/:*?"<>|]'

    # Replace all invalid characters with an underscore
    safe_filename = re.sub(invalid_chars, "_", filename)

    # You might also want to strip leading/trailing whitespace
    safe_filename = safe_filename.strip()

    # Ensure filename isn't empty after sanitizing
    if not safe_filename:
        safe_filename = "unnamed_file"

    return safe_filename


def convert_to_mp3(mp4_filepath, original_filename):
    """
    Converts the downloaded MP4 file to MP3 using pydub.
    Returns the path to the new MP3 file.
    """
    if not mp4_filepath:
        return None

    # Sanitize the original filename to make it safe for the local filesystem
    safe_original_filename = sanitize_filename(original_filename)

    # Create a new filename for the mp3, e.g., "My Video.mp4" -> "My Video.mp3"
    base_filename = os.path.splitext(safe_original_filename)[0]
    mp3_filename = f"{base_filename}.mp3"

    print(f"Converting '{mp4_filepath}' to '{mp3_filename}'...")

    try:
        # Load the video file (pydub can read mp4)
        audio = AudioSegment.from_file(mp4_filepath, format="mp4")

        # --- START: MODIFICATIONS FOR SMALLER FILE ---

        # 1. (Optional) Convert to Mono
        # If the audio is stereo, this will cut the file size in half.
        # Great for speech, podcasts, or lectures.
        # Uncomment the line below to enable it.
        # audio = audio.set_channels(1)

        # 2. Set the Bitrate
        # This is the primary way to control file size vs. quality.
        # "128k" = Good quality, standard for music.
        # "64k"  = Good for speech-only content, significantly smaller.
        # "32k"  = Very small, but may have noticeable quality loss.
        output_bitrate = "32k"

        # Export as MP3 with the specified bitrate
        audio.export(mp3_filename, format="mp3", bitrate=output_bitrate)

        # --- END: MODIFICATIONS ---

        print(f"Successfully converted to: {mp3_filename} (Bitrate: {output_bitrate})")
        return mp3_filename

    except Exception as e:
        print(f"An error occurred during conversion: {e}")
        print(
            "Please make sure FFmpeg is installed and accessible in your system's PATH."
        )
        return None


def upload_to_folder(service, mp3_filepath, folder_id):
    """
    Uploads the generated MP3 file to a specific Google Drive folder.
    """
    if not mp3_filepath:
        return

    try:
        file_metadata = {
            "name": os.path.basename(mp3_filepath),
            "parents": [folder_id],  # Specify the folder to upload into
        }

        media = MediaFileUpload(mp3_filepath, mimetype="audio/mpeg")

        print(f"Uploading '{mp3_filepath}' to Google Drive...")

        file = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )

        print(f"Successfully uploaded. File ID: {file.get('id')}")

    except HttpError as error:
        print(f"An error occurred during upload: {error}")
    except Exception as e:
        print(f"An unexpected error occurred during upload: {e}")


def cleanup_files(*filepaths):
    """
    Deletes local temporary files.
    """
    for f in filepaths:
        if f and os.path.exists(f):
            try:
                os.remove(f)
                print(f"Cleaned up local file: {f}")
            except Exception as e:
                print(f"Warning: Could not delete {f}. Error: {e}")


def main_process_handler(event, context):
    """
    Serverless function handler to trigger the video conversion.
    Expects a JSON body with 'video_url' and 'folder_url'.
    """
    print("--- Serverless Handler Received Request ---")

    # 1. Parse the request body
    raw_body = event.get("body", "{}")
    try:
        body_dict = json.loads(raw_body)
    except json.JSONDecodeError:
        print("Error: Invalid JSON body format")
        return {
            "statusCode": 400,
            "body": json.dumps({"message": "Invalid JSON body format"}),
        }

    # 2. Get required parameters from the body
    video_url = body_dict.get("video_url")
    folder_url = body_dict.get("folder_url")

    if not video_url or not folder_url:
        print("Error: 'video_url' and 'folder_url' are required.")
        return {
            "statusCode": 400,
            "body": json.dumps(
                {"message": "'video_url' and 'folder_url' are required in the body."}
            ),
        }

    # 3. Call the main business logic
    try:
        success, message = main_process(video_url, folder_url)

        if success:
            print(f"Success: {message}")
            return {"statusCode": 200, "body": json.dumps({"message": message})}
        else:
            print(f"Failure: {message}")
            # 500 status code indicates a server-side processing error
            return {"statusCode": 500, "body": json.dumps({"message": message})}
    except Exception as e:
        # Catch-all for any unhandled exceptions
        print(f"Critical handler error: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {"message": f"An unexpected server error occurred: {str(e)}"}
            ),
        }


def main_process(video_url, folder_url):
    """
    The main function that orchestrates the entire process.
    Returns a tuple: (bool_success, message_string)
    """
    # 1. Authenticate
    print("Authenticating with Google Drive...")
    service = authenticate_google_drive()
    if not service:
        print("Failed to authenticate. Exiting.")
        return (False, "Failed to authenticate with Google Drive.")

    # 2. Extract IDs
    video_id = extract_id_from_url(video_url)
    folder_id = extract_id_from_url(folder_url)

    if not video_id or not folder_id:
        print("Could not extract valid ID from one or both URLs. Exiting.")
        return (False, "Could not extract valid ID from one or both URLs.")

    print(f"Video ID: {video_id}")
    print(f"Folder ID: {folder_id}")

    # 3. Download, Convert, Upload
    mp4_file = None
    mp3_file = None
    try:
        mp4_file, original_name = download_file(service, video_id)
        if not mp4_file:
            return (False, "Download failed.")  # Download failed

        # 4. Convert
        mp3_file = convert_to_mp3(mp4_file, original_name)
        if not mp3_file:
            return (False, "Conversion failed.")  # Conversion failed

        # 5. Upload
        upload_to_folder(service, mp3_file, folder_id)

        # If we get here, all steps were successful
        message = f"Successfully processed and uploaded: {mp3_file}"
        print(message)
        return (True, message)

    except Exception as e:
        # Catch any other unexpected errors during the process
        error_message = f"An unexpected error occurred: {e}"
        print(error_message)
        return (False, error_message)

    finally:
        # 6. Cleanup
        print("Cleaning up local files...")
        cleanup_files(mp4_file, mp3_file)
        print("Cleanup complete.")


if __name__ == "__main__":
    print("--- Google Drive Video to MP3 Converter (Serverless Test) ---")
    print("NOTE: You must follow the README.md setup instructions first.\n")

    # --- Define the test data ---
    video_url = "https://drive.google.com/file/d/17IlHTmWUGf3yOAlzO4Nnx7ANX3EjQSX4/view?usp=sharing"
    folder_url = "https://drive.google.com/drive/folders/17We1iX19Osse1tSX3JIg3DicwqKIUlmR?usp=sharing"

    if not video_url or not folder_url:
        print("Both URLs are required for the test.")
    else:
        # --- Create a mock 'event' similar to a serverless environment ---
        # The body is a JSON *string*, just as it would be from an API Gateway
        mock_event = {
            "body": json.dumps({"video_url": video_url, "folder_url": folder_url})
        }

        # 'context' is often not needed for simple handlers, so we pass None
        mock_context = None

        # --- Call the new handler function ---
        print("Starting handler test...")
        result = main_process_handler(mock_event, mock_context)

        print("\n--- Handler Result ---")
        print(json.dumps(result, indent=2))
        print("------------------------")
