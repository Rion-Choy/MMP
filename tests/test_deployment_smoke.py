import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_public_verification_end_to_end_with_session(tmp_path: Path) -> None:
    app = create_app(testing=True, database_url_override="sqlite+pysqlite:///:memory:")
    db = app.state.session_factory()
    from app.models import MailMessage, MailRecipient, PrivateTarget
    from datetime import datetime

    token = "55555555-5555-4555-8555-555555555555"
    target = PrivateTarget(email_address="private@example.com", normalized_email="private@example.com", access_token=token)
    db.add(target)
    db.flush()
    message = MailMessage(immutable_message_id="end-to-end", received_at=datetime.utcnow(), body_text="safe body", first_archived_at=datetime.utcnow(), last_seen_at=datetime.utcnow())
    message.recipients.append(MailRecipient(normalized_email="private@example.com", recipient_type="to"))
    db.add(message)
    db.commit()

    client = TestClient(app)
    first = client.get(f"/m/{token}")
    assert first.status_code == 200
    image = client.get(f"/m/{token}/captcha.svg")
    answer = re.search(r">([A-Za-z0-9]{4})</text>", image.text).group(1)
    verify = client.post(f"/m/{token}/verify", data={"answer": answer}, follow_redirects=False)
    assert verify.status_code == 303
    view = client.get(f"/m/{token}/view?page=1")
    assert view.status_code == 200
    assert "safe body" in view.text
