from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Program:
    title: str
    start: datetime
    stop: datetime
    description: str = ""

    @property
    def time_range(self) -> str:
        return f"{self.start.strftime('%H:%M')} - {self.stop.strftime('%H:%M')}"


@dataclass(slots=True)
class Channel:
    stream_id: str
    name: str
    category_id: str | None
    category_name: str | None
    stream_icon: str | None
    epg_channel_id: str | None
    stream_url: str
    current_program: Program | None = None
    upcoming_programs: list[Program] = field(default_factory=list)

