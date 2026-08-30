"""SQLAlchemy 2.0 ORM 모델. docs/reference/db-schema.md의 DDL을 그대로 옮긴다. 구현: T2.

주의: 여기서 컬럼을 바꾸면 반드시 alembic revision --autogenerate로 마이그레이션을 같이 만든다.
db-schema.md는 참고용 스냅샷일 뿐, 실제 스키마의 진실은 alembic/versions 히스토리다.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    phone_e164: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date)
    kakao_sub: Mapped[str | None] = mapped_column(Text, unique=True)
    tier: Mapped[str] = mapped_column(Text, nullable=False, server_default="FREE")
    storage_quota_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="209715200")
    storage_used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (Index(None, "user_id", "kind", "granted_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Recipient(Base):
    __tablename__ = "recipients"
    __table_args__ = (
        CheckConstraint("phone_e164 IS NOT NULL OR email IS NOT NULL", name="ck_recipients_phone_or_email"),
        Index(None, "owner_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    relation: Mapped[str | None] = mapped_column(Text)
    phone_e164: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    # 수신자가 이 발신자로부터의 모든 수신을 영구 거부한 경우
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Capsule(Base):
    __tablename__ = "capsules"
    __table_args__ = (Index(None, "owner_id", "status"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # 본문은 암호문으로만 저장 (절대 규칙 1)
    body_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    body_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    dek_wrapped: Mapped[bytes | None] = mapped_column(LargeBinary)  # KEK로 감싼 데이터키
    content_hash: Mapped[str | None] = mapped_column(Text)  # sealed 시점 SHA-256
    trigger_type: Mapped[str] = mapped_column(Text, nullable=False)  # SCHEDULED/PROGNOSIS/DEATH_CLAIM
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DRAFT")
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CapsuleTrigger(Base):
    __tablename__ = "capsule_triggers"
    __table_args__ = (
        Index(None, "next_fire_at", postgresql_where=text("next_fire_at IS NOT NULL")),
    )

    capsule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capsules.id", ondelete="CASCADE"), primary_key=True
    )
    fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recurrence_rule: Mapped[str | None] = mapped_column(Text)  # RFC5545 RRULE
    recurrence_until: Mapped[date | None] = mapped_column(Date)
    # 여명형(B) 안전장치 — 절대 규칙 4
    require_confirm: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    confirm_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_defer_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapsuleRecipient(Base):
    __tablename__ = "capsule_recipients"

    capsule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capsules.id", ondelete="CASCADE"), primary_key=True
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipients.id", ondelete="CASCADE"), primary_key=True
    )
    personal_note: Mapped[str | None] = mapped_column(Text)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (Index(None, "capsule_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    capsule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capsules.id", ondelete="CASCADE"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(Text, nullable=False)  # 버킷 내 경로
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    dek_wrapped: Mapped[bytes | None] = mapped_column(LargeBinary)
    sha256: Mapped[str | None] = mapped_column(Text)
    upload_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
    transcript: Mapped[str | None] = mapped_column(Text)  # Whisper 결과
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (Index(None, "status", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    capsule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capsules.id"), nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("recipients.id"), nullable=False)
    occurrence_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")  # 반복 발송 회차
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
    channel: Mapped[str | None] = mapped_column(Text)  # ALIMTALK/SMS/EMAIL
    access_token_hash: Mapped[str] = mapped_column(Text, nullable=False)  # 열람 링크 토큰의 해시만 저장
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # capsule:recipient:occurrence — 절대 규칙 2 (중복 발송 차단)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DeliveryEvent(Base):
    __tablename__ = "delivery_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False
    )
    event: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DeathClaim(Base):
    __tablename__ = "death_claims"
    __table_args__ = (Index(None, "subject_lookup_hash"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # 유족이 조회한 경우 대상자를 특정하기 위한 해시(원문 저장 안 함): sha256(이름|생년월일|휴대폰뒷4)
    subject_lookup_hash: Mapped[str] = mapped_column(Text, nullable=False)
    claimant_name: Mapped[str] = mapped_column(Text, nullable=False)
    claimant_phone: Mapped[str] = mapped_column(Text, nullable=False)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_key: Mapped[str | None] = mapped_column(Text)  # 증명서 오브젝트 키
    evidence_type: Mapped[str | None] = mapped_column(Text)  # E_CERT / SCAN
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="SUBMITTED")
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AiJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # WRITING_COACH/SAFETY_REVIEW/TRANSCRIBE
    ref_type: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="QUEUED")
    request: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)  # USER/RECIPIENT/ADMIN/SYSTEM
    actor_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


__all__ = [
    "AiJob",
    "AuditLog",
    "Capsule",
    "CapsuleRecipient",
    "CapsuleTrigger",
    "Consent",
    "DeathClaim",
    "Delivery",
    "DeliveryEvent",
    "MediaAsset",
    "Recipient",
    "User",
]
