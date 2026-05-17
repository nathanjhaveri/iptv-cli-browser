from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from typing import Any


class StreamToolError(RuntimeError):
    """Raised when an external stream tool cannot complete the requested action."""


@dataclass(slots=True)
class StreamStats:
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: str | None = None
    video_bit_rate: int | None = None
    audio_bit_rate: int | None = None
    format_bit_rate: int | None = None
    duration_seconds: float | None = None

    @property
    def resolution(self) -> str | None:
        if self.width is None or self.height is None:
            return None
        return f"{self.width}x{self.height}"

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.resolution:
            lines.append(f"Resolution: {self.resolution}")

        video_parts = [self.video_codec] if self.video_codec else []
        if self.frame_rate:
            video_parts.append(self.frame_rate)
        video_bitrate = format_bit_rate(self.video_bit_rate)
        if video_bitrate:
            video_parts.append(video_bitrate)
        if video_parts:
            lines.append(f"Video: {', '.join(video_parts)}")

        audio_parts = [self.audio_codec] if self.audio_codec else []
        audio_bitrate = format_bit_rate(self.audio_bit_rate)
        if audio_bitrate:
            audio_parts.append(audio_bitrate)
        if audio_parts:
            lines.append(f"Audio: {', '.join(audio_parts)}")

        container_bitrate = format_bit_rate(self.format_bit_rate)
        if container_bitrate:
            lines.append(f"Container bitrate: {container_bitrate}")
        if self.duration_seconds is not None:
            lines.append(f"Duration: {format_duration(self.duration_seconds)}")
        return lines or ["No stream stats returned."]


@dataclass(slots=True)
class StreamImage:
    width: int
    height: int
    data: bytes


def ffprobe_command(stream_url: str) -> list[str]:
    return [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,bit_rate:format=duration,bit_rate",
        "-of",
        "json",
        stream_url,
    ]


def ffmpeg_image_command(stream_url: str) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        stream_url,
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]


def vlc_launch_command(stream_url: str, *, system: str | None = None) -> list[str]:
    system = system or platform.system()
    if system == "Darwin":
        return ["open", "-n", "-a", "VLC", "--args", stream_url]
    return ["vlc", stream_url]


def probe_stream_stats(stream_url: str, *, timeout_seconds: int = 20) -> StreamStats:
    _require_binary("ffprobe")
    result = _run(ffprobe_command(stream_url), timeout_seconds=timeout_seconds)
    return parse_ffprobe_stats(result.stdout)


def capture_stream_image(
    stream_url: str,
    *,
    timeout_seconds: int = 30,
) -> StreamImage:
    _require_binary("ffmpeg")
    result = _run_bytes(ffmpeg_image_command(stream_url), timeout_seconds=timeout_seconds)
    width, height = parse_png_dimensions(result.stdout)
    return StreamImage(width=width, height=height, data=result.stdout)


def detect_terminal_image_protocol(environ: dict[str, str] | None = None) -> str | None:
    environ = environ or os.environ
    term_program = environ.get("TERM_PROGRAM", "").casefold()
    if environ.get("KITTY_WINDOW_ID") or term_program in {"ghostty", "kitty"}:
        return "kitty"
    if environ.get("WEZTERM_PANE"):
        return "kitty"
    return None


def kitty_transmit_image_sequence(
    image: StreamImage,
    *,
    image_id: int,
    columns: int,
    rows: int,
) -> bytes:
    return _kitty_graphics_sequence(
        image.data,
        first_control=f"a=T,f=100,i={image_id},c={columns},r={rows},q=2",
    )


def kitty_place_image_sequence(*, image_id: int, columns: int, rows: int) -> bytes:
    return f"\x1b_Ga=p,i={image_id},c={columns},r={rows},q=2\x1b\\".encode("ascii")


def kitty_delete_image_sequence(*, image_id: int) -> bytes:
    return f"\x1b_Ga=d,d=i,i={image_id},q=2\x1b\\".encode("ascii")


def terminal_cursor_position_sequence(row: int, column: int) -> bytes:
    return f"\x1b[{row + 1};{column + 1}H".encode("ascii")


def parse_png_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[12:16] != b"IHDR":
        raise StreamToolError("ffmpeg did not return a PNG frame.")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    if width <= 0 or height <= 0:
        raise StreamToolError("ffmpeg returned an invalid PNG frame.")
    return width, height


def open_with_vlc(stream_url: str) -> None:
    command = vlc_launch_command(stream_url)
    if command[0] == "open":
        _run_launcher(command)
        return
    _require_binary(command[0])
    _launch(command)


def parse_ffprobe_stats(payload: str | dict[str, Any]) -> StreamStats:
    data = json.loads(payload) if isinstance(payload, str) else payload
    streams = data.get("streams") or []
    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")
    format_info = data.get("format") or {}
    frame_rate = None
    if video:
        frame_rate = _format_frame_rate(_string_or_none(video.get("avg_frame_rate")))
        if frame_rate is None:
            frame_rate = _format_frame_rate(_string_or_none(video.get("r_frame_rate")))

    return StreamStats(
        video_codec=_string_or_none(video.get("codec_name")) if video else None,
        audio_codec=_string_or_none(audio.get("codec_name")) if audio else None,
        width=_int_or_none(video.get("width")) if video else None,
        height=_int_or_none(video.get("height")) if video else None,
        frame_rate=frame_rate,
        video_bit_rate=_int_or_none(video.get("bit_rate")) if video else None,
        audio_bit_rate=_int_or_none(audio.get("bit_rate")) if audio else None,
        format_bit_rate=_int_or_none(format_info.get("bit_rate")),
        duration_seconds=_float_or_none(format_info.get("duration")),
    )


def format_bit_rate(value: int | None) -> str | None:
    if value is None:
        return None
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} Mbps"
    if value >= 1_000:
        return f"{value / 1_000:.0f} kbps"
    return f"{value} bps"


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _first_stream(streams: list[dict[str, Any]], codec_type: str) -> dict[str, Any] | None:
    for stream in streams:
        if stream.get("codec_type") == codec_type:
            return stream
    return None


def _format_frame_rate(value: str | None) -> str | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        try:
            rate = float(value)
        except ValueError:
            return None
    if rate <= 0:
        return None
    if rate.is_integer():
        return f"{int(rate)} fps"
    return f"{rate:.2f} fps"


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "N/A":
        return None
    return text


def _int_or_none(value: Any) -> int | None:
    text = _string_or_none(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _float_or_none(value: Any) -> float | None:
    text = _string_or_none(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _require_binary(binary: str) -> None:
    if shutil.which(binary) is None:
        raise StreamToolError(f"`{binary}` was not found on PATH.")


def _run(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise StreamToolError(f"`{command[0]}` timed out after {timeout_seconds}s.") from exc
    except subprocess.CalledProcessError as exc:
        detail = _error_detail(exc.stderr or exc.stdout or "")
        if detail:
            raise StreamToolError(f"`{command[0]}` failed: {detail}") from exc
        raise StreamToolError(f"`{command[0]}` failed with exit code {exc.returncode}.") from exc
    except FileNotFoundError as exc:
        raise StreamToolError(f"`{command[0]}` was not found on PATH.") from exc


def _run_bytes(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise StreamToolError(f"`{command[0]}` timed out after {timeout_seconds}s.") from exc
    except subprocess.CalledProcessError as exc:
        detail = _error_detail((exc.stderr or exc.stdout or b"").decode("utf-8", errors="replace"))
        if detail:
            raise StreamToolError(f"`{command[0]}` failed: {detail}") from exc
        raise StreamToolError(f"`{command[0]}` failed with exit code {exc.returncode}.") from exc
    except FileNotFoundError as exc:
        raise StreamToolError(f"`{command[0]}` was not found on PATH.") from exc


def _run_launcher(command: list[str]) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise StreamToolError(f"`{command[0]}` timed out while launching.") from exc
    except subprocess.CalledProcessError as exc:
        detail = _error_detail(exc.stderr or "")
        if detail:
            raise StreamToolError(detail) from exc
        raise StreamToolError(f"`{command[0]}` failed with exit code {exc.returncode}.") from exc
    except FileNotFoundError as exc:
        raise StreamToolError(f"`{command[0]}` was not found on PATH.") from exc


def _launch(command: list[str]) -> None:
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise StreamToolError(f"`{command[0]}` was not found on PATH.") from exc


def _error_detail(value: str, *, limit: int = 300) -> str:
    detail = " ".join(value.split())
    if len(detail) <= limit:
        return detail
    return f"{detail[: limit - 3]}..."


def _kitty_graphics_sequence(payload: bytes, *, first_control: str, chunk_size: int = 4096) -> bytes:
    encoded = base64.standard_b64encode(payload)
    parts: list[bytes] = []
    for offset in range(0, len(encoded), chunk_size):
        chunk = encoded[offset : offset + chunk_size]
        more = offset + chunk_size < len(encoded)
        if offset == 0:
            control = f"{first_control},m={1 if more else 0}"
        else:
            control = f"m={1 if more else 0}"
        parts.append(b"\x1b_G" + control.encode("ascii") + b";" + chunk + b"\x1b\\")
    return b"".join(parts)
