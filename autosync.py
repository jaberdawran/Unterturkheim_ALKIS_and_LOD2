"""
QFieldCloud Auto-Sync Script
============================
Watches QFieldCloud for changes every 30 seconds.
Downloads GeoPackages and syncs attributes to Supabase PostgreSQL.
Media files (photos/videos/PDFs) are uploaded to Supabase Storage
so they are accessible from Cesium online.

Deploy as a Background Worker on Render.
"""

import time
import requests
import sqlite3
import psycopg2
import os
import tempfile
import mimetypes
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# QFieldCloud Configuration
# ─────────────────────────────────────────────────────────────

QFIELDCLOUD_TOKEN   = 'IvenfNSVDuWBnpU6K5Gth0VcWrxgk9NHnmeHOE9CRvqxA0y31SC9SKG0zmXqfB6KplHhH8IJsq9OC0P1Hf6j1390CBQKQHPIIiKL'
QFIELDCLOUD_URL     = 'https://app.qfield.cloud/api/v1'
QFIELDCLOUD_PROJECT = 'jaberdawran'
PROJECT_NAME        = 'Digital_Twin_Unterturkheim'
PROJECT_ID          = None

# ─────────────────────────────────────────────────────────────
# Supabase PostgreSQL Configuration
# ─────────────────────────────────────────────────────────────

PG_HOST     = 'aws-1-eu-north-1.pooler.supabase.com'
PG_PORT     = 6543          # 6543 = session mode, more stable for long-running scripts
PG_DATABASE = 'postgres'
PG_USER     = 'postgres.kxisoojfjyhcqvjxfgbl'
PG_PASSWORD = os.environ.get('PG_PASSWORD', 'Afghanistan@28911@')

PG_SCHEMA   = '2D_ALKIS_Buildings'
PG_TABLE    = 'ALKIS_2D_Buildings'

# ─────────────────────────────────────────────────────────────
# Supabase Storage Configuration
# Media files are uploaded here so Cesium can access them online
# ─────────────────────────────────────────────────────────────

# Your Supabase project URL — find it in Supabase → Settings → API
SUPABASE_URL     = os.environ.get('SUPABASE_URL', 'https://kxisoojfjyhcqvjxfgbl.supabase.co')
# Your Supabase service_role key — find it in Supabase → Settings → API → service_role
SUPABASE_KEY     = os.environ.get('SUPABASE_KEY', '')
# Storage bucket name — create this once in Supabase → Storage
SUPABASE_BUCKET  = 'building-media'

# ─────────────────────────────────────────────────────────────
# Sync Settings
# ─────────────────────────────────────────────────────────────

SYNC_INTERVAL = 30   # seconds — 1s is too aggressive for cloud deployment

TEMP_DIR = os.path.join(tempfile.gettempdir(), 'qfc_autosync')

# ─────────────────────────────────────────────────────────────

HEADERS = {
    'Authorization': f'Token {QFIELDCLOUD_TOKEN}',
    'Content-Type': 'application/json',
}

last_version = None

# ─────────────────────────────────────────────────────────────


def log(msg):
    # Flush immediately so Render shows logs in real time
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────
# Find Project UUID Automatically
# ─────────────────────────────────────────────────────────────

def find_project_id():
    global PROJECT_ID
    try:
        log("🔍 Searching for QFieldCloud project...")
        r = requests.get(f"{QFIELDCLOUD_URL}/projects/", headers=HEADERS, timeout=30)
        if r.status_code != 200:
            log(f"❌ Could not list projects: {r.status_code} {r.text}")
            return None
        for p in r.json():
            if p.get('name', '') == PROJECT_NAME:
                PROJECT_ID = p.get('id')
                log(f"✅ Found project: {PROJECT_ID}")
                return PROJECT_ID
        log(f"❌ Project not found: {PROJECT_NAME}")
        return None
    except Exception as e:
        log(f"❌ Error finding project: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Project Info & File Listing
# ─────────────────────────────────────────────────────────────

def get_project_info():
    try:
        r = requests.get(f"{QFIELDCLOUD_URL}/projects/{PROJECT_ID}/", headers=HEADERS, timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        log(f"❌ Network error: {e}")
        return None


def list_project_files():
    try:
        r = requests.get(f"{QFIELDCLOUD_URL}/files/{PROJECT_ID}/", headers=HEADERS, timeout=30)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        log(f"❌ Error listing files: {e}")
        return []


def debug_list_all_files():
    files = list_project_files()
    if not files:
        log("⚠️ No files found in project.")
        return
    log(f"📂 All files in project ({len(files)} total):")
    for f in files:
        log(f"   {f.get('name','?')}  ({f.get('size','?')} bytes)")


# ─────────────────────────────────────────────────────────────
# Download GeoPackage
# ─────────────────────────────────────────────────────────────

def download_file(filename, local_path):
    try:
        url = f"{QFIELDCLOUD_URL}/files/{PROJECT_ID}/{filename}/"
        r = requests.get(url, headers=HEADERS, stream=True, timeout=120)
        if r.status_code != 200:
            log(f"❌ Error downloading {filename}: {r.status_code}")
            return False
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        log(f"❌ Download error: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# Upload Media to Supabase Storage
# ─────────────────────────────────────────────────────────────

def upload_to_supabase_storage(local_path, storage_filename):
    """
    Upload a file to Supabase Storage bucket.
    Returns the public URL, or None on failure.
    """
    if not SUPABASE_KEY:
        log("⚠️ SUPABASE_KEY not set — skipping media upload")
        return None

    try:
        mime, _ = mimetypes.guess_type(local_path)
        mime = mime or 'application/octet-stream'

        upload_url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{storage_filename}"

        with open(local_path, 'rb') as f:
            r = requests.post(
                upload_url,
                headers={
                    'Authorization': f'Bearer {SUPABASE_KEY}',
                    'Content-Type': mime,
                    'x-upsert': 'true',   # overwrite if already exists
                },
                data=f,
                timeout=120
            )

        if r.status_code in (200, 201):
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{storage_filename}"
            log(f"☁️  Uploaded to Supabase Storage: {storage_filename}")
            return public_url
        else:
            log(f"⚠️ Storage upload failed: {r.status_code} {r.text[:200]}")
            return None

    except Exception as e:
        log(f"⚠️ Storage upload error: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Download Attachment from QFieldCloud + Upload to Supabase
# ─────────────────────────────────────────────────────────────

def download_attachment(filename, oid):
    """
    1. Download media file from QFieldCloud
    2. Upload it to Supabase Storage
    3. Return the public URL (so Cesium can load it from anywhere)
    """
    if not filename or filename.lower() == 'none' or not filename.strip():
        return None

    filename  = filename.replace('\\', '/')
    bare_name = filename.split('/')[-1]
    ext       = os.path.splitext(bare_name)[1] or '.jpg'

    storage_filename = f"{oid}{ext}"   # e.g. DEBWL522100019H0BL.jpg

    # Try to download from QFieldCloud
    local_path = os.path.join(TEMP_DIR, storage_filename)
    os.makedirs(TEMP_DIR, exist_ok=True)

    # Skip if already uploaded (check Supabase Storage)
    # We use the public URL pattern directly
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{storage_filename}"

    urls_to_try = [
        f"{QFIELDCLOUD_URL}/files/{PROJECT_ID}/files/{bare_name}/",
        f"{QFIELDCLOUD_URL}/files/{PROJECT_ID}/{filename}/",
        f"{QFIELDCLOUD_URL}/files/{PROJECT_ID}/{bare_name}/",
    ]

    downloaded = False
    for url in urls_to_try:
        try:
            r = requests.get(url, headers=HEADERS, stream=True, timeout=120)
            if r.status_code == 200:
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                downloaded = True
                break
            else:
                log(f"   ↳ Tried {url} → {r.status_code}")
        except Exception as e:
            log(f"   ↳ Error: {e}")

    if not downloaded:
        log(f"⚠️ Could not download: {bare_name}")
        return None

    # Upload to Supabase Storage
    result = upload_to_supabase_storage(local_path, storage_filename)

    # Clean up local temp file
    try:
        os.remove(local_path)
    except Exception:
        pass

    return result or storage_filename


# ─────────────────────────────────────────────────────────────
# Sync GeoPackage → PostgreSQL
# ─────────────────────────────────────────────────────────────

def sync_gpkg_to_postgres(gpkg_path):
    try:
        gpkg_conn = sqlite3.connect(gpkg_path)
        gpkg_conn.row_factory = sqlite3.Row
        cur = gpkg_conn.cursor()

        cur.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'")
        tables = [row[0] for row in cur.fetchall()]

        if not tables:
            log("⚠️ No feature tables found.")
            return False

        log(f"📦 GeoPackage layers: {tables}")

        pg_conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT,
            dbname=PG_DATABASE, user=PG_USER, password=PG_PASSWORD,
            connect_timeout=30
        )
        pg_cur = pg_conn.cursor()
        synced_count = 0

        for table in tables:
            log(f"🔄 Syncing layer: {table}")
            cur.execute(f'SELECT * FROM "{table}"')
            rows    = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            log(f"   Records found: {len(rows)}")

            for row in rows:
                row_dict = dict(zip(columns, row))
                oid = row_dict.get('oid_')
                if not oid:
                    continue

                field_mapping = {
                    'aktualit':   'aktualit',
                    'gebnutzbez': 'gebnutzbez',
                    'funktion':   'funktion',
                    'fktkurz':    'fktkurz',
                    'name':       'name',
                    'anzahlgs':   'anzahlgs',
                    'lagebeztxt': 'lagebeztxt',
                    'PHOTO':      'PHOTO',   # single field — holds photo, video, or PDF
                }

                update_fields = []
                update_values = []

                for gpkg_col, pg_col in field_mapping.items():
                    if gpkg_col not in row_dict:
                        continue
                    val = row_dict[gpkg_col]
                    if val is None:
                        continue

                    if gpkg_col == 'PHOTO':
                        # Download from QFieldCloud and upload to Supabase Storage
                        saved = download_attachment(str(val), oid)
                        if saved:
                            val = saved   # store public URL or filename

                    update_fields.append(f'"{pg_col}" = %s')
                    update_values.append(val)

                if not update_fields:
                    continue

                update_values.append(oid)
                sql = f'''
                    UPDATE "{PG_SCHEMA}"."{PG_TABLE}"
                    SET {", ".join(update_fields)}
                    WHERE "oid_" = %s
                '''
                pg_cur.execute(sql, update_values)
                synced_count += pg_cur.rowcount

        pg_conn.commit()
        pg_cur.close()
        pg_conn.close()
        gpkg_conn.close()

        log(f"✅ Synced {synced_count} records")
        return True

    except Exception as e:
        log(f"❌ Sync error: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# Check For Changes
# ─────────────────────────────────────────────────────────────

def check_and_sync():
    global last_version

    log("🔍 Checking QFieldCloud for changes...")
    project = get_project_info()
    if not project:
        return

    current_version = (
        project.get('updated_at')
        or project.get('data_last_updated_at')
        or project.get('status')
    )
    log(f"   Project version: {current_version}")

    if current_version == last_version:
        log("   No changes detected.")
        return

    last_version = current_version
    log("🆕 Changes detected!")

    files      = list_project_files()
    gpkg_files = [f for f in files if str(f.get('name', '')).endswith('.gpkg')]

    if not gpkg_files:
        log("⚠️ No GeoPackage files found.")
        return

    os.makedirs(TEMP_DIR, exist_ok=True)

    for gpkg_file in gpkg_files:
        filename   = gpkg_file.get('name')
        local_path = os.path.join(TEMP_DIR, filename)
        log(f"⬇️  Downloading {filename}...")
        if download_file(filename, local_path):
            log(f"✅ Downloaded {filename}")
            sync_gpkg_to_postgres(local_path)
        else:
            log(f"❌ Failed to download {filename}")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':

    log("=" * 60)
    log("  QFieldCloud Auto-Sync → Supabase PostgreSQL")
    log("=" * 60)
    log(f"  User     : {QFIELDCLOUD_PROJECT}")
    log(f"  Project  : {PROJECT_NAME}")
    log(f"  Database : {PG_HOST}")
    log(f"  Storage  : {SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/")
    log(f"  Interval : every {SYNC_INTERVAL} seconds")
    log("=" * 60)

    if not find_project_id():
        log("❌ Cannot continue without valid project UUID.")
        exit(1)

    log("🔎 Scanning project files...")
    debug_list_all_files()
    log("=" * 60)

    while True:
        try:
            check_and_sync()
        except KeyboardInterrupt:
            log("👋 Stopped by user.")
            break
        except Exception as e:
            log(f"❌ Unexpected error: {e}")

        log(f"⏳ Next check in {SYNC_INTERVAL} seconds...\n")
        time.sleep(SYNC_INTERVAL)
