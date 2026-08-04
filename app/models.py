from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class PrivateTarget(Base):
    __tablename__ = "private_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    access_token: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sessions: Mapped[list["PublicSession"]] = relationship(back_populates="target")


class MailMessage(Base):
    __tablename__ = "mail_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    immutable_message_id: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    internet_message_id: Mapped[Optional[str]] = mapped_column(String(998), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mail_folders.id"), nullable=True)
    folder_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_archived_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    body_fetch_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    recipients: Mapped[list["MailRecipient"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
    folder: Mapped[Optional["MailFolder"]] = relationship(back_populates="messages")


class MailRecipient(Base):
    __tablename__ = "mail_recipients"
    __table_args__ = (
        UniqueConstraint("message_id", "normalized_email", "recipient_type"),
        Index("ix_mail_recipients_email_message", "normalized_email", "message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("mail_messages.id", ondelete="CASCADE"), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    recipient_type: Mapped[str] = mapped_column(String(3), nullable=False)

    message: Mapped[MailMessage] = relationship(back_populates="recipients")


class PublicSession(Base):
    __tablename__ = "public_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("private_targets.id", ondelete="CASCADE"), nullable=False)
    captcha_answer_mac: Mapped[str] = mapped_column(String(64), nullable=False)
    captcha_payload: Mapped[str] = mapped_column(Text, nullable=False, default="")
    captcha_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    target: Mapped[PrivateTarget] = relationship(back_populates="sessions")


class MailFolder(Base):
    __tablename__ = "mail_folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_folder_id: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    folder_name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mail_folders.id"), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_message_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    last_message_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    messages: Mapped[list[MailMessage]] = relationship(back_populates="folder")


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    setting_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    setting_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class OAuthTransaction(Base):
    __tablename__ = "oauth_transactions"
    __table_args__ = (UniqueConstraint("transaction_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    state_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    flow_type: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
