from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from iptv_browser.models import Program
from iptv_browser.library import attach_epg, format_ffmpeg_command, load_channels, load_epg_snapshot
from iptv_browser.xtream import load_dotenv


class LibraryTests(unittest.TestCase):
    def test_load_dotenv_sets_missing_environment_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_path = root / ".env"
            env_path.write_text(
                'IPTV_SERVER=http://example.com:8080\nIPTV_USERNAME="demo"\nIPTV_PASSWORD=secret\n',
                encoding="utf-8",
            )
            for key in ("IPTV_SERVER", "IPTV_USERNAME", "IPTV_PASSWORD"):
                os.environ.pop(key, None)

            load_dotenv(env_path)

            self.assertEqual("http://example.com:8080", os.environ["IPTV_SERVER"])
            self.assertEqual("demo", os.environ["IPTV_USERNAME"])
            self.assertEqual("secret", os.environ["IPTV_PASSWORD"])

    def test_load_channels_builds_stream_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "xtream_live_streams.json").write_text(
                json.dumps(
                    [
                        {
                            "stream_id": 101,
                            "name": "News HD",
                            "category_id": "5",
                            "epg_channel_id": "news.hd",
                            "container_extension": "ts",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (root / "xtream_categories.json").write_text(
                json.dumps([{"category_id": "5", "category_name": "News"}]),
                encoding="utf-8",
            )
            channels = load_channels(root, server="http://example.com:8080", username="u", password="p")
            self.assertEqual(1, len(channels))
            self.assertEqual("http://example.com:8080/live/u/p/101.ts", channels[0].stream_url)
            self.assertEqual("News", channels[0].category_name)

    def test_load_epg_snapshot_and_attach(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            xml = """<?xml version="1.0" encoding="utf-8"?>
<tv>
  <channel id="news.hd"><display-name>News HD</display-name></channel>
  <programme start="20260425190000 +0000" stop="20260425200000 +0000" channel="news.hd"><title>Bulletin</title></programme>
  <programme start="20260425200000 +0000" stop="20260425210000 +0000" channel="news.hd"><title>Weather</title></programme>
</tv>
"""
            (root / "epg.xml").write_text(xml, encoding="utf-8")
            display_names, programs = load_epg_snapshot(
                root, now=datetime(2026, 4, 25, 19, 30, tzinfo=timezone.utc)
            )
            self.assertIn("news.hd", display_names)
            channels = [
                type("ChannelLike", (), {"name": "News HD", "epg_channel_id": "news.hd", "current_program": None, "upcoming_programs": []})()
            ]
            result = attach_epg(
                channels,
                display_names,
                programs,
                now=datetime(2026, 4, 25, 19, 30, tzinfo=timezone.utc),
            )
            self.assertEqual("Bulletin", result[0].current_program.title)
            self.assertEqual("Weather", result[0].upcoming_programs[0].title)

    def test_ffmpeg_command_uses_slugged_filename(self) -> None:
        channel = type(
            "ChannelLike",
            (),
            {
                "name": "ES| M.LALIGA 1 HD",
                "stream_url": "http://example/stream.ts",
                "current_program": None,
            },
        )()
        command = format_ffmpeg_command(
            channel,
            timestamp=datetime(2026, 4, 25, 11, 12, 13, tzinfo=timezone.utc),
        )
        self.assertIn('"2026-04-25T111213-es-m-laliga-1-hd.ts"', command)

    def test_ffmpeg_command_uses_epg_title_and_remaining_duration(self) -> None:
        channel = type(
            "ChannelLike",
            (),
            {
                "name": "ES| M.LALIGA 1 HD",
                "stream_url": "http://example/stream.ts",
                "current_program": Program(
                    title="El Clasico",
                    start=datetime(2026, 4, 25, 11, 0, 0, tzinfo=timezone.utc),
                    stop=datetime(2026, 4, 25, 13, 0, 0, tzinfo=timezone.utc),
                    description="",
                ),
            },
        )()
        command = format_ffmpeg_command(
            channel,
            timestamp=datetime(2026, 4, 25, 11, 12, 13, tzinfo=timezone.utc),
        )
        self.assertIn("-t 01:47:47", command)
        self.assertIn('"el-clasico-2026-04-25T1100-es-m-laliga-1-hd.ts"', command)


if __name__ == "__main__":
    unittest.main()
