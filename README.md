# Mail Portal

Mail Portal is a self-hosted Outlook mailbox archiving service and token-gated message viewer. It uses Microsoft Graph to synchronize selected mailbox folders into a local SQLite archive, then exposes the archived messages through an administrator console and private public-access links.

The project is intended for controlled deployments where mailbox data, OAuth credentials, and runtime state remain on infrastructure managed by the operator.

## Features

- Microsoft Outlook / Microsoft Graph synchronization.
- Web, Device Code, and manual refresh-token OAuth configuration modes.
- Incremental synchronization with a per-folder cursor.
- Deduplication using immutable Microsoft message IDs.
- HTML-to-text message conversion and recipient normalization.
- Configurable synchronization interval and folder selection.
- Administrator dashboard with:
  - Private target mailbox management.
  - Bulk target import and export.
  - Short per-target notes.
  - Archived-mail search, filtering, and pagination.
  - Manual synchronization.
  - OAuth, synchronization, and CAPTCHA settings.
- Public mailbox pages protected by long-lived opaque access links.
- Optional CAPTCHA verification for public mailbox access.
- Token-scoped public session cookies so multiple public links can be used safely in one browser.
- Server-side session binding between a public link and its target mailbox.
- CSRF protection for administrator forms.
- No-store response headers and security headers suitable for reverse-proxy deployment.
- SQLite migrations managed with Alembic.
- Systemd and Caddy deployment templates.
- Automated tests covering authentication, OAuth flows, synchronization, public sessions, migrations, and route behavior.

## Technology Stack

- Python 3.11 or 3.12
- FastAPI and Uvicorn
- SQLAlchemy 2
- SQLite with WAL mode
- Alembic
- Microsoft Graph API
- Jinja2 templates
- `uv` for environment and dependency management
- Pytest

## Repository Layout

```text
app/                         Application code, services, routes, and templates
migrations/                  Alembic environment and migration revisions
scripts/                     Runtime initialization, migration, and sync entry points
deploy/                      Generic systemd and Caddy deployment templates
tests/                       Automated test suite
pyproject.toml               Project metadata and dependency constraints
uv.lock                     Locked dependency versions
runtime/                     Local runtime data; ignored by Git
```

The `runtime/` directory is intentionally not part of the source distribution. It may contain the SQLite database, OAuth configuration, instance secrets, backups, and synchronization state.

## Requirements

- Python `>=3.11,<3.13`
- [`uv`](https://docs.astral.sh/uv/)
- A Microsoft application registration configured for the OAuth flow you plan to use.
- Microsoft Graph delegated permissions appropriate for mailbox synchronization:
  - `openid`
  - `profile`
  - `email`
  - `offline_access`
  - `User.Read`
  - `Mail.Read`

For web authorization, configure the application's redirect URI to match the value supplied through `MAIL_PORTAL_OAUTH_REDIRECT_URI`. Device Code authorization requires the corresponding public-client settings in the Microsoft application registration.

## Local Development

### 1. Install dependencies

```bash
git clone <repository-url>
cd mail-portal
uv sync --dev
```

### 2. Initialize a local runtime

Use a runtime directory outside the tracked source files. The example below uses the repository's ignored `runtime/` directory for local development only.

```bash
export MAIL_PORTAL_DATA_DIR="$PWD/runtime"
uv run python scripts/init_runtime.py --root "$MAIL_PORTAL_DATA_DIR" --migrate
```

On the first run, the command prints an administrator password once. Store it securely. The password, instance secrets, OAuth configuration, database, and backups must never be committed to Git or pasted into public documentation.

### 3. Start the development server

```bash
export MAIL_PORTAL_HOST="127.0.0.1"
export MAIL_PORTAL_PORT="8000"
export MAIL_PORTAL_OAUTH_REDIRECT_URI="http://localhost:8000/admin/oauth/callback"

uv run uvicorn app.main:app \
  --host "$MAIL_PORTAL_HOST" \
  --port "$MAIL_PORTAL_PORT"
```

Useful local URLs:

- Health check: `http://127.0.0.1:8000/healthz`
- Administrator login: `http://127.0.0.1:8000/admin/login`
- Administrator dashboard: `http://127.0.0.1:8000/admin`

After signing in, configure the Microsoft mailbox from **Administrator Settings**. The application validates the authorized Microsoft account against the configured mailbox address before saving OAuth results.

## Configuration

Configuration is supplied through environment variables so deployment-specific values do not need to be stored in the repository.

| Variable | Purpose | Default |
| --- | --- | --- |
| `MAIL_PORTAL_DATA_DIR` | Runtime root containing data, secrets, and backups | `<project>/runtime` |
| `MAIL_PORTAL_DATABASE_URL` | Optional SQLAlchemy database URL override | SQLite under `MAIL_PORTAL_DATA_DIR/data/` |
| `MAIL_PORTAL_INSTANCE_SECRETS` | Instance secret file path | `<data-dir>/secrets/instance-secrets.json` |
| `MAIL_PORTAL_MICROSOFT_OAUTH` | Microsoft OAuth configuration path | `<data-dir>/secrets/microsoft-oauth.json` |
| `MAIL_PORTAL_HOST` | Application bind address | `127.0.0.1` |
| `MAIL_PORTAL_PORT` | Application bind port | `8000` |
| `MAIL_PORTAL_OAUTH_REDIRECT_URI` | OAuth callback URL | `http://localhost:8000/admin/oauth/callback` |
| `MAIL_PORTAL_PUBLIC_SESSION_TTL` | Public session lifetime in seconds | `1800` |
| `MAIL_PORTAL_CAPTCHA_TTL` | CAPTCHA lifetime in seconds | `300` |
| `MAIL_PORTAL_ADMIN_SESSION_TTL` | Administrator session lifetime in seconds | `86400` |

The application also supports optional cookie-name overrides. These should only be changed when there is a clear deployment requirement, and administrator cookies must remain separate from public mailbox session cookies.

## Synchronization

The background worker reads the persisted synchronization settings and uses Microsoft Graph to process selected folders incrementally.

```bash
export MAIL_PORTAL_DATA_DIR="/path/to/runtime"
uv run python scripts/run_worker.py
```

Synchronization behavior:

- Each provider folder has its own timestamp-and-message-ID cursor.
- The cursor boundary is inclusive, with local ID comparison to avoid missing messages that share a timestamp.
- A synchronization round processes at most 50 messages across the selected folders.
- Messages are deduplicated by immutable message ID.
- Folder names support common localized aliases such as Inbox, Archive, Junk, Sent, Deleted, and Drafts.
- A file lock prevents overlapping worker and manual synchronization runs.
- When scheduled synchronization is disabled, the worker does not construct a Graph client or perform network work.

The one-shot helper is available for controlled maintenance operations:

```bash
uv run python scripts/sync_once.py --help
```

## Administrator Workflow

1. Sign in at `/admin/login`.
2. Open `/admin/settings`.
3. Configure Microsoft OAuth and verify the mailbox connection.
4. Choose the synchronization interval and folders.
5. Create or import private target addresses under `/admin/targets`.
6. Share only the generated public access link for the intended target.
7. Review synchronized messages from `/admin/messages`.
8. Disable or delete a target when its public access link should no longer work.

Public mailbox pages use the following route shape:

```text
/m/<opaque-access-token>
```

Do not place real access tokens, mailbox addresses, OAuth URLs, or message contents in documentation, issue reports, screenshots, or source control.

## Production Deployment

The repository includes generic deployment templates for a loopback-bound application behind Caddy. The templates contain placeholders and must be rendered for each environment; they are not intended to be installed unchanged.

### Required deployment values

Set values appropriate for the target host before running the installer:

```bash
export MAIL_PORTAL_PROJECT_ROOT="/path/to/mail-portal"
export MAIL_PORTAL_DOMAIN="mail.example.com"
export MAIL_PORTAL_PUBLIC_URL="https://mail.example.com"
export MAIL_PORTAL_HOST="127.0.0.1"
export MAIL_PORTAL_PORT="8000"
export MAIL_PORTAL_USER="mailportal"
export MAIL_PORTAL_GROUP="mailportal"
export MAIL_PORTAL_WEB_SERVICE="mailportal-web.service"
export MAIL_PORTAL_SYNC_SERVICE="mailportal-sync.service"
export MAIL_PORTAL_RUNTIME_DIR="/var/lib/mail-portal-runtime"
export MAIL_PORTAL_CADDYFILE="/etc/caddy/Caddyfile"
export MAIL_PORTAL_OAUTH_REDIRECT_URI="$MAIL_PORTAL_PUBLIC_URL/admin/oauth/callback"
```

The service account and group must already exist. The application should listen on loopback, with Caddy serving the public hostname.

### Install

After reviewing the templates and confirming the environment values:

```bash
sudo --preserve-env=MAIL_PORTAL_PROJECT_ROOT,MAIL_PORTAL_DOMAIN,MAIL_PORTAL_PUBLIC_URL,MAIL_PORTAL_HOST,MAIL_PORTAL_PORT,MAIL_PORTAL_USER,MAIL_PORTAL_GROUP,MAIL_PORTAL_WEB_SERVICE,MAIL_PORTAL_SYNC_SERVICE,MAIL_PORTAL_RUNTIME_DIR,MAIL_PORTAL_CADDYFILE,MAIL_PORTAL_OAUTH_REDIRECT_URI \
  "$MAIL_PORTAL_PROJECT_ROOT/deploy/install-as-root.sh"
```

The installer:

1. Validates required deployment inputs.
2. Renders the systemd and Caddy templates into a temporary directory.
3. Creates the runtime directory with restricted permissions.
4. Initializes instance secrets only on first installation.
5. Runs database migrations as the configured service user.
6. Preserves existing instance secrets, OAuth configuration, and database files.
7. Installs and enables the web and synchronization services.
8. Validates Caddy before reloading it.
9. Waits for the local health endpoint before completing.

The installer performs service restarts and requires root privileges. Review its behavior before using it on an existing deployment.

Before expecting automatic HTTPS, make sure the public DNS records point to the host and that the reverse proxy can reach the loopback application.

## Database and Runtime Safety

Treat the runtime directory as user data:

- Back up the SQLite database before migrations or maintenance.
- Keep SQLite WAL and SHM files together with the database during backup and restore operations.
- Run migrations with the same runtime directory and service identity used by the application.
- Verify `PRAGMA integrity_check` and the Alembic revision after a migration.
- Do not reset or recreate the production database to solve schema problems.
- Do not delete public sessions, synchronization state, OAuth transactions, or message records unless the operation is intentional and understood.

The application stores OAuth refresh tokens, instance secrets, administrator authentication material, synchronization state, and archived mailbox data in the runtime directory. These files are excluded from version control and must remain private.

## Security Notes

- Use HTTPS for production public access.
- Keep the application backend bound to loopback when Caddy or another reverse proxy is used.
- Use a strong administrator password and store it outside source control.
- Keep runtime directories and secret files owned by the service account with restrictive permissions.
- Never commit `.env` files, SQLite databases, WAL/SHM files, OAuth JSON files, instance secrets, logs, backups, or virtual environments.
- Treat public access links as credentials; distribute them only to intended recipients.
- Disable or remove unused target addresses promptly.
- Rotate OAuth credentials through the administrator settings flow rather than editing runtime files in public workspaces.
- Review the full Git history before publishing the repository if it has ever contained runtime artifacts or credentials.

## Testing and Verification

Run the complete test suite with:

```bash
uv run pytest -q
```

Additional checks:

```bash
uv run python -m compileall -q app migrations scripts tests

git diff --check
```

When a migrated database is available, check for schema drift with:

```bash
MAIL_PORTAL_DATA_DIR="/path/to/runtime" uv run alembic check
```

The test suite uses isolated test databases and does not require production OAuth credentials.

## License

No license has been specified for this repository yet. Add an appropriate license before distributing the project publicly.
