#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

: "${MAIL_PORTAL_PROJECT_ROOT:?Set MAIL_PORTAL_PROJECT_ROOT to the checked-out project path.}"
: "${MAIL_PORTAL_DOMAIN:?Set MAIL_PORTAL_DOMAIN to the public hostname.}"
: "${MAIL_PORTAL_PORT:?Set MAIL_PORTAL_PORT to the loopback port.}"
: "${MAIL_PORTAL_USER:?Set MAIL_PORTAL_USER to the service account.}"
: "${MAIL_PORTAL_WEB_SERVICE:?Set MAIL_PORTAL_WEB_SERVICE to the web unit name.}"
: "${MAIL_PORTAL_SYNC_SERVICE:?Set MAIL_PORTAL_SYNC_SERVICE to the worker unit name.}"

PROJECT="$MAIL_PORTAL_PROJECT_ROOT"
APP_DOMAIN="$MAIL_PORTAL_DOMAIN"
APP_PORT="$MAIL_PORTAL_PORT"
APP_HOST="${MAIL_PORTAL_HOST:-127.0.0.1}"
APP_USER="$MAIL_PORTAL_USER"
APP_GROUP="${MAIL_PORTAL_GROUP:-$APP_USER}"
WEB_SERVICE="$MAIL_PORTAL_WEB_SERVICE"
SYNC_SERVICE="$MAIL_PORTAL_SYNC_SERVICE"
RUNTIME="${MAIL_PORTAL_RUNTIME_DIR:-$PROJECT/runtime}"
UNIT_DIR="${MAIL_PORTAL_SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
CADDYFILE="${MAIL_PORTAL_CADDYFILE:-/etc/caddy/Caddyfile}"
PUBLIC_URL="${MAIL_PORTAL_PUBLIC_URL:-https://$APP_DOMAIN}"
OAUTH_REDIRECT_URI="${MAIL_PORTAL_OAUTH_REDIRECT_URI:-$PUBLIC_URL/admin/oauth/callback}"

if [[ ! -d "$PROJECT" ]]; then
  echo "Project directory does not exist: $PROJECT" >&2
  exit 1
fi
if [[ ! -x "$PROJECT/.venv/bin/python" ]]; then
  echo "Project virtualenv is missing or not executable: $PROJECT/.venv/bin/python" >&2
  exit 1
fi
if [[ ! "$APP_PORT" =~ ^[0-9]+$ ]] || (( APP_PORT < 1 || APP_PORT > 65535 )); then
  echo "MAIL_PORTAL_PORT must be a TCP port number between 1 and 65535." >&2
  exit 1
fi
if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "Service account does not exist: $APP_USER" >&2
  exit 1
fi
if ! getent group "$APP_GROUP" >/dev/null 2>&1; then
  echo "Service group does not exist: $APP_GROUP" >&2
  exit 1
fi

cd "$PROJECT"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

render_template() {
  local template="$1"
  local output="$2"
  RENDER_APP_ROOT="$PROJECT" \
  RENDER_RUNTIME_DIR="$RUNTIME" \
  RENDER_APP_HOST="$APP_HOST" \
  RENDER_APP_PORT="$APP_PORT" \
  RENDER_APP_DOMAIN="$APP_DOMAIN" \
  RENDER_APP_USER="$APP_USER" \
  RENDER_APP_GROUP="$APP_GROUP" \
  RENDER_OAUTH_REDIRECT_URI="$OAUTH_REDIRECT_URI" \
  python3 - "$template" "$output" <<'PY'
from pathlib import Path
import os
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
text = source.read_text(encoding="utf-8")
replacements = {
    "__APP_ROOT__": os.environ["RENDER_APP_ROOT"],
    "__RUNTIME_DIR__": os.environ["RENDER_RUNTIME_DIR"],
    "__APP_HOST__": os.environ["RENDER_APP_HOST"],
    "__APP_PORT__": os.environ["RENDER_APP_PORT"],
    "__APP_DOMAIN__": os.environ["RENDER_APP_DOMAIN"],
    "__APP_USER__": os.environ["RENDER_APP_USER"],
    "__APP_GROUP__": os.environ["RENDER_APP_GROUP"],
    "__OAUTH_REDIRECT_URI__": os.environ["RENDER_OAUTH_REDIRECT_URI"],
}
for token, value in replacements.items():
    text = text.replace(token, value)
if "__APP_" in text or "__RUNTIME_" in text or "__OAUTH_" in text:
    raise SystemExit(f"unresolved deployment template token in {source}")
target.write_text(text, encoding="utf-8")
PY
}

WEB_UNIT="$UNIT_DIR/$WEB_SERVICE"
SYNC_UNIT="$UNIT_DIR/$SYNC_SERVICE"
render_template "$PROJECT/deploy/web.service.template" "$TMP_DIR/web.service"
render_template "$PROJECT/deploy/sync.service.template" "$TMP_DIR/sync.service"
render_template "$PROJECT/deploy/caddy-mail-portal.caddy" "$TMP_DIR/caddy-site.caddy"

install -d -o "$APP_USER" -g "$APP_GROUP" -m 0700 "$RUNTIME" "$RUNTIME/data" "$RUNTIME/secrets" "$RUNTIME/backups"

# Initialize secrets only on the first installation; later installs preserve them.
if [[ ! -e "$RUNTIME/secrets/instance-secrets.json" ]]; then
  runuser -u "$APP_USER" -- env MAIL_PORTAL_DATA_DIR="$RUNTIME" \
    PYTHONPATH="$PROJECT" \
    "$PROJECT/.venv/bin/python" "$PROJECT/scripts/init_runtime.py" --root "$RUNTIME" --migrate
else
  chown "$APP_USER:$APP_GROUP" "$RUNTIME/secrets/instance-secrets.json"
  chmod 0600 "$RUNTIME/secrets/instance-secrets.json"
  runuser -u "$APP_USER" -- env MAIL_PORTAL_DATA_DIR="$RUNTIME" \
    PYTHONPATH="$PROJECT" \
    "$PROJECT/.venv/bin/python" "$PROJECT/scripts/migrate.py"
fi

# Preserve existing OAuth configuration and database, correcting ownership if present.
[[ ! -e "$RUNTIME/secrets/microsoft-oauth.json" ]] || { chown "$APP_USER:$APP_GROUP" "$RUNTIME/secrets/microsoft-oauth.json"; chmod 0600 "$RUNTIME/secrets/microsoft-oauth.json"; }
[[ ! -e "$RUNTIME/data/mail-portal.sqlite3" ]] || { chown "$APP_USER:$APP_GROUP" "$RUNTIME/data/mail-portal.sqlite3"; chmod 0600 "$RUNTIME/data/mail-portal.sqlite3"; }

install -o root -g root -m 0644 "$TMP_DIR/web.service" "$WEB_UNIT"
install -o root -g root -m 0644 "$TMP_DIR/sync.service" "$SYNC_UNIT"

if [[ ! -f "$CADDYFILE" ]]; then
  echo "Caddy configuration file does not exist: $CADDYFILE" >&2
  exit 1
fi

cp "$CADDYFILE" "$CADDYFILE.bak-mail-portal-$(date +%Y%m%d-%H%M%S)"
if ! grep -qF "$APP_DOMAIN {" "$CADDYFILE"; then
  printf '\n' >> "$CADDYFILE"
  cat "$TMP_DIR/caddy-site.caddy" >> "$CADDYFILE"
fi

caddy validate --config "$CADDYFILE"
systemctl daemon-reload
systemctl enable "$WEB_SERVICE" "$SYNC_SERVICE"
systemctl restart "$WEB_SERVICE"

HEALTH_URL="http://${APP_HOST}:${APP_PORT}/healthz"
for attempt in {1..30}; do
  if curl -fsS "$HEALTH_URL" >/dev/null; then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    echo "$WEB_SERVICE did not become healthy; inspect systemd status and journal." >&2
    exit 1
  fi
  sleep 1
done

systemctl reload caddy
systemctl restart "$SYNC_SERVICE"

curl -fsS "$HEALTH_URL"
printf '\nInstalled and started the configured Mail Portal services. Existing instance secrets were preserved.\n'
