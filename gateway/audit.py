"""SQLAlchemy audit store: one row per tool call, plus recent and summary queries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from gateway.models import ToolCallResult


class Base(DeclarativeBase):
    """Declarative base for the audit schema."""


class AuditRecord(Base):
    """One tool call as it happened, kept for later queries."""

    __tablename__ = "audit_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool: Mapped[str] = mapped_column(String(64), index=True)
    caller: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "caller": self.caller,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AuditStore:
    """Writes call results to the audit table and reads them back."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        Base.metadata.create_all(self.engine)

    @classmethod
    def from_url(cls, url: str) -> "AuditStore":
        """Build a store from a SQLAlchemy URL, creating the schema if needed."""
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
        return cls(engine)

    def record(self, result: ToolCallResult, caller: str) -> int:
        """Insert one call and return its audit id."""
        with Session(self.engine) as session:
            row = AuditRecord(
                tool=result.tool,
                caller=caller,
                status=result.status,
                latency_ms=result.latency_ms,
                error=result.error,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the newest calls first."""
        with Session(self.engine) as session:
            rows = session.scalars(
                select(AuditRecord).order_by(AuditRecord.id.desc()).limit(limit)
            ).all()
            return [row.as_dict() for row in rows]

    def summary(self) -> dict[str, Any]:
        """Count calls by status and report the median latency of successful calls."""
        with Session(self.engine) as session:
            by_status = {
                status: count
                for status, count in session.execute(
                    select(AuditRecord.status, func.count()).group_by(AuditRecord.status)
                ).all()
            }
            latencies = session.scalars(
                select(AuditRecord.latency_ms)
                .where(AuditRecord.status == "ok")
                .order_by(AuditRecord.latency_ms)
            ).all()
        median = 0.0
        if latencies:
            middle = len(latencies) // 2
            median = (
                latencies[middle]
                if len(latencies) % 2
                else (latencies[middle - 1] + latencies[middle]) / 2
            )
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "median_latency_ms": round(median, 2),
        }

    def close(self) -> None:
        self.engine.dispose()
