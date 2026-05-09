#!/usr/bin/env python3
"""
secure-git-sync-engine.py

DDL Git Sync — Dynamic Token Push
====================================
Authenticates via HashiCorp Vault (AppRole), fetches a short-lived GitHub App
installation token, and pushes all extracted DDL files to the target repository.

Security model:
  - No credentials are hardcoded. All secrets are pulled from Vault at runtime.
  - Vault connection parameters are read from environment variables.
  - GitHub access token is ephemeral (10-minute JWT window, short-lived install token).
  - SQL Server logging uses Windows Authentication on localhost.

Environment variables required:
  VAULT_URL           — Vault server URL  (e.g. https://vault.internal:8200)
  VAULT_API_PATH      — KV path to GitHub App secrets
  VAULT_ROLE_ID       — AppRole Role ID
  VAULT_SECRET_ID     — AppRole Secret ID
  GITHUB_ORG          — GitHub organization name
  GITHUB_REPO         — Target repository name
  DDL_OUTPUT_DIR      — Local directory containing extracted DDL files

See .env.example for a template.

@author       Madan U
@role         Cloud Database Administrator
@project      Database DDL Versioning Platform
"""

import os
import sys
import datetime
import subprocess
import urllib3
import pyodbc
import requests
import jwt
from collections import defaultdict
from cryptography.hazmat.primitives import serialization

# Suppress SSL warnings for internal Vault endpoints (self-signed cert environment)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────
# CONFIGURATION — all values from environment
# ─────────────────────────────────────────────
VAULT_URL     = os.environ.get("VAULT_URL", "")
VAULT_API_PATH = os.environ.get("VAULT_API_PATH", "")
VAULT_ROLE_ID  = os.environ.get("VAULT_ROLE_ID", "")
VAULT_SECRET_ID = os.environ.get("VAULT_SECRET_ID", "")
GITHUB_ORG    = os.environ.get("GITHUB_ORG", "")
GITHUB_REPO   = os.environ.get("GITHUB_REPO", "")
TARGET_DIR    = os.environ.get("DDL_OUTPUT_DIR", r"C:\DDL_Automation\ddl_exports")
LOGGING_DB    = "DBA_Admin"

_required = {
    "VAULT_URL": VAULT_URL, "VAULT_API_PATH": VAULT_API_PATH,
    "VAULT_ROLE_ID": VAULT_ROLE_ID, "VAULT_SECRET_ID": VAULT_SECRET_ID,
    "GITHUB_ORG": GITHUB_ORG, "GITHUB_REPO": GITHUB_REPO,
}
_missing = [k for k, v in _required.items() if not v]
if _missing:
    sys.exit(f"ERROR: Missing required environment variables: {', '.join(_missing)}")

SUMMARY_FILE = os.path.join(TARGET_DIR, "git_summary.txt")

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
summary_log: list[str] = []

def write_log(message: str) -> None:
    print(message)
    ts = datetime.datetime.now().strftime('%H:%M:%S')
    summary_log.append(f"[{ts}] {message}")

def save_summary() -> None:
    try:
        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(summary_log))
        print(f"\n[INFO] Summary saved: {SUMMARY_FILE}")
    except Exception as e:
        print(f"\n[ERROR] Failed to write summary: {e}")

# ─────────────────────────────────────────────
# SQL LOGGING
# Updates GitStatus in the central management table.
# Uses Windows Auth (localhost) — no password over the wire.
# ─────────────────────────────────────────────
def update_git_status(status: str) -> None:
    conn_str = (
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=localhost;"
        f"Database={LOGGING_DB};"
        "Trusted_Connection=yes;"
    )
    sql = """
        UPDATE DDL_Git_Sync_Logs
        SET    GitStatus = ?
        WHERE  CAST(ExecutionTime AS DATE) = CAST(GETDATE() AS DATE)
    """
    try:
        with pyodbc.connect(conn_str, timeout=5) as conn:
            conn.cursor().execute(sql, (status,))
            conn.commit()
        write_log(f"    → SQL log updated: {status}")
    except Exception as e:
        write_log(f"    → WARNING: SQL logging failed: {e}")

# ─────────────────────────────────────────────
# VAULT AUTHENTICATION
# ─────────────────────────────────────────────
def vault_approle_login() -> str:
    """Login to Vault with AppRole, return client token."""
    resp = requests.post(
        f"{VAULT_URL}/v1/auth/approle/login",
        json={"role_id": VAULT_ROLE_ID, "secret_id": VAULT_SECRET_ID},
        verify=False,
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Vault login failed ({resp.status_code}): {resp.text}")
    return resp.json()["auth"]["client_token"]

def get_vault_secrets(token: str) -> tuple[str, str]:
    """Pull GitHub App ID and private key from Vault KV."""
    resp = requests.get(
        f"{VAULT_URL}{VAULT_API_PATH}",
        headers={"X-Vault-Token": token},
        verify=False,
        timeout=10,
    )
    data = resp.json()["data"]["data"]
    # Key names below match what your Vault KV store actually contains.
    # Update if your Vault path uses different field names.
    app_id_key  = os.environ.get("VAULT_GH_APP_ID_KEY",  "GH_APP_ID")
    app_key_key = os.environ.get("VAULT_GH_APP_KEY_KEY", "GH_APP_KEY")
    return data[app_id_key], data[app_key_key]

# ─────────────────────────────────────────────
# GITHUB TOKEN GENERATION
# Standard GitHub App authentication flow:
#   1. Sign a 10-minute JWT with the App's RSA private key
#   2. Exchange for an installation token (short-lived, scoped to the repo)
# ─────────────────────────────────────────────
def generate_github_token(app_id: str, pk_pem: str | bytes) -> str:
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    payload = {"iat": now - 60, "exp": now + 600, "iss": app_id}

    if isinstance(pk_pem, str):
        pk_pem = pk_pem.encode("utf-8")
    private_key = serialization.load_pem_private_key(pk_pem, password=None)
    jwt_token   = jwt.encode(payload, private_key, algorithm="RS256")

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    inst_resp = requests.get(
        f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}/installation",
        headers=headers, timeout=10,
    )
    inst_id = inst_resp.json()["id"]
    tk_resp = requests.post(
        f"https://api.github.com/app/installations/{inst_id}/access_tokens",
        headers=headers, timeout=10,
    )
    return tk_resp.json()["token"]

# ─────────────────────────────────────────────
# GIT OPERATIONS
# ─────────────────────────────────────────────
def execute_git_push(token: str) -> None:
    repo_url = f"https://x-access-token:{token}@github.com/{GITHUB_ORG}/{GITHUB_REPO}.git"
    os.chdir(TARGET_DIR)

    # Initialize repo if first run
    if not os.path.exists(".git"):
        write_log("    → Initializing Git repository...")
        subprocess.run(["git", "init", "--quiet"],  check=True)
        subprocess.run(["git", "branch", "-M", "master"], check=True)

    # Configure repo settings (line endings, identity)
    for cmd in [
        ["git", "config", "core.autocrlf", "true"],
        ["git", "config", "core.safecrlf", "false"],
        ["git", "config", "advice.detachedHead", "false"],
        ["git", "config", "user.name",  "dbateam"],
        ["git", "config", "user.email", "dba@yourdomain.com"],  # update as needed
    ]:
        subprocess.run(cmd, check=True)

    # Set / update remote
    remotes = subprocess.run(["git", "remote"], capture_output=True, text=True)
    if "origin" in remotes.stdout:
        subprocess.run(["git", "remote", "set-url", "origin", repo_url])
    else:
        subprocess.run(["git", "remote", "add", "origin", repo_url])

    # Stage all files
    write_log("    → Staging changes (git add .)...")
    subprocess.run(["git", "add", "."], check=True)

    status = subprocess.run(["git", "status", "--porcelain"],
                             capture_output=True, text=True)

    ist_now    = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    commit_msg = f"ddl backup - {ist_now.strftime('%Y-%m-%d %H:%M:%S')}"
    db_status  = "No Changes"

    # Commit if there are changes
    if not status.stdout.strip():
        write_log("    → No file changes detected. Skipping commit.")
    else:
        changes = status.stdout.strip().split("\n")
        server_counts: dict[str, int] = defaultdict(int)
        for line in changes:
            path = line[3:].strip('"')
            if " -> " in path:
                path = path.split(" -> ")[1]
            parts = path.replace("\\", "/").split("/")
            server_counts[parts[0] if len(parts) > 1 else "root"] += 1

        write_log(f"    → {len(changes)} file(s) changed across "
                  f"{len(server_counts)} server folder(s):")
        for sv, cnt in sorted(server_counts.items()):
            write_log(f"        {sv}: {cnt} file(s)")

        write_log(f"    → Committing: '{commit_msg}'")
        commit_res = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True, text=True, check=True,
        )
        if commit_res.stdout:
            write_log(f"    → {commit_res.stdout.strip()}")
        db_status = "Success"

    # Push unconditionally (fixes "ahead by N commits" drift)
    write_log("    → Pushing to origin/master...")
    push_res = subprocess.run(
        ["git", "push", "-u", "origin", "master"],
        capture_output=True, text=True,
    )
    if push_res.returncode == 0:
        details = push_res.stderr.strip() or push_res.stdout.strip()
        if details:
            write_log(f"    → Push details: {details}")
        update_git_status(db_status)
    else:
        write_log(f"    → PUSH FAILED: {push_res.stderr.strip()}")
        update_git_status("Failed")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    write_log("=" * 60)
    write_log(f"  DDL GIT SYNC — {datetime.datetime.now().strftime('%Y-%m-%d')}")
    write_log("=" * 60)
    try:
        write_log("1. Authenticating with Vault...")
        vault_token = vault_approle_login()
        app_id, pk  = get_vault_secrets(vault_token)
        write_log("    → Vault authentication successful.")

        write_log("2. Generating GitHub installation token...")
        gh_token = generate_github_token(app_id, pk)
        write_log("    → GitHub token issued.")

        write_log("3. Executing Git operations...")
        execute_git_push(gh_token)

        write_log("\n[RESULT] All operations completed successfully.")

    except subprocess.CalledProcessError as e:
        err = e.stderr or e.stdout or str(e)
        write_log(f"\n[RESULT] Git command failed: {err}")
        update_git_status("Failed")

    except Exception as e:
        write_log(f"\n[RESULT] Failed: {str(e)[:500]}")
        update_git_status("Failed")

    finally:
        save_summary()
