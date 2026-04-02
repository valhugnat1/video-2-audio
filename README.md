# drive-video-2-audio

REST API that downloads a video from Google Drive, converts it to MP3, and streams the audio file back in the HTTP response.

## How it works

1. The client sends a `POST /convert` request with a Google Drive video URL.
2. The server authenticates with Google Drive using a Service Account.
3. The video is downloaded into an isolated temporary directory.
4. `pydub` + `FFmpeg` extract and encode the audio track to MP3 (32 kbps by default).
5. The MP3 is streamed back in the response, then all temporary files are deleted.

## Project structure

```
├── Dockerfile
├── requirements.txt
├── main.py                      # FastAPI app (endpoints, auth, streaming)
└── drive_video_converter.py     # Business logic (download, conversion, upload)
```

## Prerequisites

- Docker (or Python 3.10+ with FFmpeg installed locally)
- A Google Cloud Service Account with the Google Drive API enabled
- The Service Account must have access (Viewer at minimum) to the files to be converted

### Creating the Service Account

1. Create a project on [console.cloud.google.com](https://console.cloud.google.com).
2. Enable the Google Drive API under **APIs & Services → Library**.
3. Create a service account under **APIs & Services → Credentials → Service Account**.
4. Generate a JSON key from the service account's **Keys** tab.
5. Share the target Google Drive folder/file with the `client_email` address from the JSON key (Viewer role, or Editor if the owner has restricted downloads).

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_SA_CREDENTIALS` | yes | Full JSON content of the Service Account key |
| `API_SECRET` | yes | Bearer token expected in the `Authorization` header |

## Running with Docker

```bash
# Build
docker build -t drive-video-2-audio .

# Run
docker run -d \
  --name converter \
  -p 8080:8080 \
  -e API_SECRET="my-secret" \
  -e GOOGLE_SA_CREDENTIALS="$(cat credentials.json)" \
  drive-video-2-audio
```

## Running without Docker

```bash
pip install -r requirements.txt
export API_SECRET="my-secret"
export GOOGLE_SA_CREDENTIALS="$(cat credentials.json)"
python main.py
```

The server listens on `http://localhost:8080`.

## Endpoints

### `GET /`

Health check. No authentication required.

```bash
curl http://localhost:8080/
# {"status":"ok"}
```

### `POST /convert`

Converts a Google Drive video to MP3 and returns the audio file.

**Required headers:**
- `Content-Type: application/json`
- `Authorization: Bearer <API_SECRET>`

**Body:**

```json
{
  "video_url": "https://drive.google.com/file/d/FILE_ID/view"
}
```

**Response:** MP3 file (`audio/mpeg`) with a `Content-Disposition` header containing the filename.

**Example:**

```bash
curl -X POST http://localhost:8080/convert \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer my-secret" \
  -d '{"video_url": "https://drive.google.com/file/d/XXXXX/view"}' \
  --output audio.mp3
```

## Security

- **Authentication** — Every `/convert` request requires a valid Bearer token.
- **URL validation** — Only `https://drive.google.com` URLs are accepted (SSRF protection).
- **File size limit** — Files larger than 2 GB are rejected before download (configurable via `MAX_FILE_SIZE_BYTES`).
- **File isolation** — Each conversion runs in a dedicated temporary directory (`tempfile.mkdtemp`), always cleaned up in a `finally` block.
- **Filename sanitization** — `sanitize_filename()` neutralizes path traversals (`..`, `/`) and special characters.
- **Opaque errors** — HTTP responses never contain internal details (stack traces, file paths). Detailed errors are logged server-side only.
- **Non-root user** — The Dockerfile runs the process under a dedicated `appuser` account.

## Conversion settings

In `drive_video_converter.py`:

| Constant | Default              | Description |
|---|----------------------|---|
| `OUTPUT_BITRATE` | `"32k"`              | Output MP3 bitrate. Use `"64k"` for better speech quality, `"128k"` for music. |
| `MAX_FILE_SIZE_BYTES` | `2 * 1024³` (2 GB) | Maximum allowed size for the source file. |

## n8n integration

Add an **HTTP Request** node:

- **Method**: `POST`
- **URL**: `http://<host>:8080/convert`
- **Authentication**: Header Auth → `Authorization: Bearer <API_SECRET>`
- **Body**: JSON → `{"video_url": "https://drive.google.com/file/d/XXXXX/view"}`
- **Response Format**: File

The returned binary MP3 file can then be passed to a Whisper node (transcription), Google Drive node (storage), or any node that accepts binary files.

## Troubleshooting

| Symptom | Likely cause | Solution |
|---|---|---|
| `401 Unauthorized` | Missing or incorrect Bearer token | Check the `Authorization` header and the `API_SECRET` variable |
| `503 Service not configured` | `API_SECRET` not set on the server | Add the environment variable |
| `500 Conversion pipeline failed` | Google Drive or FFmpeg error | Check container logs (`docker logs converter`) |
| `403` in logs | Download restricted by the file owner | Grant the Service Account the Editor role on the file/folder |
| Timeout | Video too long for the configured timeout | Increase the container or reverse proxy timeout |