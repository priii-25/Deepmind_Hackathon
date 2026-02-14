"""
Base model with tenant isolation. Every model inherits from this.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


def new_uuid():
    return str(uuid.uuid4())


class TenantBase(Base):
    """Abstract base with tenant_id on every row."""

    __abstract__ = True

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=new_uuid
    )
    tenant_id: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
