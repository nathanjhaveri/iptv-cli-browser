from __future__ import annotations

import curses
import os
import textwrap
from dataclasses import dataclass

from .library import format_ffmpeg_command, normalize_name
from .models import Channel, Program
from .stream_tools import (
    StreamImage,
    StreamToolError,
    capture_stream_image,
    detect_terminal_image_protocol,
    kitty_delete_image_sequence,
    kitty_place_image_sequence,
    kitty_transmit_image_sequence,
    open_with_vlc,
    probe_stream_stats,
    terminal_cursor_position_sequence,
)


CTRL_C = 3
CTRL_E = 5
CTRL_F = 6
CTRL_O = 15
CTRL_P = 16
CTRL_R = 18
TERMINAL_IMAGE_ID = 2401
SEARCH_SCOPES = ("Channels", "EPG", "All")
DEFAULT_SEARCH_SCOPE_INDEX = 2


@dataclass
class BrowserResult:
    selected_command: str | None = None


@dataclass
class ProgramRow:
    channel: Channel
    program: Program


@dataclass(frozen=True, slots=True)
class PreviewPane:
    top: int
    left: int
    height: int
    width: int


class ChannelBrowser:
    def __init__(self, channels: list[Channel]) -> None:
        self.channels = channels
        self.filtered = list(channels)
        self.query = ""
        self.channel_index = 0
        self.program_index = 0
        self.program_mode = False
        self.search_scope_index = DEFAULT_SEARCH_SCOPE_INDEX
        self.channel_search_text_by_id: dict[str, str] = {}
        self.epg_search_entries_by_channel_id: dict[str, list[tuple[str, Program]]] = {}
        self.epg_match_by_channel_id: dict[str, Program] = {}
        self.result = BrowserResult()
        self.stream_status_by_channel_id: dict[str, list[str]] = {}
        self.stream_image_channel_id: str | None = None
        self.stream_image: StreamImage | None = None
        self.stream_image_generation = 0
        self.terminal_image_protocol = detect_terminal_image_protocol()
        self.terminal_image_transmitted_generation = -1
        self.terminal_image_visible = False
        self._build_search_index()

    def run(self) -> BrowserResult:
        curses.wrapper(self._main)
        return self.result

    def _apply_filter(self) -> None:
        self.epg_match_by_channel_id = {}
        if not self.query:
            self.filtered = list(self.channels)
        else:
            needle = normalize_name(self.query)
            items: list[Channel] = []
            for channel in self.channels:
                channel_matches = self._search_scope in {"Channels", "All"} and needle in self.channel_search_text_by_id[channel.stream_id]
                epg_match = self._first_epg_match(channel, needle) if self._search_scope in {"EPG", "All"} else None
                if epg_match:
                    self.epg_match_by_channel_id[channel.stream_id] = epg_match
                if channel_matches or epg_match:
                    items.append(channel)
            self.filtered = items
        if self.channel_index >= len(self.filtered):
            self.channel_index = max(0, len(self.filtered) - 1)
        if self.program_index >= len(self._selected_program_rows()):
            self.program_index = max(0, len(self._selected_program_rows()) - 1)

    @property
    def _search_scope(self) -> str:
        return SEARCH_SCOPES[self.search_scope_index]

    def _build_search_index(self) -> None:
        for channel in self.channels:
            channel_parts = [
                channel.name,
                channel.category_name or "",
                channel.current_program.title if channel.current_program else "",
            ]
            self.channel_search_text_by_id[channel.stream_id] = normalize_name(" ".join(channel_parts))
            self.epg_search_entries_by_channel_id[channel.stream_id] = [
                (normalize_name(f"{program.title} {program.description}"), program)
                for program in channel.epg_programs
            ]

    def _first_epg_match(self, channel: Channel, needle: str) -> Program | None:
        for search_text, program in self.epg_search_entries_by_channel_id.get(channel.stream_id, []):
            if needle in search_text:
                return program
        return None

    def _epg_match_for(self, channel: Channel) -> Program | None:
        return self.epg_match_by_channel_id.get(channel.stream_id)

    def _selected_channel(self) -> Channel | None:
        if not self.filtered:
            return None
        return self.filtered[self.channel_index]

    def _selected_program_rows(self) -> list[ProgramRow]:
        channel = self._selected_channel()
        if channel is None:
            return []
        if channel.epg_programs:
            return [ProgramRow(channel=channel, program=program) for program in channel.epg_programs]
        rows: list[ProgramRow] = []
        if channel.current_program:
            rows.append(ProgramRow(channel=channel, program=channel.current_program))
        rows.extend(ProgramRow(channel=channel, program=program) for program in channel.upcoming_programs)
        return rows

    def _selected_program_row(self) -> ProgramRow | None:
        rows = self._selected_program_rows()
        if not rows:
            return None
        return rows[self.program_index]

    def _main(self, stdscr: curses.window) -> None:
        curses.curs_set(1)
        stdscr.keypad(True)
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
            except curses.error:
                pass

        try:
            while True:
                self._draw(stdscr)
                key = stdscr.getch()
                if key == 27:
                    return
                if key == CTRL_C:
                    if self.query:
                        self.query = ""
                        self._apply_filter()
                        continue
                    return
                if key == CTRL_E:
                    self.search_scope_index = (self.search_scope_index + 1) % len(SEARCH_SCOPES)
                    self._apply_filter()
                    continue
                if key == CTRL_F:
                    self._show_stream_frame(stdscr)
                    continue
                if key == CTRL_O:
                    self._open_stream_in_vlc(stdscr)
                    continue
                if key == CTRL_P:
                    self._open_stream_in_vlc(stdscr)
                    continue
                if key == CTRL_R:
                    self._select_record_command()
                    continue
                if key == ord("\t"):
                    if self.program_mode:
                        self.program_mode = False
                    elif self._selected_program_rows():
                        self.program_mode = True
                        self.program_index = self._matched_program_index(self._selected_channel())
                    continue
                if key == curses.KEY_LEFT and self.program_mode:
                    self.program_mode = False
                    continue
                if key == curses.KEY_UP:
                    if self.program_mode:
                        self.program_index = max(0, self.program_index - 1)
                    else:
                        self.channel_index = max(0, self.channel_index - 1)
                    continue
                if key == curses.KEY_DOWN:
                    if self.program_mode:
                        self.program_index = min(
                            max(0, len(self._selected_program_rows()) - 1),
                            self.program_index + 1,
                        )
                    else:
                        self.channel_index = min(max(0, len(self.filtered) - 1), self.channel_index + 1)
                    continue
                if key in (curses.KEY_BACKSPACE, 127, 8):
                    self.query = self.query[:-1]
                    self._apply_filter()
                    continue
                if 32 <= key <= 126:
                    self.query += chr(key)
                    self._apply_filter()
        finally:
            self._delete_terminal_image()

    def _draw(self, stdscr: curses.window) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        detail_height = self._detail_height(height)
        list_top = 0
        detail_top = max(1, height - 2 - detail_height)
        list_bottom = detail_top - 1
        rows = max(1, list_bottom - list_top)
        start = 0
        preview_pane = self._preview_pane(height, width)
        list_width = max(1, width - 1)
        if preview_pane:
            list_width = max(1, preview_pane.left - 1)

        if self.program_mode:
            program_rows = self._selected_program_rows()
            if self.program_index >= rows:
                start = self.program_index - rows + 1
            visible_programs = program_rows[start : start + rows]
            for row, program_row in enumerate(visible_programs):
                idx = start + row
                prefix = "NOW" if idx == 0 and program_row.channel.current_program is program_row.program else "EPG"
                line = self._format_program_row(prefix, program_row.program)
                attr = curses.A_REVERSE if idx == self.program_index else curses.A_NORMAL
                stdscr.addnstr(list_top + row, 0, line, list_width, attr)
        else:
            if self.channel_index >= rows:
                start = self.channel_index - rows + 1
            visible = self.filtered[start : start + rows]
            for row, channel in enumerate(visible):
                idx = start + row
                current = channel.current_program.title if channel.current_program else ""
                line = channel.name
                if channel.epg_programs:
                    line = f"{line} [EPG {len(channel.epg_programs)}]"
                if current:
                    line = f"{line} | {current}"
                epg_match = self._epg_match_for(channel)
                if epg_match:
                    line = f"{line} | Match: {self._format_program_match(epg_match)}"
                attr = curses.A_REVERSE if idx == self.channel_index else curses.A_NORMAL
                stdscr.addnstr(list_top + row, 0, line, list_width, attr)

        self._draw_detail(stdscr, detail_top, 0, detail_height, width - 1)

        mode = "Programs" if self.program_mode else "Channels"
        if self.program_mode:
            total = len(self._selected_program_rows())
            position = min(total, self.program_index + 1) if total else 0
            count_label = f"{position}/{total}"
        else:
            count_label = f"{len(self.filtered)}/{len(self.channels)}"
        footer = (
            f"{mode}: {count_label}  "
            f"Search: {self._search_scope}  "
            "Ctrl-E: scope  Ctrl-R: command  Ctrl-F: frame/stats  Ctrl-P: VLC  Tab: programs  Ctrl-C: clear/quit"
        )
        stdscr.addnstr(height - 2, 0, footer, width - 1, curses.A_BOLD)
        prompt = f"Search ({self._search_scope}): {self.query}"
        stdscr.addnstr(height - 1, 0, prompt, width - 1)
        cursor = (height - 1, min(width - 1, len(prompt)))
        stdscr.move(*cursor)
        stdscr.refresh()
        self._render_terminal_image(preview_pane, cursor=cursor)

    def _draw_detail(
        self,
        stdscr: curses.window,
        top: int,
        left: int,
        height: int,
        width: int,
    ) -> None:
        bottom = top + max(1, height)
        channel = self._selected_channel()
        program_row = self._selected_program_row() if self.program_mode else None
        if channel is None:
            stdscr.addnstr(top, left, "No channels match the current filter.", width)
            return
        lines = [
            channel.name,
            f"Category: {channel.category_name or 'Uncategorized'}",
            f"Stream ID: {channel.stream_id}",
        ]
        if channel.epg_programs:
            lines.append(f"EPG: {len(channel.epg_programs)} current/future programs loaded")
        epg_match = self._epg_match_for(channel)
        if epg_match and not program_row:
            lines.append(f"Search match: {self._format_program_match(epg_match)}")
        detail_program = program_row.program if program_row else channel.current_program
        if detail_program:
            label = "Program" if program_row else "Now"
            lines.append(f"{label}: {detail_program.time_range} {detail_program.title}")
            if detail_program.description:
                lines.append(detail_program.description)
        else:
            lines.append("Now: No current program found")
        if not program_row and channel.upcoming_programs:
            lines.append("Next:")
            for program in channel.upcoming_programs:
                lines.append(f"{program.time_range} {program.title}")

        stream_status = self._stream_status_for(channel)
        if stream_status:
            lines.append("")
            lines.extend(stream_status)

        lines.append("")
        command = format_ffmpeg_command(channel, program=detail_program)
        lines.append("Record command:")
        lines.append(command)

        self._draw_detail_lines(stdscr, lines, top, left, height, width)

    def _draw_detail_lines(
        self,
        stdscr: curses.window,
        lines: list[str],
        top: int,
        left: int,
        height: int,
        width: int,
    ) -> None:
        bottom = top + max(1, height)
        row = top
        for line in lines:
            wrapped_lines = self._wrap_detail_line(line, width)
            for wrapped in wrapped_lines:
                if row >= bottom:
                    break
                stdscr.addnstr(row, left, wrapped, width)
                row += 1
            if row >= bottom:
                break

    def _wrap_detail_line(self, line: str, width: int) -> list[str]:
        if not line:
            return [""]
        if line.startswith("ffmpeg "):
            return self._wrap_shell_command(line, width)
        return textwrap.wrap(
            line,
            width=max(1, width),
            break_long_words=False,
            break_on_hyphens=False,
        ) or [line]

    def _wrap_shell_command(self, line: str, width: int) -> list[str]:
        available_width = max(8, width - 2)
        parts = line.split(" ")
        wrapped: list[str] = []
        current = ""

        for part in parts:
            candidate = part if not current else f"{current} {part}"
            if len(candidate) <= available_width:
                current = candidate
                continue
            if current:
                wrapped.append(f"{current} \\")
                current = f"  {part}"
            else:
                wrapped.append(f"{part} \\")
                current = "  "

        if current:
            wrapped.append(current)
        return wrapped or [line]

    def _format_program_row(self, prefix: str, program: Program) -> str:
        date_label = program.start.strftime("%a %m-%d")
        return f"{date_label} | {program.time_range} | {prefix} | {program.title}"

    def _format_program_match(self, program: Program) -> str:
        return f"{program.start.strftime('%a %m-%d')} {program.time_range} {program.title}"

    def _matched_program_index(self, channel: Channel | None) -> int:
        if channel is None:
            return 0
        matched = self._epg_match_for(channel)
        if matched is None:
            return 0
        for index, row in enumerate(self._selected_program_rows()):
            if row.program is matched:
                return index
        return 0

    def _select_record_command(self) -> None:
        if self.program_mode:
            selected = self._selected_program_row()
            if selected is None:
                return
            self.result.selected_command = format_ffmpeg_command(selected.channel, program=selected.program)
            self._set_stream_status(selected.channel, ["Record command selected; quit to print it."])
            return

        selected_channel = self._selected_channel()
        if selected_channel is None:
            return
        self.result.selected_command = format_ffmpeg_command(selected_channel)
        self._set_stream_status(selected_channel, ["Record command selected; quit to print it."])

    def _show_stream_frame(self, stdscr: curses.window) -> None:
        channel = self._selected_channel()
        if channel is None:
            return

        self._set_stream_image(channel, None)
        self._set_stream_status(channel, ["Stream: probing stats and capturing full-resolution frame..."])
        self._draw(stdscr)

        lines: list[str] = []
        try:
            stats = probe_stream_stats(channel.stream_url)
        except StreamToolError as exc:
            lines.append(f"Stats error: {exc}")
        else:
            lines.extend(stats.summary_lines())

        if self.terminal_image_protocol != "kitty":
            lines.insert(0, "Preview error: terminal image protocol is unavailable.")
        else:
            try:
                image = capture_stream_image(channel.stream_url)
            except StreamToolError as exc:
                lines.insert(0, f"Preview error: {exc}")
            else:
                self._set_stream_image(channel, image)
                lines.insert(0, f"Preview: {image.width}x{image.height} rendered in upper-right pane.")

        self._set_stream_status(channel, lines or ["No stream details available."])

    def _open_stream_in_vlc(self, stdscr: curses.window) -> None:
        channel = self._selected_channel()
        if channel is None:
            return

        self._set_stream_status(channel, ["VLC: launching stream..."])
        self._draw(stdscr)
        try:
            open_with_vlc(channel.stream_url)
        except StreamToolError as exc:
            self._set_stream_status(channel, [f"VLC error: {exc}"])
            return
        self._set_stream_status(channel, ["VLC: launched stream."])

    def _set_stream_status(self, channel: Channel, lines: list[str]) -> None:
        self.stream_status_by_channel_id[channel.stream_id] = lines

    def _stream_status_for(self, channel: Channel) -> list[str]:
        return self.stream_status_by_channel_id.get(channel.stream_id, [])

    def _set_stream_image(self, channel: Channel, image: StreamImage | None) -> None:
        self.stream_image_channel_id = channel.stream_id if image else None
        self.stream_image = image
        self.stream_image_generation += 1
        self.terminal_image_transmitted_generation = -1

    def _stream_image_for(self, channel: Channel) -> StreamImage | None:
        if self.stream_image_channel_id != channel.stream_id:
            return None
        return self.stream_image

    def _detail_height(self, terminal_height: int) -> int:
        return min(10, max(6, terminal_height // 3))

    def _preview_pane(self, terminal_height: int, terminal_width: int) -> PreviewPane | None:
        channel = self._selected_channel()
        if channel is None or self._stream_image_for(channel) is None:
            return None
        if self.terminal_image_protocol != "kitty" or terminal_width < 72 or terminal_height < 14:
            return None

        detail_top = max(1, terminal_height - 2 - self._detail_height(terminal_height))
        preview_height = max(1, detail_top - 1)
        preview_width = min(max(32, terminal_width // 2), terminal_width - 28)
        if preview_height < 4 or preview_width < 24:
            return None
        return PreviewPane(top=0, left=terminal_width - preview_width - 1, height=preview_height, width=preview_width)

    def _render_terminal_image(self, pane: PreviewPane | None, *, cursor: tuple[int, int]) -> None:
        channel = self._selected_channel()
        image = self._stream_image_for(channel) if channel else None
        if pane is None or image is None or self.terminal_image_protocol != "kitty":
            self._delete_terminal_image()
            return

        sequence = bytearray()
        if self.terminal_image_transmitted_generation != self.stream_image_generation:
            if self.terminal_image_visible:
                sequence.extend(kitty_delete_image_sequence(image_id=TERMINAL_IMAGE_ID))
            sequence.extend(terminal_cursor_position_sequence(pane.top, pane.left))
            sequence.extend(
                kitty_transmit_image_sequence(
                    image,
                    image_id=TERMINAL_IMAGE_ID,
                    columns=pane.width,
                    rows=pane.height,
                )
            )
            self.terminal_image_transmitted_generation = self.stream_image_generation
        else:
            sequence.extend(terminal_cursor_position_sequence(pane.top, pane.left))
            sequence.extend(
                kitty_place_image_sequence(
                    image_id=TERMINAL_IMAGE_ID,
                    columns=pane.width,
                    rows=pane.height,
                )
            )
        sequence.extend(terminal_cursor_position_sequence(*cursor))
        os.write(1, bytes(sequence))
        self.terminal_image_visible = True

    def _delete_terminal_image(self) -> None:
        if self.terminal_image_protocol != "kitty" or not self.terminal_image_visible:
            return
        os.write(1, kitty_delete_image_sequence(image_id=TERMINAL_IMAGE_ID))
        self.terminal_image_visible = False
        self.terminal_image_transmitted_generation = -1
