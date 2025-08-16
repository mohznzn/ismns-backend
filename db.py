# db.py
import os
import uuid
from datetime import datetime, timedelta

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, ForeignKey, func, create_engine, Index
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
)

# ---------- Engine & Session ----------
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL manquant. Configure-le dans les variables d'environnement.")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,           # évite les connexions mortes
    future=True
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

# ---------- Base declarative ----------
class Base(DeclarativeBase):
    pass

def _uuid() -> str:
    return str(uuid.uuid4())

# ---------- Modèles ----------
class Qcm(Base):
    __tablename__ = "qcms"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # futur multi-tenant
    language: Mapped[str] = mapped_column(String(8), default="en")
    job_description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|published
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    skills_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # ['Fundamentals', ...] (JSON str)
    share_token: Mapped[str | None] = mapped_column(String(255), nullable=True)

    questions: Mapped[list["Question"]] = relationship(back_populates="qcm", cascade="all, delete-orphan")
    invites:   Mapped[list["Invite"]]   = relationship(back_populates="qcm", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    qcm_id: Mapped[str] = mapped_column(String(36), ForeignKey("qcms.id", ondelete="CASCADE"), index=True)
    skill: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)

    qcm: Mapped["Qcm"] = relationship(back_populates="questions")
    options: Mapped[list["Option"]] = relationship(back_populates="question", cascade="all, delete-orphan")

Index("ix_questions_qcm_skill", Question.qcm_id, Question.skill)


class Option(Base):
    __tablename__ = "options"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    question_id: Mapped[str] = mapped_column(String(36), ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)

    question: Mapped["Question"] = relationship(back_populates="options")


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    qcm_id: Mapped[str] = mapped_column(String(36), ForeignKey("qcms.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # token de partage (JWT ou opaque)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=0)  # 0 = illimité
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    qcm: Mapped["Qcm"] = relationship(back_populates="invites")


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    qcm_id: Mapped[str] = mapped_column(String(36), ForeignKey("qcms.id", ondelete="CASCADE"), index=True)
    invite_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("invites.id", ondelete="SET NULL"), nullable=True)
    candidate_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)         # 0..100 (ou nb correct)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)          # pour randomisation stable

class Answer(Base):
    __tablename__ = "answers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    attempt_id: Mapped[str] = mapped_column(String(36), ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[str] = mapped_column(String(36), ForeignKey("questions.id", ondelete="CASCADE"))
    option_id: Mapped[str] = mapped_column(String(36), ForeignKey("options.id", ondelete="CASCADE"))
    correct: Mapped[bool] = mapped_column(Boolean, default=False)

# ---------- Helpers ----------
def create_all_tables():
    Base.metadata.create_all(bind=engine)

def invite_is_valid(inv: Invite) -> bool:
    if inv.expires_at and datetime.utcnow() > inv.expires_at.replace(tzinfo=None):
        return False
    if inv.max_uses and inv.used_count >= inv.max_uses:
        return False
    return True
