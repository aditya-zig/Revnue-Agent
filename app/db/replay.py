from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.tables import Base


class MerchantReplayControl(Base):
    """Mutable control-plane pointer for a deterministic replay namespace.

    Replay evidence itself remains in the normal append-only PaymentEvent,
    RecoveryCase and AuditEvent tables. A reset advances ``generation`` and
    points the UI/controller at a fresh run instead of deleting old evidence.
    """

    __tablename__ = "merchant_replay_controls"

    replay_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=47)
    scenario: Mapped[str] = mapped_column(String(32), nullable=False, default="primary")
    cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="reset")
    active_run_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
