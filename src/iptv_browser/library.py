from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import Channel, Program
from .xtream import CATEGORIES_FILE, EPG_FILE, LIVE_STREAMS_FILE

DEFAULT_RECORD_DURATION_SECONDS = 30 * 60


@dataclass(slots=True)
class LibraryInspection:
    live_stream_count: int
    live_streams_with_epg_channel_id: int
    category_count: int
    epg_channel_count: int
    epg_program_channel_count: int
    epg_program_entry_count: int
    channels_with_epg: int
    epg_is_stale: bool
    next_program_at: datetime | None


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_name(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value or "")
    return collapsed.strip().casefold()


def parse_xmltv_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H%M%S %z")


def provider_stream_url(server: str, username: str, password: str, stream_id: str, extension: str | None) -> str:
    ext = extension or "ts"
    return f"{server.rstrip('/')}/live/{username}/{password}/{stream_id}.{ext}"


def load_channels(workdir: Path, *, server: str | None = None, username: str | None = None, password: str | None = None) -> list[Channel]:
    live_path = workdir / LIVE_STREAMS_FILE
    category_path = workdir / CATEGORIES_FILE
    if not live_path.exists():
        raise FileNotFoundError(f"Missing {LIVE_STREAMS_FILE} in {workdir}")

    categories: dict[str, str] = {}
    if category_path.exists():
        for item in load_json(category_path):
            category_id = str(item.get("category_id", "")).strip()
            category_name = str(item.get("category_name", "")).strip()
            if category_id:
                categories[category_id] = category_name

    channels: list[Channel] = []
    for item in load_json(live_path):
        stream_id = str(item.get("stream_id", "")).strip()
        if not stream_id:
            continue
        category_id = str(item.get("category_id", "")).strip() or None
        epg_channel_id = str(item.get("epg_channel_id", "")).strip() or None
        direct_source = str(item.get("direct_source", "")).strip() or None
        extension = str(item.get("container_extension", "")).strip() or None
        if direct_source:
            stream_url = direct_source
        elif server and username and password:
            stream_url = provider_stream_url(server, username, password, stream_id, extension)
        else:
            stream_url = f"<missing credentials for stream {stream_id}>"
        channels.append(
            Channel(
                stream_id=stream_id,
                name=str(item.get("name", f"Stream {stream_id}")).strip(),
                category_id=category_id,
                category_name=categories.get(category_id or "", None),
                stream_icon=str(item.get("stream_icon", "")).strip() or None,
                epg_channel_id=epg_channel_id,
                stream_url=stream_url,
            )
        )
    return channels


def load_epg_snapshot(workdir: Path, *, now: datetime | None = None) -> tuple[dict[str, list[str]], dict[str, list[Program]]]:
    epg_path = workdir / EPG_FILE
    if not epg_path.exists():
        return {}, {}

    now = now or datetime.now().astimezone()
    display_names: dict[str, list[str]] = defaultdict(list)
    programs: dict[str, list[Program]] = defaultdict(list)

    for event, elem in ET.iterparse(epg_path, events=("end",)):
        if elem.tag == "channel":
            channel_id = elem.attrib.get("id", "").strip()
            if channel_id:
                for display in elem.findall("display-name"):
                    text = (display.text or "").strip()
                    if text:
                        display_names[channel_id].append(text)
            elem.clear()
            continue

        if elem.tag != "programme":
            continue

        channel_id = elem.attrib.get("channel", "").strip()
        start_raw = elem.attrib.get("start")
        stop_raw = elem.attrib.get("stop")
        if not channel_id or not start_raw or not stop_raw:
            elem.clear()
            continue

        try:
            start = parse_xmltv_timestamp(start_raw)
            stop = parse_xmltv_timestamp(stop_raw)
        except ValueError:
            elem.clear()
            continue

        if stop < now:
            elem.clear()
            continue

        if len(programs[channel_id]) >= 4:
            elem.clear()
            continue

        title = (elem.findtext("title") or "").strip() or "Untitled"
        description = (elem.findtext("desc") or "").strip()
        programs[channel_id].append(Program(title=title, start=start, stop=stop, description=description))
        elem.clear()

    return display_names, programs


def attach_epg(channels: list[Channel], display_names: dict[str, list[str]], programs: dict[str, list[Program]], *, now: datetime | None = None) -> list[Channel]:
    now = now or datetime.now().astimezone()
    channel_by_name: dict[str, list[str]] = defaultdict(list)
    for channel_id, names in display_names.items():
        for name in names:
            channel_by_name[normalize_name(name)].append(channel_id)

    for channel in channels:
        match_id = channel.epg_channel_id if channel.epg_channel_id in programs else None
        if not match_id:
            names_to_try = [channel.name]
            if channel.epg_channel_id:
                names_to_try.append(channel.epg_channel_id)
            for candidate in names_to_try:
                ids = channel_by_name.get(normalize_name(candidate), [])
                if ids:
                    match_id = ids[0]
                    break
        if not match_id:
            continue

        matched = programs.get(match_id, [])
        current: Program | None = None
        upcoming: list[Program] = []
        for program in matched:
            if program.start <= now <= program.stop and current is None:
                current = program
            elif program.start > now and len(upcoming) < 3:
                upcoming.append(program)
        channel.current_program = current
        channel.upcoming_programs = upcoming
    return channels


def load_library(
    workdir: Path,
    *,
    server: str | None = None,
    username: str | None = None,
    password: str | None = None,
    now: datetime | None = None,
) -> list[Channel]:
    channels = load_channels(workdir, server=server, username=username, password=password)
    display_names, programs = load_epg_snapshot(workdir, now=now)
    return attach_epg(channels, display_names, programs, now=now)


def inspect_library(
    workdir: Path,
    *,
    server: str | None = None,
    username: str | None = None,
    password: str | None = None,
    now: datetime | None = None,
) -> LibraryInspection:
    now = now or datetime.now().astimezone()

    live_stream_count = 0
    live_streams_with_epg_channel_id = 0
    category_count = 0
    channels: list[Channel] = []

    live_path = workdir / LIVE_STREAMS_FILE
    if live_path.exists():
        raw_channels = load_json(live_path)
        live_stream_count = len(raw_channels)
        live_streams_with_epg_channel_id = sum(1 for item in raw_channels if str(item.get("epg_channel_id", "")).strip())
        channels = load_channels(workdir, server=server, username=username, password=password)

    category_path = workdir / CATEGORIES_FILE
    if category_path.exists():
        category_count = len(load_json(category_path))

    display_names, programs = load_epg_snapshot(workdir, now=now)
    epg_channel_count = len(display_names)
    epg_program_channel_count = len(programs)
    epg_program_entry_count = sum(len(entries) for entries in programs.values())
    next_program_at = min((program.start for entries in programs.values() for program in entries), default=None)

    if channels:
        channels = attach_epg(channels, display_names, programs, now=now)
    channels_with_epg = sum(1 for channel in channels if channel.current_program or channel.upcoming_programs)

    epg_is_stale = epg_channel_count > 0 and epg_program_entry_count == 0

    return LibraryInspection(
        live_stream_count=live_stream_count,
        live_streams_with_epg_channel_id=live_streams_with_epg_channel_id,
        category_count=category_count,
        epg_channel_count=epg_channel_count,
        epg_program_channel_count=epg_program_channel_count,
        epg_program_entry_count=epg_program_entry_count,
        channels_with_epg=channels_with_epg,
        epg_is_stale=epg_is_stale,
        next_program_at=next_program_at,
    )


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def format_seconds_as_hms(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_ffmpeg_command(
    channel: Channel,
    *,
    program: Program | None = None,
    timestamp: datetime | None = None,
) -> str:
    timestamp = timestamp or datetime.now().astimezone()
    target_program = program or channel.current_program
    title_slug = slugify(channel.name) or "channel"
    parts: list[str] = []

    if target_program:
        program_slug = slugify(target_program.title)
        if program_slug:
            parts.append(program_slug)
        parts.append(target_program.start.astimezone(timestamp.tzinfo).strftime("%Y-%m-%dT%H%M"))
    else:
        parts.append(timestamp.strftime("%Y-%m-%dT%H%M%S"))

    parts.append(title_slug)
    filename = "-".join(parts) + ".ts"

    command_parts = [
        "ffmpeg",
        "-reconnect", "1",
        "-i", f'"{channel.stream_url}"',
    ]
    duration_seconds = DEFAULT_RECORD_DURATION_SECONDS
    if target_program:
        if program is not None:
            duration_seconds = int((target_program.stop - target_program.start).total_seconds())
        else:
            duration_seconds = int((target_program.stop - timestamp).total_seconds())
    if duration_seconds > 0:
        command_parts.extend(["-t", format_seconds_as_hms(duration_seconds)])
    command_parts.extend([
        "-map", "0",
        "-dn",
        "-fflags", "+discardcorrupt",
        "-c", "copy",
        "-f", "mpegts",
        f'"{filename}"',
    ])
    return " ".join(command_parts)
