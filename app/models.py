from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class MotherMailbox(Base):
    __tablename__ = "mother_mailboxes"
    __table_args__ = (
        UniqueConstraint("normalized_email", name="uq_mother_mailboxes_normalized_email"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    authority: Mapped[str] = mapped_column(String(255), nullable=False, default="consumers")
    auth_method: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_sync_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    folders: Mapped[list["MailFolder"]] = relationship(back_populates="mother_mailbox")
    messages: Mapped[list["MailMessage"]] = relationship(back_populates="mother_mailbox")
    sync_runs: Mapped[list["SyncRun"]] = relationship(back_populates="mother_mailbox")
    oauth_transactions: Mapped[list["OAuthTransaction"]] = relationship(back_populates="mother_mailbox")


class SyncTrigger(Base):
    __tablename__ = "sync_triggers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    skip_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trigger_source: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")

    cycle: Mapped[Optional["SyncCycle"]] = relationship(back_populates="trigger", uselist=False)


class SyncCycle(Base):
    __tablename__ = "sync_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger_id: Mapped[int] = mapped_column(ForeignKey("sync_triggers.id"), nullable=False, unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    mailbox_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_mailbox_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_mailbox_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    trigger: Mapped[SyncTrigger] = relationship(back_populates="cycle")
    sync_runs: Mapped[list["SyncRun"]] = relationship(back_populates="cycle")


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
    __table_args__ = (
        UniqueConstraint("mother_mailbox_id", "immutable_message_id", name="uq_mail_messages_mailbox_immutable"),
        Index("ix_mail_messages_mother_mailbox_id", "mother_mailbox_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mother_mailbox_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mother_mailboxes.id"), nullable=True
    )
    immutable_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    internet_message_id: Mapped[Optional[str]] = mapped_column(String(998), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mail_folders.id"), nullable=True)
    folder_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_archived_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    body_fetch_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    mother_mailbox: Mapped[Optional[MotherMailbox]] = relationship(back_populates="messages")
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
    __table_args__ = (
        UniqueConstraint("mother_mailbox_id", "provider_folder_id", name="uq_mail_folders_mailbox_provider"),
        Index("ix_mail_folders_mother_mailbox_id", "mother_mailbox_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mother_mailbox_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mother_mailboxes.id"), nullable=True
    )
    provider_folder_id: Mapped[str] = mapped_column(String(512), nullable=False)
    folder_name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mail_folders.id"), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_message_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    last_message_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    mother_mailbox: Mapped[Optional[MotherMailbox]] = relationship(back_populates="folders")
    messages: Mapped[list[MailMessage]] = relationship(back_populates="folder")


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        Index("ix_sync_runs_mailbox_cycle_started", "mother_mailbox_id", "cycle_started_at", "started_at"),
        Index("uq_sync_runs_cycle_mailbox", "cycle_id", "mother_mailbox_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mother_mailbox_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mother_mailboxes.id"), nullable=True
    )
    cycle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sync_cycles.id"), nullable=True, index=True)
    cycle_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    mother_mailbox: Mapped[Optional[MotherMailbox]] = relationship(back_populates="sync_runs")
    cycle: Mapped[Optional[SyncCycle]] = relationship(back_populates="sync_runs")


class AppSetting(Base):
    __tablename__ = "app_settings"

    setting_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    setting_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class OAuthTransaction(Base):
    __tablename__ = "oauth_transactions"
    __table_args__ = (
        UniqueConstraint("transaction_id"),
        Index("ix_oauth_transactions_mother_mailbox_id", "mother_mailbox_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mother_mailbox_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mother_mailboxes.id"), nullable=True
    )
    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    state_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    flow_type: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    mother_mailbox: Mapped[Optional[MotherMailbox]] = relationship(back_populates="oauth_transactions")
