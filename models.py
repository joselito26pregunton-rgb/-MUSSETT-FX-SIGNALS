from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class Signal(Base):
    __tablename__ = "fx_signals"
    signal_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(10))
    direction: Mapped[str] = mapped_column(String(4))
    timeframe: Mapped[str] = mapped_column(String(10), index=True)
    price: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BotState(Base):
    __tablename__ = "fx_bot_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    update_offset: Mapped[int] = mapped_column(Integer, default=0)


class Notification(Base):
    __tablename__ = "fx_notifications"
    key: Mapped[str] = mapped_column(String(240), primary_key=True)
    signal_id: Mapped[str | None] = mapped_column(ForeignKey("fx_signals.signal_id"))
    kind: Mapped[str] = mapped_column(String(25))
    text: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    message_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "fx_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str | None] = mapped_column(ForeignKey("fx_signals.signal_id"))
    kind: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HypotheticalEntry(Base):
    __tablename__ = "fx_hypothetical_entries"
    signal_id: Mapped[str] = mapped_column(ForeignKey("fx_signals.signal_id"), primary_key=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
