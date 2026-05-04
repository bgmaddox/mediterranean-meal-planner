"""
drive_upload.py
---------------
Upload the weekly meal plan PDF to Google Drive via the Drive API (OAuth2
installed-app flow — correct for a local, single-user app).

First run:  a browser window opens for Google consent → token saved to
            data/drive_token.json automatically.
Later runs: token loaded from disk and auto-refreshed when expired.

One-time setup (see Settings tab in the app for a step-by-step guide):
  1. Go to console.cloud.google.com → New project
  2. Enable the Drive API
  3. Create OAuth 2.0 credentials (type: Desktop application)
  4. Download the JSON and save as  data/client_secrets.json
  5. Click "Upload to Drive" in the app — browser opens for consent
"""

import json
import io
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_DATA = Path(__file__).parent / "data"
TOKEN_PATH = _DATA / "drive_token.json"
SECRETS_PATH = _DATA / "client_secrets.json"
_FOLDER_CACHE = _DATA / "drive_folder_id.json"
FOLDER_NAME = "Mediterranean Meal Plans"


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def credentials_configured() -> bool:
    """Return True if client_secrets.json exists — Drive upload is ready to try."""
    return SECRETS_PATH.exists()


def upload_pdf_to_drive(pdf_bytes: bytes, filename: str) -> str:
    """
    Upload *pdf_bytes* to the 'Mediterranean Meal Plans' folder in Google Drive.

    Returns the web-view URL of the uploaded file so the caller can display it.

    Raises
    ------
    FileNotFoundError   client_secrets.json is missing (guide user to Settings)
    RuntimeError        anything else from the Drive API
    """
    if not credentials_configured():
        raise FileNotFoundError(
            f"client_secrets.json not found at {SECRETS_PATH}. "
            "See the Drive Setup section in Settings for instructions."
        )

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    creds = _get_credentials()
    service = build("drive", "v3", credentials=creds)
    folder_id = _get_or_create_folder(service)

    file_meta = {"name": filename, "parents": [folder_id]}
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")

    try:
        uploaded = service.files().create(
            body=file_meta,
            media_body=media,
            fields="id,webViewLink",
        ).execute()
    except Exception as exc:
        raise RuntimeError(f"Drive upload failed: {exc}") from exc

    return uploaded.get("webViewLink", "")


# ─────────────────────────────────────────────────────────────────────────────
# Internal
# ─────────────────────────────────────────────────────────────────────────────

def _get_credentials():
    """Load cached credentials, refresh if expired, or run the consent flow."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_PATH), SCOPES)
            # port=0 picks any free port; open_browser=True launches the consent page
            creds = flow.run_local_server(port=8080, open_browser=True)
        TOKEN_PATH.write_text(creds.to_json())

    return creds


def _get_or_create_folder(service) -> str:
    """Return the Drive folder ID for FOLDER_NAME, creating it if it doesn't exist."""
    # Check local cache to avoid a network round-trip every call
    if _FOLDER_CACHE.exists():
        try:
            cached = json.loads(_FOLDER_CACHE.read_text())
            if cached.get("folder_id"):
                return cached["folder_id"]
        except (json.JSONDecodeError, KeyError):
            pass

    # Search Drive for the folder
    q = (
        f"name='{FOLDER_NAME}' "
        "and mimeType='application/vnd.google-apps.folder' "
        "and trashed=false"
    )
    results = service.files().list(q=q, fields="files(id)", pageSize=1).execute()
    files = results.get("files", [])

    if files:
        folder_id = files[0]["id"]
    else:
        meta = {
            "name": FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
        }
        folder = service.files().create(body=meta, fields="id").execute()
        folder_id = folder["id"]

    _FOLDER_CACHE.write_text(json.dumps({"folder_id": folder_id}))
    return folder_id
