"""
Meetings / calls — notetaker agent data.
"""

from datetime import datetime

from sqlalchemy import String, Text, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class Call(TenantBase):
    __tablename__ = "calls"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=True)
    meeting_link: Mapped[str] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String, nullable=True)  # zoom, teams, meet, etc.
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="scheduled"
    )  # scheduled, joining, recording, processing, completed, failed
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    transcript: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    action_items: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)
    meetingbaas_bot_id: Mapped[str] = mapped_column(String, nullable=True)
    call_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)


class CalendarEvent(TenantBase):
    __tablename__ = "calendar_events"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    meeting_link: Mapped[str] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String, nullable=True)
    calendar_provider: Mapped[str] = mapped_column(String, nullable=True)  # google, outlook
    event_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
