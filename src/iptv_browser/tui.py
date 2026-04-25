from __future__ import annotations

import curses
from dataclasses import dataclass

from .library import format_ffmpeg_command, normalize_name
from .models import Channel


@dataclass
class BrowserResult:
    selected_command: str | None = None


class ChannelBrowser:
    def __init__(self, channels: list[Channel]) -> None:
        self.channels = channels
        self.filtered = list(channels)
        self.query = ""
        self.index = 0
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
        if self.index >= len(self.filtered):
            self.index = max(0, len(self.filtered) - 1)

    def _main(self, stdscr: curses.window) -> None:
        curses.curs_set(1)
        stdscr.keypad(True)
        curses.use_default_colors()

        while True:
            self._draw(stdscr)
            key = stdscr.getch()
            if key in (ord("q"), 27):
                return
            if key == 3:
                if self.query:
                    self.query = ""
                    self._apply_filter()
                    continue
                return
            if key in (curses.KEY_UP, ord("k")):
                self.index = max(0, self.index - 1)
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                self.index = min(max(0, len(self.filtered) - 1), self.index + 1)
                continue
            if key in (curses.KEY_BACKSPACE, 127, 8):
                self.query = self.query[:-1]
                self._apply_filter()
                continue
            if key in (10, 13):
                if self.filtered:
                    self.result.selected_command = format_ffmpeg_command(self.filtered[self.index])
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
        if self.index >= rows:
            start = self.index - rows + 1

        visible = self.filtered[start : start + rows]
        for row, channel in enumerate(visible):
            idx = start + row
            current = channel.current_program.title if channel.current_program else ""
            line = channel.name
            if current:
                line = f"{line} | {current}"
            attr = curses.A_REVERSE if idx == self.index else curses.A_NORMAL
            stdscr.addnstr(list_top + row, 0, line, width - 1, attr)

        selected = self.filtered[self.index] if self.filtered else None
        self._draw_detail(stdscr, selected, detail_top, 0, detail_height, width - 1)

        footer = f"Channels: {len(self.filtered)}/{len(self.channels)}  Enter: refresh command  Ctrl-C: clear/quit"
        stdscr.addnstr(height - 2, 0, footer, width - 1, curses.A_BOLD)
        prompt = f"Search: {self.query}"
        stdscr.addnstr(height - 1, 0, prompt, width - 1)
        stdscr.move(height - 1, min(width - 1, len(prompt)))
        stdscr.refresh()

    def _draw_detail(
        self,
        stdscr: curses.window,
        channel: Channel | None,
        top: int,
        left: int,
        height: int,
        width: int,
    ) -> None:
        bottom = top + max(1, height)
        if channel is None:
            stdscr.addnstr(top, left, "No channels match the current filter.", width)
            return
        lines = [
            channel.name,
            f"Category: {channel.category_name or 'Uncategorized'}",
            f"Stream ID: {channel.stream_id}",
        ]
        if channel.current_program:
            lines.append(f"Now: {channel.current_program.time_range} {channel.current_program.title}")
            if channel.current_program.description:
                lines.append(channel.current_program.description)
        else:
            lines.append("Now: No current program found")
        if channel.upcoming_programs:
            lines.append("Next:")
            for program in channel.upcoming_programs:
                lines.append(f"{program.time_range} {program.title}")
        lines.append("")
        lines.append("ffmpeg:")
        command = self.result.selected_command or format_ffmpeg_command(channel)
        lines.append(command)

        row = top
        for line in lines:
            if row >= bottom:
                break
            stdscr.addnstr(row, left, line, width)
            row += 1
