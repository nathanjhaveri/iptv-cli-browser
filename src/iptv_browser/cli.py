from __future__ import annotations

import argparse
from pathlib import Path

from .library import inspect_library, load_library
from .tui import ChannelBrowser
from .xtream import CATEGORIES_FILE, EPG_FILE, LIVE_STREAMS_FILE, XtreamConfigError, fetch_xtream_data, load_dotenv, resolve_credentials


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iptv", description="Browse Xtream IPTV metadata and emit ffmpeg commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch Xtream channels and EPG into the current directory.")
    fetch_parser.add_argument("--server")
    fetch_parser.add_argument("--username")
    fetch_parser.add_argument("--password")
    fetch_parser.add_argument("--refresh-epg", action="store_true", help="Force-refresh epg.xml even if it exists.")

    browse_parser = subparsers.add_parser("browse", help="Browse existing local IPTV metadata.")
    browse_parser.add_argument("--server")
    browse_parser.add_argument("--username")
    browse_parser.add_argument("--password")

    inspect_parser = subparsers.add_parser("inspect", help="Print local metadata status.")
    inspect_parser.add_argument("--server")
    inspect_parser.add_argument("--username")
    inspect_parser.add_argument("--password")

    return parser


def cmd_fetch(args: argparse.Namespace) -> int:
    try:
        server, username, password = resolve_credentials(args.server, args.username, args.password)
    except XtreamConfigError as exc:
        print(exc)
        return 2

    paths = fetch_xtream_data(
        workdir=Path.cwd(),
        server=server,
        username=username,
        password=password,
        refresh_epg=args.refresh_epg,
    )
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


def cmd_browse(args: argparse.Namespace) -> int:
    try:
        server, username, password = resolve_credentials(args.server, args.username, args.password)
    except XtreamConfigError:
        server = username = password = None

    try:
        channels = load_library(Path.cwd(), server=server, username=username, password=password)
    except FileNotFoundError as exc:
        print(f"{exc}. Run `iptv fetch` first.")
        return 2

    try:
        result = ChannelBrowser(channels).run()
    except KeyboardInterrupt:
        return 0
    if result.selected_command:
        print(result.selected_command)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    workdir = Path.cwd()
    for filename in (LIVE_STREAMS_FILE, CATEGORIES_FILE, EPG_FILE):
        path = workdir / filename
        state = "present" if path.exists() else "missing"
        print(f"{filename}: {state}")

    try:
        server, username, password = resolve_credentials(args.server, args.username, args.password)
    except XtreamConfigError:
        server = username = password = None

    try:
        summary = inspect_library(workdir, server=server, username=username, password=password)
    except FileNotFoundError:
        return 0

    print(f"channels: {summary.live_stream_count}")
    print(f"channels_with_epg_channel_id: {summary.live_streams_with_epg_channel_id}")
    print(f"categories: {summary.category_count}")
    print(f"epg_channels: {summary.epg_channel_count}")
    print(f"epg_channels_with_programs: {summary.epg_program_channel_count}")
    print(f"epg_program_entries: {summary.epg_program_entry_count}")
    print(f"channels_with_matched_epg: {summary.channels_with_epg}")
    print(f"epg_is_stale: {'yes' if summary.epg_is_stale else 'no'}")
    print(
        "next_epg_program_at: "
        f"{summary.next_program_at.isoformat() if summary.next_program_at else 'none'}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path.cwd() / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "fetch":
        return cmd_fetch(args)
    if args.command == "browse":
        return cmd_browse(args)
    if args.command == "inspect":
        return cmd_inspect(args)
    parser.error(f"Unknown command: {args.command}")
    return 2
