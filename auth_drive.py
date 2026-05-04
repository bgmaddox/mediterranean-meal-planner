"""
One-time Google Drive authorization script.
Run this once from the terminal to generate data/drive_token.json.
After that, the Streamlit app will use the cached token automatically.

Usage:
    cd /Users/brettmaddox/Documents/CODING/Diet
    source .venv/bin/activate
    python auth_drive.py
"""

from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
SECRETS_PATH = Path("data/client_secrets.json")
TOKEN_PATH = Path("data/drive_token.json")

if not SECRETS_PATH.exists():
    raise FileNotFoundError(f"client_secrets.json not found at {SECRETS_PATH}")

flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_PATH), SCOPES)
creds = flow.run_local_server(port=8080, open_browser=True)
TOKEN_PATH.write_text(creds.to_json())
print(f"✓ Token saved to {TOKEN_PATH}")
print("You can now use the Drive upload feature in the Streamlit app.")
