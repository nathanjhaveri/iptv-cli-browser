from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


LIVE_STREAMS_FILE = "xtream_live_streams.json"
CATEGORIES_FILE = "xtream_categories.json"
EPG_FILE = "epg.xml"


class XtreamConfigError(ValueError):
    """Raised when the Xtream configuration is incomplete."""


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def resolve_credentials(
    server: str | None,
    username: str | None,
    password: str | None,
) -> tuple[str, str, str]:
    server = server or os.getenv("IPTV_SERVER")
    username = username or os.getenv("IPTV_USERNAME")
    password = password or os.getenv("IPTV_PASSWORD")
    if not server or not username or not password:
        raise XtreamConfigError(
            "Xtream credentials are required. Set flags or IPTV_SERVER/IPTV_USERNAME/IPTV_PASSWORD."
        )
    return server.rstrip("/"), username, password


def xtream_url(server: str, username: str, password: str, *, action: str | None = None) -> str:
    query: dict[str, str] = {"username": username, "password": password}
    if action:
        query["action"] = action
    return f"{server}/player_api.php?{urlencode(query)}"


def xmltv_url(server: str, username: str, password: str) -> str:
    return f"{server}/xmltv.php?{urlencode({'username': username, 'password': password})}"


def fetch_text(url: str) -> str:
    with urlopen(url) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> Any:
    return json.loads(fetch_text(url))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def fetch_xtream_data(
    *,
    workdir: Path,
    server: str,
    username: str,
    password: str,
    refresh_epg: bool,
) -> dict[str, Path]:
    live_streams = fetch_json(xtream_url(server, username, password, action="get_live_streams"))
    categories = fetch_json(xtream_url(server, username, password, action="get_live_categories"))

    live_path = workdir / LIVE_STREAMS_FILE
    category_path = workdir / CATEGORIES_FILE
    epg_path = workdir / EPG_FILE

    write_json(live_path, live_streams)
    write_json(category_path, categories)

    if refresh_epg or not epg_path.exists():
        epg_path.write_text(fetch_text(xmltv_url(server, username, password)), encoding="utf-8")

    return {"live_streams": live_path, "categories": category_path, "epg": epg_path}
