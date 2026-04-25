from __future__ import annotations

import curses
from dataclasses import dataclass

from .library import format_ffmpeg_command, normalize_name
from .models import Channel, Program


@dataclass
class BrowserResult:
    selected_command: str | None = None


@dataclass
class ProgramRow:
    channel: Channel
    program: Program


class ChannelBrowser:
    def __init__(self, channels: list[Channel]) -> None:
        self.channels = channels
        self.filtered = list(channels)
        self.query = ""
        self.channel_index = 0
        self.program_index = 0
        self.program_mode = False
        self.result = BrowserResult()

    def run(self) -> BrowserResult:
        curses.wrapper(self._main)
        return self.result

    def _apply_filter(self) -> None:
        if not self.query:
            self.filtered = list(self.channels)
        else:
            needle = normalize_name(self.query)
            items: list[Channel] = []
            for channel in self.channels:
                haystacks = [
                    channel.name,
                    channel.category_name or "",
                    channel.current_program.title if channel.current_program else "",
                ]
                if any(needle in normalize_name(part) for part in haystacks):
                    items.append(channel)
            self.filtered = items
        if self.channel_index >= len(self.filtered):
            self.channel_index = max(0, len(self.filtered) - 1)
        if self.program_index >= len(self._selected_program_rows()):
            self.program_index = max(0, len(self._selected_program_rows()) - 1)

    def _selected_channel(self) -> Channel | None:
        if not self.filtered:
            return None
        return self.filtered[self.channel_index]

    def _selected_program_rows(self) -> list[ProgramRow]:
        channel = self._selected_channel()
        if channel is None:
            return []
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
        curses.use_default_colors()

        while True:
            self._draw(stdscr)
            key = stdscr.getch()
            if key == 27:
                return
            if key == 3:
                if self.query:
                    self.query = ""
                    self._apply_filter()
                    continue
                return
            if key == ord("\t"):
                if self.program_mode:
                    self.program_mode = False
                elif self._selected_program_rows():
                    self.program_mode = True
                    self.program_index = 0
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
                    self.program_index = min(max(0, len(self._selected_program_rows()) - 1), self.program_index + 1)
                else:
                    self.channel_index = min(max(0, len(self.filtered) - 1), self.channel_index + 1)
                continue
            if key in (curses.KEY_BACKSPACE, 127, 8):
                self.query = self.query[:-1]
                self._apply_filter()
                continue
            if key in (10, 13):
                if self.program_mode:
                    selected = self._selected_program_row()
                    if selected:
                        self.result.selected_command = format_ffmpeg_command(selected.channel, program=selected.program)
                else:
                    selected = self._selected_channel()
                    if selected:
                        self.result.selected_command = format_ffmpeg_command(selected)
                continue
            if 32 <= key <= 126:
                self.query += chr(key)
                self._apply_filter()

    def _draw(self, stdscr: curses.window) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        detail_height = min(9, max(6, height // 3))
        list_top = 0
        detail_top = max(1, height - 2 - detail_height)
        list_bottom = detail_top - 1
        rows = max(1, list_bottom - list_top)
        start = 0
        if self.program_mode:
            program_rows = self._selected_program_rows()
            if self.program_index >= rows:
                start = self.program_index - rows + 1
            visible_programs = program_rows[start : start + rows]
            for row, program_row in enumerate(visible_programs):
                idx = start + row
                prefix = "NOW" if idx == 0 and program_row.channel.current_program is program_row.program else "EPG"
                line = f"{program_row.program.time_range} | {prefix} | {program_row.program.title}"
                attr = curses.A_REVERSE if idx == self.program_index else curses.A_NORMAL
                stdscr.addnstr(list_top + row, 0, line, width - 1, attr)
        else:
            if self.channel_index >= rows:
                start = self.channel_index - rows + 1
            visible = self.filtered[start : start + rows]
            for row, channel in enumerate(visible):
                idx = start + row
                current = channel.current_program.title if channel.current_program else ""
                line = channel.name
                if current:
                    line = f"{line} | {current}"
                attr = curses.A_REVERSE if idx == self.channel_index else curses.A_NORMAL
                stdscr.addnstr(list_top + row, 0, line, width - 1, attr)

        self._draw_detail(stdscr, detail_top, 0, detail_height, width - 1)

        mode = "Programs" if self.program_mode else "Channels"
        footer = f"{mode}: {len(self._selected_program_rows()) if self.program_mode else len(self.filtered)}/{len(self.channels) if not self.program_mode else len(self._selected_program_rows())}  Enter: refresh command  Tab: toggle programs  Ctrl-C: clear/quit"
        stdscr.addnstr(height - 2, 0, footer, width - 1, curses.A_BOLD)
        prompt = f"Search: {self.query}"
        stdscr.addnstr(height - 1, 0, prompt, width - 1)
        stdscr.move(height - 1, min(width - 1, len(prompt)))
        stdscr.refresh()

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
        lines.append("")
        command = self.result.selected_command or format_ffmpeg_command(channel, program=detail_program)
        lines.append(command)
        lines.append(f'vlc "{channel.stream_url}"')

        row = top
        for line in lines:
            if row >= bottom:
                break
            stdscr.addnstr(row, left, line, width)
            row += 1
