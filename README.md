# Mail Portal deployment template

This repository contains the Mail Portal application and deployment templates.

Environment-specific deployment values are intentionally not stored in the repository. Before installing, set the values for your own host, domain, service account, and systemd unit names.

## Configure deployment values

Run these commands from the checked-out project directory:

```bash
export MAIL_PORTAL_PROJECT_ROOT="$PWD"
export MAIL_PORTAL_DOMAIN="mail.example.com"
export MAIL_PORTAL_PUBLIC_URL="https://$MAIL_PORTAL_DOMAIN"
export MAIL_PORTAL_HOST="127.0.0.1"
export MAIL_PORTAL_PORT="8000"
export MAIL_PORTAL_USER="mailportal"
export MAIL_PORTAL_GROUP="mailportal"
export MAIL_PORTAL_WEB_SERVICE="mailportal-web.service"
export MAIL_PORTAL_SYNC_SERVICE="mailportal-sync.service"
export MAIL_PORTAL_RUNTIME_DIR="$MAIL_PORTAL_PROJECT_ROOT/runtime"
export MAIL_PORTAL_CADDYFILE="/etc/caddy/Caddyfile"
export MAIL_PORTAL_OAUTH_REDIRECT_URI="$MAIL_PORTAL_PUBLIC_URL/admin/oauth/callback"
```

Replace the example values before running the installer. The service account and group must already exist on the target host. The application should listen on a loopback address behind Caddy.

## Initialize or migrate

```bash
cd "$MAIL_PORTAL_PROJECT_ROOT"

export MAIL_PORTAL_DATA_DIR="$MAIL_PORTAL_RUNTIME_DIR"
uv run python scripts/init_runtime.py --root "$MAIL_PORTAL_RUNTIME_DIR" --migrate
```

The first run prints the administrator password once. Save it securely; do not commit the runtime directory.

## Install as root

After reviewing the deployment templates and setting all required environment variables:

```bash
sudo --preserve-env=MAIL_PORTAL_PROJECT_ROOT,MAIL_PORTAL_DOMAIN,MAIL_PORTAL_PUBLIC_URL,MAIL_PORTAL_HOST,MAIL_PORTAL_PORT,MAIL_PORTAL_USER,MAIL_PORTAL_GROUP,MAIL_PORTAL_WEB_SERVICE,MAIL_PORTAL_SYNC_SERVICE,MAIL_PORTAL_RUNTIME_DIR,MAIL_PORTAL_CADDYFILE,MAIL_PORTAL_OAUTH_REDIRECT_URI \
  "$MAIL_PORTAL_PROJECT_ROOT/deploy/install-as-root.sh"
```

The installer:

1. Renders the generic systemd and Caddy templates with the values supplied in the environment;
2. Installs the rendered Web and Worker units;
3. Preserves existing runtime data and instance secrets;
4. Backs up the Caddy configuration before an optional site-block append;
5. Validates Caddy before reloading it;
6. Waits for the local health endpoint before completing.

The installer is intentionally configuration-driven. It refuses to run when the project root, public domain, loopback port, service account, or systemd unit names are not supplied.

## Deployment templates

`deploy/web.service.template` and `deploy/sync.service.template` are systemd unit templates. `deploy/caddy-mail-portal.caddy` and `deploy/caddy-mail-portal-route.json` are Caddy templates. They use placeholders and are rendered by the installer:

```text
__APP_ROOT__
__RUNTIME_DIR__
__APP_DOMAIN__
__APP_HOST__
__APP_PORT__
__APP_USER__
__APP_GROUP__
__OAUTH_REDIRECT_URI__
```

The JSON file is provided for API-based Caddy deployments and is not intended to be loaded without rendering.

## Verification

```bash
systemctl status "$MAIL_PORTAL_WEB_SERVICE" "$MAIL_PORTAL_SYNC_SERVICE" --no-pager
curl -i "$MAIL_PORTAL_PUBLIC_URL/healthz"
ss -lntp | grep ":$MAIL_PORTAL_PORT"
```

Keep `runtime/`, database files, OAuth configuration, instance secrets, and backups outside version control.
