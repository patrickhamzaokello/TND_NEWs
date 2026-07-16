"""
Database + media backup to Google Drive.

Why OAuth (not a service account): Google Drive service accounts have their
own storage quota — which is 0 bytes on a personal (non-Workspace) account.
Files a service account uploads to a "shared" folder still count against the
service account's own quota and fail with storageQuotaExceeded. The only way
to write into a personal Drive under its real quota is to authenticate AS
that Google account, hence the one-time OAuth flow in `gdrive_authorize`.

Scope used: drive.file — the app can only see/manage files IT created, not
your whole Drive. That's enough to upload, list, and prune old backups.

Setup (one-time):
  1. Google Cloud Console → APIs & Services → Credentials → Create OAuth
     client ID → Application type: Desktop app. Download the JSON as
     `gdrive_client_secret.json` in the project root (or set
     GDRIVE_CLIENT_SECRETS_FILE to its path).
  2. Enable the Google Drive API on that Cloud project.
  3. Run `python manage.py gdrive_authorize` LOCALLY (needs a browser) —
     it opens a consent screen, then writes `gdrive_token.json`.
  4. Copy gdrive_token.json to the server (or set GDRIVE_TOKEN_FILE to its
     path) — the backup command refreshes it automatically after that.
"""

import logging
import os
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive.file']
BACKUP_FOLDER_NAME = 'TNDNEWS Backups'


def _client_secrets_file() -> str:
    return getattr(settings, 'GDRIVE_CLIENT_SECRETS_FILE', None) or str(
        Path(settings.BASE_DIR) / 'gdrive_client_secret.json'
    )


def _token_file() -> str:
    return getattr(settings, 'GDRIVE_TOKEN_FILE', None) or str(
        Path(settings.BASE_DIR) / 'gdrive_token.json'
    )


# ── OAuth ──────────────────────────────────────────────────────────────────

def run_oauth_flow() -> None:
    """Interactive, browser-based — run locally once, then copy the token file."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets_path = _client_secrets_file()
    if not os.path.exists(secrets_path):
        raise FileNotFoundError(
            f"OAuth client secrets not found at {secrets_path}. "
            "Download it from Google Cloud Console (OAuth client ID → Desktop app) "
            "and place it there, or set GDRIVE_CLIENT_SECRETS_FILE."
        )

    flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
    creds = flow.run_local_server(port=0)

    token_path = _token_file()
    with open(token_path, 'w') as f:
        f.write(creds.to_json())
    logger.info('Google Drive token saved to %s', token_path)


def get_drive_service():
    """Build an authenticated Drive API client, refreshing the token if needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_path = _token_file()
    if not os.path.exists(token_path):
        raise FileNotFoundError(
            f"No Google Drive token at {token_path}. "
            "Run `python manage.py gdrive_authorize` locally first, then copy "
            "the resulting gdrive_token.json to the server."
        )

    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)


def ensure_backup_folder(service) -> str:
    """Return the target Drive folder ID, creating it on first run if needed."""
    configured = getattr(settings, 'GDRIVE_BACKUP_FOLDER_ID', '')
    if configured:
        return configured

    query = (
        f"name = '{BACKUP_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    results = service.files().list(q=query, fields='files(id, name)').execute()
    files = results.get('files', [])
    if files:
        return files[0]['id']

    folder = service.files().create(
        body={'name': BACKUP_FOLDER_NAME, 'mimeType': 'application/vnd.google-apps.folder'},
        fields='id',
    ).execute()
    folder_id = folder['id']
    logger.info(
        'Created Drive folder "%s" (id=%s) — set GDRIVE_BACKUP_FOLDER_ID to reuse it explicitly',
        BACKUP_FOLDER_NAME, folder_id,
    )
    return folder_id


# ── Database dump ─────────────────────────────────────────────────────────

def dump_database(output_path: str) -> None:
    """pg_dump the default database in custom (compressed) format."""
    db = settings.DATABASES['default']
    env = os.environ.copy()
    if db.get('PASSWORD'):
        env['PGPASSWORD'] = db['PASSWORD']

    cmd = [
        'pg_dump',
        '-h', db.get('HOST') or 'localhost',
        '-p', str(db.get('PORT') or 5432),
        '-U', db['USER'],
        '-F', 'c',   # custom format — compressed, restorable with pg_restore
        '--no-owner',
        '--no-privileges',
        '-f', output_path,
        db['NAME'],
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {result.stderr[:2000]}")


# ── Zip ───────────────────────────────────────────────────────────────────

def build_backup_zip(zip_path: str, db_dump_path: str, include_media: bool = True) -> int:
    """Bundle the db dump (+ optionally MEDIA_ROOT) into one zip. Returns file count."""
    file_count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_dump_path, arcname='database.dump')
        file_count += 1

        if include_media:
            media_root = Path(settings.MEDIA_ROOT)
            if media_root.exists():
                for path in media_root.rglob('*'):
                    if path.is_file():
                        zf.write(path, arcname=str(Path('media') / path.relative_to(media_root)))
                        file_count += 1
    return file_count


# ── Drive upload / prune ───────────────────────────────────────────────────

def upload_backup(service, folder_id: str, file_path: str) -> dict:
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(file_path, mimetype='application/zip', resumable=True)
    request = service.files().create(
        body={'name': os.path.basename(file_path), 'parents': [folder_id]},
        media_body=media,
        fields='id, name, size, webViewLink',
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info('Upload progress: %d%%', int(status.progress() * 100))
    return response


def prune_old_backups(service, folder_id: str, keep: int) -> int:
    """Delete all but the `keep` most recent backups in the folder. Returns count deleted."""
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        orderBy='createdTime desc',
        fields='files(id, name, createdTime)',
        pageSize=1000,
    ).execute()
    files = results.get('files', [])
    to_delete = files[keep:]
    for f in to_delete:
        service.files().delete(fileId=f['id']).execute()
    return len(to_delete)


# ── Orchestration ──────────────────────────────────────────────────────────

def run_backup(include_media: bool = True, keep: int = 14, keep_local: bool = False) -> dict:
    """
    Full backup pass: pg_dump → zip (+ media) → upload to Drive → prune old ones.
    Returns a summary dict.
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    tmp_dir = tempfile.mkdtemp(prefix='tndnews_backup_')
    db_dump_path = os.path.join(tmp_dir, 'database.dump')
    zip_name = f'tndnews_backup_{timestamp}.zip'
    zip_path = os.path.join(tmp_dir, zip_name)

    logger.info('Backup started | include_media=%s', include_media)
    dump_database(db_dump_path)
    file_count = build_backup_zip(zip_path, db_dump_path, include_media=include_media)
    zip_size = os.path.getsize(zip_path)
    logger.info('Backup archive built | %s | %d files | %.1f MB', zip_name, file_count, zip_size / 1e6)

    service = get_drive_service()
    folder_id = ensure_backup_folder(service)
    uploaded = upload_backup(service, folder_id, zip_path)
    logger.info('Uploaded to Drive | id=%s | link=%s', uploaded.get('id'), uploaded.get('webViewLink'))

    pruned = prune_old_backups(service, folder_id, keep=keep) if keep else 0

    if not keep_local:
        os.remove(db_dump_path)
        os.remove(zip_path)
        os.rmdir(tmp_dir)

    return {
        'zip_name': zip_name,
        'zip_size_mb': round(zip_size / 1e6, 2),
        'file_count': file_count,
        'drive_file_id': uploaded.get('id'),
        'drive_link': uploaded.get('webViewLink'),
        'pruned': pruned,
        'local_path': zip_path if keep_local else None,
    }
