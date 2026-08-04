from __future__ import annotations

from datetime import date, datetime, time, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from uuid import uuid4

from app.config import ADMIN_CSRF_COOKIE, ADMIN_SESSION_COOKIE, sync_lock_path
from app.dependencies import require_admin
from app.models import AppSetting, MailMessage, MailRecipient, PrivateTarget, SyncRun, TargetTag
from app.services.admin_session import authenticate_admin
from app.services.csrf import new_csrf_token, validate_csrf
from app.services.email_normalization import normalize_email_address
from app.services.instance_secrets import load_instance_secrets
from app.services.microsoft_oauth import (
    OAuthError,
    build_authorization_url,
    exchange_authorization_code,
    load_oauth_config,
    oauth_config_from_tokens,
    poll_device_code,
    request_device_code,
    save_oauth_config,
    validate_access_token_for_mailbox,
    validate_oauth_config,
)
from app.services.oauth_transactions import (
    OAUTH_STATE_COOKIE,
    consume_transaction,
    create_transaction,
    decode_transaction_payload,
    generate_pkce_pair,
    get_transaction,
    get_transaction_by_state,
)
from app.services.graph_factory import build_graph_client
from app.worker import FileSyncLock, SyncWorker
from app.services.settings_service import (
    MAX_SYNC_INTERVAL_SECONDS,
    MIN_SYNC_INTERVAL_SECONDS,
    get_enabled_folder_names,
    get_sync_enabled,
    get_sync_interval,
    set_enabled_folder_names,
    set_sync_enabled,
    set_sync_interval,
)
from app.services.target_service import create_target, delete_target, disable_target, enable_target
from app.services.tag_service import assign_tag, create_tag, delete_tag, rename_tag
from app.templates import templates

router = APIRouter(prefix="/admin")


_BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _parse_admin_date(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid date filter") from exc
    boundary = datetime.combine(parsed, time.max if end else time.min, tzinfo=_BEIJING_TZ)
    return boundary.astimezone(timezone.utc).replace(tzinfo=None)


def _message_filter_query(
    *,
    recipient: str,
    folder: str,
    received_from: str | None,
    received_to: str | None,
):
    query = select(MailMessage).options(selectinload(MailMessage.recipients))
    if recipient:
        query = query.join(MailRecipient).where(MailRecipient.normalized_email == recipient)
    if folder:
        query = query.where(MailMessage.folder_name == folder)
    start = _parse_admin_date(received_from)
    end = _parse_admin_date(received_to, end=True)
    if start is not None:
        query = query.where(MailMessage.received_at >= start)
    if end is not None:
        query = query.where(MailMessage.received_at <= end)
    if recipient:
        query = query.distinct()
    return query


def _message_query_string(
    *,
    q: str,
    recipient: str,
    folder: str,
    received_from: str,
    received_to: str,
    page_size: int,
    page: int,
) -> str:
    values = {
        "q": q,
        "recipient": recipient,
        "folder": folder,
        "received_from": received_from,
        "received_to": received_to,
        "page_size": page_size,
        "page": page,
    }
    return urlencode({key: value for key, value in values.items() if value not in ("", None)})


def no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def csrf_context(request: Request) -> dict[str, str]:
    return {"csrf_token": request.cookies.get(ADMIN_CSRF_COOKIE) or new_csrf_token()}


def set_csrf_cookie(request: Request, response: Response, token: str) -> Response:
    if request.cookies.get(ADMIN_CSRF_COOKIE) != token:
        response.set_cookie(ADMIN_CSRF_COOKIE, token, httponly=False, samesite="strict", secure=not request.app.state.testing, max_age=3600)
    return response


def get_settings(request: Request) -> dict[str, str]:
    db = request.app.state.session_factory()
    try:
        return {row.setting_key: row.setting_value for row in db.scalars(select(AppSetting))}
    finally:
        db.close()


def _sync_context(request: Request) -> dict[str, object]:
    db = request.app.state.session_factory()
    try:
        return {
            "interval_seconds": get_sync_interval(db),
            "folder_names": ",".join(get_enabled_folder_names(db)),
            "sync_enabled": get_sync_enabled(db),
        }
    finally:
        db.close()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    token = csrf_context(request)["csrf_token"]
    response = templates.TemplateResponse(request, "admin/login.html", {"error": None, "csrf_token": token})
    return set_csrf_cookie(request, no_store(response), token)


@router.post("/login")
def login(request: Request, password: str = Form(...), csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    instance = load_instance_secrets(request.app.state.instance_secrets_path)
    cookie = authenticate_admin(instance, password)
    if cookie is None:
        response = templates.TemplateResponse(request, "admin/login.html", {"error": "密码错误", "csrf_token": csrf_token}, status_code=401)
        return set_csrf_cookie(request, no_store(response), csrf_token)
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(ADMIN_SESSION_COOKIE, cookie, httponly=True, samesite="lax", secure=not request.app.state.testing, max_age=3600)
    return no_store(response)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_SESSION_COOKIE)
    return no_store(response)


@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def dashboard(request: Request) -> Response:
    db = request.app.state.session_factory()
    try:
        latest_sync = db.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
        target_count = db.scalar(select(func.count(PrivateTarget.id)).where(PrivateTarget.removed_at.is_(None))) or 0
        message_count = db.scalar(select(func.count(MailMessage.id))) or 0
        token = csrf_context(request)["csrf_token"]
        response = templates.TemplateResponse(request, "admin/dashboard.html", {"settings": get_settings(request), "latest_sync": latest_sync, "target_count": target_count, "message_count": message_count, "csrf_token": token})
        return set_csrf_cookie(request, no_store(response), token)
    finally:
        db.close()


@router.post("/sync-now", dependencies=[Depends(require_admin)])
def sync_now(request: Request, csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    from app.config import database_url
    from app.database import create_engine_for_url, ensure_database_parent, make_session_factory

    url = database_url()
    ensure_database_parent(url)
    engine = create_engine_for_url(url)
    worker = SyncWorker(make_session_factory(engine), build_graph_client, lock=FileSyncLock(sync_lock_path()))
    try:
        result = worker.run_once()
    except Exception:
        return no_store(RedirectResponse("/admin?sync=failed", status_code=303))
    if result is None:
        return no_store(RedirectResponse("/admin?sync=busy", status_code=303))
    return no_store(RedirectResponse("/admin?sync=done", status_code=303))


@router.get("/messages", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def messages_page(
    request: Request,
    q: str = Query(default=""),
    recipient: str = Query(default=""),
    folder: str = Query(default=""),
    received_from: str = Query(default=""),
    received_to: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Response:
    q = q.strip()
    recipient = recipient.strip().casefold()
    folder = folder.strip()
    db = request.app.state.session_factory()
    try:
        filtered_query = _message_filter_query(
            recipient=recipient,
            folder=folder,
            received_from=received_from,
            received_to=received_to,
        )
        if q:
            filtered_query = filtered_query.where(MailMessage.body_text.ilike(f"%{q}%"))
        total_count = db.scalar(
            select(func.count()).select_from(
                filtered_query.order_by(None).offset(None).limit(None).subquery()
            )
        ) or 0
        messages = list(
            db.scalars(
                filtered_query
                .order_by(MailMessage.received_at.desc(), MailMessage.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size + 1)
            )
        )
        has_next = len(messages) > page_size
        messages = messages[:page_size]
        folders = [
            value
            for value in db.scalars(
                select(MailMessage.folder_name)
                .where(MailMessage.folder_name.is_not(None), MailMessage.folder_name != "")
                .distinct()
                .order_by(MailMessage.folder_name.asc())
            )
            if value
        ]
        query_values = {
            "q": q,
            "recipient": recipient,
            "folder": folder,
            "received_from": received_from,
            "received_to": received_to,
            "page_size": page_size,
        }
        previous_query = _message_query_string(**query_values, page=max(1, page - 1))
        next_query = _message_query_string(**query_values, page=page + 1)
        token = csrf_context(request)["csrf_token"]
        response = templates.TemplateResponse(
            request,
            "admin/messages.html",
            {
                "messages": messages,
                "folders": folders,
                "q": q,
                "recipient": recipient,
                "folder": folder,
                "received_from": received_from,
                "received_to": received_to,
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "has_next": has_next,
                "previous_query": previous_query,
                "next_query": next_query,
                "csrf_token": token,
            },
        )
        return set_csrf_cookie(request, no_store(response), token)
    finally:
        db.close()


@router.get("/targets", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def targets_page(request: Request, tag_id: int | None = Query(default=None)) -> Response:
    db = request.app.state.session_factory()
    try:
        query = select(PrivateTarget).where(PrivateTarget.removed_at.is_(None))
        if tag_id is not None:
            query = query.where(PrivateTarget.tag_id == tag_id)
        targets = list(db.scalars(query.order_by(PrivateTarget.created_at.desc(), PrivateTarget.id.desc())))
        tags = list(db.scalars(select(TargetTag).order_by(TargetTag.name.asc())))
        token = csrf_context(request)["csrf_token"]
        response = templates.TemplateResponse(request, "admin/targets.html", {"targets": targets, "tags": tags, "selected_tag_id": tag_id, "error": None, "csrf_token": token})
        return set_csrf_cookie(request, no_store(response), token)
    finally:
        db.close()


@router.post("/targets", dependencies=[Depends(require_admin)])
def add_target(request: Request, email_address: str = Form(...), csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    db = request.app.state.session_factory()
    try:
        try:
            create_target(db, email_address)
            db.commit()
        except ValueError:
            db.rollback()
            targets = list(db.scalars(select(PrivateTarget).where(PrivateTarget.removed_at.is_(None)).order_by(PrivateTarget.created_at.desc(), PrivateTarget.id.desc())))
            tags = list(db.scalars(select(TargetTag).order_by(TargetTag.name.asc())))
            response = templates.TemplateResponse(request, "admin/targets.html", {"targets": targets, "tags": tags, "selected_tag_id": None, "error": "邮箱地址格式不正确", "csrf_token": csrf_token}, status_code=400)
            return set_csrf_cookie(request, no_store(response), csrf_token)
        return no_store(RedirectResponse("/admin/targets", status_code=303))
    finally:
        db.close()


def _assign_target_tag(request: Request, target_id: int, tag_id: int | None) -> Response:
    db = request.app.state.session_factory()
    try:
        if assign_tag(db, target_id, tag_id) is None:
            raise ValueError("隐私邮箱不存在")
        db.commit()
        return no_store(RedirectResponse("/admin/targets", status_code=303))
    except (ValueError, TypeError):
        db.rollback()
        return no_store(RedirectResponse("/admin/targets?tag_error=invalid", status_code=303))
    finally:
        db.close()


@router.post("/targets/{target_id}/tag", dependencies=[Depends(require_admin)])
def assign_target_tag(request: Request, target_id: int, tag_id: str = Form(""), csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    try:
        value = int(tag_id) if tag_id.strip() else None
    except (TypeError, ValueError):
        return no_store(RedirectResponse("/admin/targets?tag_error=invalid", status_code=303))
    return _assign_target_tag(request, target_id, value)


@router.post("/targets/{target_id}/tag/create", dependencies=[Depends(require_admin)])
def create_and_assign_target_tag(
    request: Request,
    target_id: int,
    name: str = Form(...),
    csrf_token: str = Form(...),
) -> Response:
    validate_csrf(request, csrf_token)
    db = request.app.state.session_factory()
    try:
        target = db.get(PrivateTarget, target_id)
        if target is None or target.removed_at is not None:
            raise ValueError("隐私邮箱不存在")
        tag = create_tag(db, name)
        target.tag_id = tag.id
        db.commit()
        return no_store(RedirectResponse("/admin/targets", status_code=303))
    except (ValueError, TypeError):
        db.rollback()
        return no_store(RedirectResponse("/admin/targets?tag_error=invalid", status_code=303))
    finally:
        db.close()


@router.get("/tags", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def tags_page(request: Request) -> Response:
    db = request.app.state.session_factory()
    try:
        tags = list(db.scalars(select(TargetTag).order_by(TargetTag.name.asc())))
        token = csrf_context(request)["csrf_token"]
        response = templates.TemplateResponse(request, "admin/tags.html", {"tags": tags, "error": request.query_params.get("error"), "csrf_token": token})
        return set_csrf_cookie(request, no_store(response), token)
    finally:
        db.close()


def _tag_redirect(next_path: str, *, error: bool = False) -> str:
    destination = "/admin/targets" if next_path == "/admin/targets" else "/admin/tags"
    if error:
        return f"{destination}?tag_error=invalid" if destination == "/admin/targets" else f"{destination}?error=invalid"
    return destination


@router.post("/tags", dependencies=[Depends(require_admin)])
def add_tag(request: Request, name: str = Form(...), color: str = Form(""), next_path: str = Form("/admin/tags"), csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    db = request.app.state.session_factory()
    try:
        create_tag(db, name, color)
        db.commit()
        return no_store(RedirectResponse(_tag_redirect(next_path), status_code=303))
    except ValueError as exc:
        db.rollback()
        return no_store(RedirectResponse(_tag_redirect(next_path, error=True), status_code=303))
    finally:
        db.close()


@router.post("/tags/{tag_id}", dependencies=[Depends(require_admin)])
def edit_tag(request: Request, tag_id: int, name: str = Form(...), color: str = Form(""), csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    db = request.app.state.session_factory()
    try:
        if rename_tag(db, tag_id, name, color) is None:
            raise ValueError("标签不存在")
        db.commit()
        return no_store(RedirectResponse("/admin/tags", status_code=303))
    except ValueError as exc:
        db.rollback()
        return no_store(RedirectResponse(f"/admin/tags?error={str(exc)}", status_code=303))
    finally:
        db.close()


@router.post("/tags/{tag_id}/delete", dependencies=[Depends(require_admin)])
def remove_tag(request: Request, tag_id: int, csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    db = request.app.state.session_factory()
    try:
        delete_tag(db, tag_id)
        db.commit()
        return no_store(RedirectResponse("/admin/tags", status_code=303))
    finally:
        db.close()

@router.post("/targets/{target_id}/enable", dependencies=[Depends(require_admin)])
def enable_target_route(request: Request, target_id: int, csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    db = request.app.state.session_factory()
    try:
        enable_target(db, target_id); db.commit()
        return no_store(RedirectResponse("/admin/targets", status_code=303))
    finally:
        db.close()


@router.post("/targets/{target_id}/disable", dependencies=[Depends(require_admin)])
def disable_target_route(request: Request, target_id: int, csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    db = request.app.state.session_factory()
    try:
        disable_target(db, target_id); db.commit()
        return no_store(RedirectResponse("/admin/targets", status_code=303))
    finally:
        db.close()


@router.post("/targets/{target_id}/delete", dependencies=[Depends(require_admin)])
def delete_target_route(request: Request, target_id: int, csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    db = request.app.state.session_factory()
    try:
        delete_target(db, target_id); db.commit()
        return no_store(RedirectResponse("/admin/targets", status_code=303))
    finally:
        db.close()


@router.get("/mailbox", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def mailbox_page(request: Request, edit: int = Query(default=0)) -> Response:
    path = request.app.state.microsoft_oauth_path
    if path.exists():
        config = load_oauth_config(path)
        config["refresh_token_configured"] = bool(config.get("refresh_token"))
        config.pop("refresh_token", None)
    else:
        config = {"authority": "consumers", "auth_method": "manual", "refresh_token_configured": False}
    sync = _sync_context(request)
    token = csrf_context(request)["csrf_token"]
    connected = bool(config.get("refresh_token_configured"))
    auth_method = str(config.get("auth_method") or "manual")
    fields_read_only = connected and auth_method in {"web", "device"} and not bool(edit)
    context = {
        "config": config,
        **sync,
        "fields_read_only": fields_read_only,
        "can_edit_oauth": fields_read_only,
        "error": request.query_params.get("error"),
        "csrf_token": token,
        "authorization_url": None,
        "device_authorization": None,
    }
    response = templates.TemplateResponse(request, "admin/mother_mailbox.html", context)
    return set_csrf_cookie(request, no_store(response), token)


@router.post("/mailbox/sync-settings", dependencies=[Depends(require_admin)])
def save_sync_settings(
    request: Request,
    sync_interval_seconds: int = Form(...),
    folder_names: str = Form(""),
    csrf_token: str = Form(...),
) -> Response:
    validate_csrf(request, csrf_token)
    try:
        if not MIN_SYNC_INTERVAL_SECONDS <= sync_interval_seconds <= MAX_SYNC_INTERVAL_SECONDS:
            raise ValueError(
                f"同步间隔必须在 {MIN_SYNC_INTERVAL_SECONDS} 到 {MAX_SYNC_INTERVAL_SECONDS} 秒之间"
            )
        db = request.app.state.session_factory()
        try:
            set_sync_interval(db, sync_interval_seconds)
            set_enabled_folder_names(db, folder_names.split(","))
            db.commit()
        finally:
            db.close()
    except (ValueError, OSError):
        return no_store(RedirectResponse("/admin/mailbox?sync_error=invalid", status_code=303))
    return no_store(RedirectResponse("/admin/mailbox?sync=saved", status_code=303))


@router.post("/mailbox/sync-toggle", dependencies=[Depends(require_admin)])
def toggle_sync(request: Request, csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    db = request.app.state.session_factory()
    try:
        set_sync_enabled(db, not get_sync_enabled(db))
        db.commit()
    finally:
        db.close()
    return no_store(RedirectResponse("/admin/mailbox?sync=toggled", status_code=303))


@router.post("/mailbox", dependencies=[Depends(require_admin)])
def save_mailbox(
    request: Request,
    mailbox_address: str = Form(...),
    client_id: str = Form(...),
    authority: str = Form("consumers"),
    refresh_token: str = Form(""),
    oauth_mode: str = Form("manual"),
    csrf_token: str = Form(...),
    edit: int = Query(default=0),
) -> Response:
    validate_csrf(request, csrf_token)
    try:
        normalized = normalize_email_address(mailbox_address)
        path = request.app.state.microsoft_oauth_path
        existing = load_oauth_config(path) if path.exists() else {}
        candidate_identity = {
            "mailbox_address": normalized,
            "client_id": client_id.strip(),
            "authority": authority.strip() or "consumers",
        }
        existing_identity = {
            "mailbox_address": str(existing.get("mailbox_address") or "").strip().casefold(),
            "client_id": str(existing.get("client_id") or "").strip(),
            "authority": str(existing.get("authority") or "consumers").strip() or "consumers",
        }
        has_existing_connection = bool(existing.get("refresh_token"))
        existing_method = str(existing.get("auth_method") or "manual")
        same_identity = candidate_identity == existing_identity
        if has_existing_connection and existing_method in {"web", "device"} and not same_identity and not edit:
            raise ValueError("当前通过授权流程已连接，母邮箱信息和授权方式为只读；请先点击“重新配置授权”")
        if has_existing_connection and existing_method == "manual" and not same_identity and not edit:
            raise ValueError("当前授权配置已连接，母邮箱信息为只读；请先点击“重新配置授权”")
        candidate = {
            **candidate_identity,
            "refresh_token": refresh_token.strip() or (existing.get("refresh_token", "") if same_identity else ""),
            "auth_method": "manual",
        }
        if not candidate["client_id"]:
            raise ValueError("必须提供 Microsoft Client ID")
        if not candidate["refresh_token"]:
            raise ValueError("必须提供 refresh token")
        validate_oauth_config(candidate)
        save_oauth_config(path, candidate)
    except (OAuthError, ValueError, OSError) as exc:
        return no_store(RedirectResponse(f"/admin/mailbox?error={str(exc)}", status_code=303))
    return no_store(RedirectResponse("/admin/mailbox", status_code=303))


def _oauth_redirect_uri(request: Request) -> str:
    configured = getattr(request.app.state, "oauth_redirect_uri", "")
    return configured or str(request.url_for("oauth_callback"))


def _oauth_secret(request: Request) -> str:
    instance = load_instance_secrets(request.app.state.instance_secrets_path)
    return str(instance["cookie_secret"])


def _mailbox_candidate(mailbox_address: str, client_id: str, authority: str) -> dict[str, str]:
    normalized = normalize_email_address(mailbox_address)
    client = client_id.strip()
    if not client:
        raise ValueError("必须提供 Microsoft Client ID")
    return {
        "mailbox_address": normalized,
        "client_id": client,
        "authority": authority.strip() or "consumers",
    }


def _existing_oauth_identity(request: Request) -> tuple[dict[str, str], str, bool]:
    path = request.app.state.microsoft_oauth_path
    existing = load_oauth_config(path) if path.exists() else {}
    identity = {
        "mailbox_address": str(existing.get("mailbox_address") or "").strip().casefold(),
        "client_id": str(existing.get("client_id") or "").strip(),
        "authority": str(existing.get("authority") or "consumers").strip() or "consumers",
    }
    return identity, str(existing.get("auth_method") or "manual"), bool(existing.get("refresh_token"))


def _validate_new_oauth_identity(request: Request, candidate: dict[str, str], *, edit: bool = False) -> None:
    existing, auth_method, connected = _existing_oauth_identity(request)
    if connected and auth_method in {"web", "device"} and not edit:
        raise ValueError("当前通过授权流程已连接，母邮箱信息和授权方式为只读；请先点击“重新配置授权”")
    if connected and auth_method == "manual" and candidate != existing and not edit:
        raise ValueError("当前手动授权配置已连接，母邮箱信息为只读；请先点击“重新配置授权”")


def _mailbox_error_page(request: Request, *, config: dict, interval_seconds: int, folder_names: str, error: str, csrf_token: str) -> Response:
    return set_csrf_cookie(
        request,
        no_store(
            templates.TemplateResponse(
                request,
                "admin/mother_mailbox.html",
                {
                    "config": config,
                    "interval_seconds": interval_seconds,
                    "folder_names": folder_names,
                    "sync_enabled": True,
                    "fields_read_only": False,
                    "can_edit_oauth": False,
                    "error": error,
                    "csrf_token": csrf_token,
                    "authorization_url": None,
                    "device_authorization": None,
                },
                status_code=400,
            )
        ),
        csrf_token,
    )


def _persist_oauth_result(request: Request, *, payload: dict, tokens: dict, auth_method: str) -> None:
    validate_access_token_for_mailbox(
        access_token=str(tokens.get("access_token") or ""),
        mailbox_address=payload["mailbox_address"],
    )
    config = oauth_config_from_tokens(
        mailbox_address=payload["mailbox_address"],
        client_id=payload["client_id"],
        authority=payload["authority"],
        token_payload=tokens,
        auth_method=auth_method,
    )
    save_oauth_config(request.app.state.microsoft_oauth_path, config)


@router.post("/oauth/web/start", name="oauth_web_start", dependencies=[Depends(require_admin)])
def oauth_web_start(
    request: Request,
    mailbox_address: str = Form(...),
    client_id: str = Form(...),
    authority: str = Form("consumers"),
    csrf_token: str = Form(...),
    edit: int = Query(default=0),
) -> Response:
    validate_csrf(request, csrf_token)
    try:
        candidate = _mailbox_candidate(mailbox_address, client_id, authority)
        _validate_new_oauth_identity(request, candidate, edit=bool(edit))
        verifier, challenge = generate_pkce_pair()
        state = uuid4().hex + uuid4().hex
        db = request.app.state.session_factory()
        try:
            create_transaction(
                db,
                flow_type="web",
                state=state,
                secret=_oauth_secret(request),
                payload={
                    **candidate,
                    "code_verifier": verifier,
                },
            )
            db.commit()
        finally:
            db.close()
        url = build_authorization_url(
            client_id=candidate["client_id"],
            authority=candidate["authority"],
            redirect_uri=_oauth_redirect_uri(request),
            state=state,
            code_challenge=challenge,
        )
        response = templates.TemplateResponse(
            request,
            "admin/mother_mailbox.html",
            {
                "config": {**candidate, "auth_method": "web", "refresh_token_configured": False},
                **_sync_context(request),
                "fields_read_only": False,
                "can_edit_oauth": False,
                "error": None,
                "csrf_token": csrf_token,
                "authorization_url": url,
                "device_authorization": None,
            },
        )
        response.set_cookie(
            OAUTH_STATE_COOKIE,
            state,
            httponly=True,
            samesite="lax",
            secure=not request.app.state.testing,
            max_age=900,
        )
        return set_csrf_cookie(request, no_store(response), csrf_token)
    except (ValueError, OSError) as exc:
        sync = _sync_context(request)
        return _mailbox_error_page(
            request,
            config={"mailbox_address": mailbox_address, "client_id": client_id, "authority": authority, "auth_method": "web"},
            interval_seconds=int(sync["interval_seconds"]),
            folder_names=str(sync["folder_names"]),
            error=str(exc),
            csrf_token=csrf_token,
        )


@router.get("/oauth/callback", name="oauth_callback", dependencies=[Depends(require_admin)])
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = "") -> Response:
    if error:
        return no_store(RedirectResponse(f"/admin/mailbox?oauth_error={error}", status_code=303))
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not code or not state or not cookie_state or state != cookie_state:
        raise HTTPException(status_code=400, detail="OAuth state 校验失败")

    db = request.app.state.session_factory()
    try:
        transaction = get_transaction_by_state(db, state)
        if transaction is None:
            raise HTTPException(status_code=400, detail="OAuth 授权请求不存在、已过期或已使用")
        payload = decode_transaction_payload(transaction, _oauth_secret(request))
        tokens = exchange_authorization_code(
            client_id=payload["client_id"],
            authority=payload["authority"],
            code=code,
            code_verifier=payload["code_verifier"],
            redirect_uri=_oauth_redirect_uri(request),
        )
        _persist_oauth_result(request, payload=payload, tokens=tokens, auth_method="web")
        consume_transaction(db, transaction)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except (OAuthError, ValueError, OSError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        db.close()
    response = RedirectResponse("/admin/mailbox", status_code=303)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    return no_store(response)


@router.post("/oauth/device/start", name="oauth_device_start", dependencies=[Depends(require_admin)])
def oauth_device_start(
    request: Request,
    mailbox_address: str = Form(...),
    client_id: str = Form(...),
    authority: str = Form("consumers"),
    csrf_token: str = Form(...),
    edit: int = Query(default=0),
) -> Response:
    validate_csrf(request, csrf_token)
    try:
        candidate = _mailbox_candidate(mailbox_address, client_id, authority)
        _validate_new_oauth_identity(request, candidate, edit=bool(edit))
        device = request_device_code(client_id=candidate["client_id"], authority=candidate["authority"])
        db = request.app.state.session_factory()
        try:
            transaction = create_transaction(
                db,
                flow_type="device",
                secret=_oauth_secret(request),
                payload={
                    **candidate,
                    "device_code": device["device_code"],
                    "user_code": device["user_code"],
                    "verification_uri": device["verification_uri"],
                    "message": device.get("message", ""),
                    "interval": device.get("interval", 5),
                },
            )
            db.commit()
        finally:
            db.close()
        token = csrf_context(request)["csrf_token"]
        sync = _sync_context(request)
        response = templates.TemplateResponse(
            request,
            "admin/mother_mailbox.html",
            {
                "config": {**candidate, "auth_method": "device", "refresh_token_configured": False},
                **sync,
                "fields_read_only": False,
                "can_edit_oauth": False,
                "error": None,
                "csrf_token": token,
                "authorization_url": None,
                "device_authorization": {
                    "transaction_id": transaction.transaction_id,
                    "user_code": device["user_code"],
                    "verification_uri": device["verification_uri"],
                    "message": device.get("message", ""),
                },
            },
        )
        return set_csrf_cookie(request, no_store(response), token)
    except (OAuthError, ValueError, OSError) as exc:
        sync = _sync_context(request)
        return _mailbox_error_page(
            request,
            config={"mailbox_address": mailbox_address, "client_id": client_id, "authority": authority, "auth_method": "device"},
            interval_seconds=int(sync["interval_seconds"]),
            folder_names=str(sync["folder_names"]),
            error=str(exc),
            csrf_token=csrf_token,
        )


@router.post("/oauth/device/confirm", name="oauth_device_confirm", dependencies=[Depends(require_admin)])
def oauth_device_confirm(request: Request, transaction_id: str = Form(...), csrf_token: str = Form(...)) -> Response:
    validate_csrf(request, csrf_token)
    db = request.app.state.session_factory()
    try:
        transaction = get_transaction(db, transaction_id, flow_type="device")
        if transaction is None:
            raise HTTPException(status_code=400, detail="Device Code 授权请求不存在、已过期或已完成")
        payload = decode_transaction_payload(transaction, _oauth_secret(request))
        tokens = poll_device_code(
            client_id=payload["client_id"],
            authority=payload["authority"],
            device_code=payload["device_code"],
        )
        _persist_oauth_result(request, payload=payload, tokens=tokens, auth_method="device")
        consume_transaction(db, transaction)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        if exc.__class__.__name__ in {"DeviceAuthorizationPending", "DeviceAuthorizationSlowDown"}:
            token = csrf_context(request)["csrf_token"]
            sync = _sync_context(request)
            response = templates.TemplateResponse(
                request,
                "admin/mother_mailbox.html",
                {
                    "config": {**payload, "auth_method": "device", "refresh_token_configured": False},
                    **sync,
                    "fields_read_only": False,
                    "can_edit_oauth": False,
                    "error": "微软授权尚未完成，请在另一设备完成登录后再次确认。",
                    "authorization_url": None,
                    "device_authorization": {
                        "transaction_id": transaction.transaction_id,
                        "user_code": payload.get("user_code", ""),
                        "verification_uri": payload.get("verification_uri", "https://microsoft.com/devicelogin"),
                        "message": payload.get("message", ""),
                    },
                },
                status_code=409,
            )
            return set_csrf_cookie(request, no_store(response), token)
        if isinstance(exc, (OAuthError, ValueError, OSError)):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise
    finally:
        db.close()
    return no_store(RedirectResponse("/admin/mailbox", status_code=303))
