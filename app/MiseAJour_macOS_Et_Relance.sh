#!/usr/bin/env bash
set -euo pipefail

# macOS updater for the packaged .app (Apple Silicon arm64).
# Downloads the latest GitHub Release asset, replaces the current .app, and relaunches it.
#
# Expected release asset naming (recommended):
# - AthletesSearcher-macOS-arm64.zip  (contains "Athletes Searcher.app" at zip root)
#
# Usage:
#   bash MiseAJour_macOS_Et_Relance.sh "/Applications/Athletes Searcher.app"
#   bash MiseAJour_macOS_Et_Relance.sh "/path/to/Athletes Searcher.app"

APP_PATH="${1:-}"
if [[ -z "${APP_PATH}" ]]; then
  echo "Usage: $0 \"/path/to/Athletes Searcher.app\"" >&2
  exit 2
fi
if [[ "${APP_PATH}" != *.app ]]; then
  echo "[ERROR] APP_PATH must end with .app: ${APP_PATH}" >&2
  exit 2
fi
if [[ ! -d "${APP_PATH}" ]]; then
  echo "[ERROR] App not found: ${APP_PATH}" >&2
  exit 2
fi

REPO_OWNER="rafa-create"
REPO_NAME="AthletesSearch"
ASSET_NAME_CONTAINS="macOS-arm64"

LOG_DIR="${HOME}/Library/Application Support/AthletesSearcher/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/update.log"

ts() { date +"%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] $*" | tee -a "${LOG_FILE}"; }

log "=== Mise à jour Athletes Searcher (macOS) ==="
log "App: ${APP_PATH}"
log "Repo: ${REPO_OWNER}/${REPO_NAME}"

TMP_DIR="$(mktemp -d -t athletes_upd.XXXXXX)"
trap 'rm -rf "${TMP_DIR}" >/dev/null 2>&1 || true' EXIT

API_URL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/latest"
log "[1/5] Récupération de la dernière release…"

if ! command -v /usr/bin/curl >/dev/null 2>&1; then
  log "[ERREUR] curl introuvable."
  exit 1
fi
if ! command -v /usr/bin/python3 >/dev/null 2>&1; then
  log "[ERREUR] python3 introuvable (utilisé pour parser l'API GitHub)."
  exit 1
fi

RELEASE_JSON="${TMP_DIR}/release.json"
/usr/bin/curl -fsSL "${API_URL}" -o "${RELEASE_JSON}" || { log "[ERREUR] Impossible de contacter GitHub API."; exit 1; }

ASSET_URL="$(
  /usr/bin/python3 - <<'PY'
import json, sys
path = sys.argv[1]
contains = sys.argv[2].lower()
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
assets = data.get("assets") or []
best = None
for a in assets:
    name = (a.get("name") or "")
    url = (a.get("browser_download_url") or "")
    if not name or not url:
        continue
    if contains in name.lower() and name.lower().endswith(".zip"):
        best = url
        break
if not best:
    # fallback: first zip asset
    for a in assets:
        name = (a.get("name") or "")
        url = (a.get("browser_download_url") or "")
        if name.lower().endswith(".zip") and url:
            best = url
            break
print(best or "")
PY
  "${RELEASE_JSON}" "${ASSET_NAME_CONTAINS}"
)"

if [[ -z "${ASSET_URL}" ]]; then
  log "[ERREUR] Aucun asset .zip trouvé sur la dernière release."
  exit 1
fi
log "Asset: ${ASSET_URL}"

ZIP_PATH="${TMP_DIR}/update.zip"
log "[2/5] Téléchargement…"
/usr/bin/curl -fL --retry 3 --retry-delay 1 "${ASSET_URL}" -o "${ZIP_PATH}" || { log "[ERREUR] Téléchargement échoué."; exit 1; }

EXTRACT_DIR="${TMP_DIR}/extract"
mkdir -p "${EXTRACT_DIR}"
log "[3/5] Extraction…"
/usr/bin/unzip -q -o "${ZIP_PATH}" -d "${EXTRACT_DIR}" || { log "[ERREUR] Extraction échouée."; exit 1; }

# Find *.app inside extracted content
NEW_APP_PATH="$(/usr/bin/find "${EXTRACT_DIR}" -maxdepth 3 -name "*.app" -type d | /usr/bin/head -n 1 || true)"
if [[ -z "${NEW_APP_PATH}" || ! -d "${NEW_APP_PATH}" ]]; then
  log "[ERREUR] Aucune .app trouvée dans l'archive."
  exit 1
fi
log "Nouveau bundle: ${NEW_APP_PATH}"

PARENT_DIR="$(/usr/bin/dirname "${APP_PATH}")"
APP_BASENAME="$(/usr/bin/basename "${APP_PATH}")"
BACKUP_PATH="${PARENT_DIR}/${APP_BASENAME}.bak.$(date +%Y%m%d_%H%M%S)"

log "[4/5] Remplacement de l'application…"
log "Backup: ${BACKUP_PATH}"

# Ensure the app is not running anymore
/usr/bin/osascript -e 'tell application "Athletes Searcher" to quit' >/dev/null 2>&1 || true
sleep 0.8

# Move old aside, then move new into place.
/bin/mv "${APP_PATH}" "${BACKUP_PATH}" || { log "[ERREUR] Impossible de déplacer l'ancienne app (droits ?)."; exit 1; }
/bin/mv "${NEW_APP_PATH}" "${APP_PATH}" || { log "[ERREUR] Impossible d'installer la nouvelle app."; exit 1; }

log "[5/5] Relance…"
/usr/bin/open "${APP_PATH}" || { log "[ERREUR] Impossible de relancer l'application."; exit 1; }
log "OK."

