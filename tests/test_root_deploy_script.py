from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


def test_root_installer_waits_for_web_health_before_finishing() -> None:
    script = (PROJECT / "deploy/install-as-root.sh").read_text(encoding="utf-8")

    assert ': "${MAIL_PORTAL_PROJECT_ROOT:?' in script
    assert ': "${MAIL_PORTAL_DOMAIN:?' in script
    assert ': "${MAIL_PORTAL_PORT:?' in script
    assert ': "${MAIL_PORTAL_WEB_SERVICE:?' in script
    assert "render_template" in script
    assert 'systemctl restart "$WEB_SERVICE"' in script
    assert "for attempt in" in script
    assert 'HEALTH_URL="http://${APP_HOST}:${APP_PORT}/healthz"' in script
    assert 'curl -fsS "$HEALTH_URL"' in script
    assert "systemctl reload caddy" in script
    assert script.index('curl -fsS "$HEALTH_URL"') < script.index("systemctl reload caddy")


def test_root_installer_preserves_existing_instance_secrets() -> None:
    script = (PROJECT / "deploy/install-as-root.sh").read_text(encoding="utf-8")

    assert "if [[ ! -e \"$RUNTIME/secrets/instance-secrets.json\" ]]" in script
    assert "Existing instance secrets were preserved" in script
