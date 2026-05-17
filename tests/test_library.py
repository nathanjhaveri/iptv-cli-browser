from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from iptv_browser.models import Program
from iptv_browser.library import attach_epg, format_ffmpeg_command, inspect_library, load_channels, load_epg_snapshot
from iptv_browser.stream_tools import (
    StreamImage,
    detect_terminal_image_protocol,
    ffmpeg_image_command,
    ffprobe_command,
    kitty_delete_image_sequence,
    kitty_place_image_sequence,
    kitty_transmit_image_sequence,
    parse_png_dimensions,
    parse_ffprobe_stats,
    vlc_launch_command,
)
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

    def test_inspect_library_reports_stale_epg(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "xtream_live_streams.json").write_text(
                json.dumps([{"stream_id": 101, "name": "News HD", "epg_channel_id": "news.hd"}]),
                encoding="utf-8",
            )
            (root / "xtream_categories.json").write_text(json.dumps([]), encoding="utf-8")
            xml = """<?xml version="1.0" encoding="utf-8"?>
<tv>
  <channel id="news.hd"><display-name>News HD</display-name></channel>
  <programme start="20250425190000 +0000" stop="20250425200000 +0000" channel="news.hd"><title>Old Bulletin</title></programme>
</tv>
"""
            (root / "epg.xml").write_text(xml, encoding="utf-8")
            summary = inspect_library(root, now=datetime(2026, 4, 25, 19, 30, tzinfo=timezone.utc))
            self.assertEqual(1, summary.live_stream_count)
            self.assertEqual(1, summary.live_streams_with_epg_channel_id)
            self.assertEqual(1, summary.epg_channel_count)
            self.assertEqual(0, summary.epg_program_entry_count)
            self.assertEqual(0, summary.channels_with_epg)
            self.assertTrue(summary.epg_is_stale)

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
        self.assertIn('ffmpeg -reconnect 1 -i "http://example/stream.ts"', command)
        self.assertIn("-t 00:30:00", command)
        self.assertIn("-map 0 -dn -fflags +discardcorrupt -c copy -f mpegts", command)
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

    def test_ffmpeg_command_can_target_upcoming_program(self) -> None:
        current_program = Program(
            title="Current Show",
            start=datetime(2026, 4, 25, 11, 0, 0, tzinfo=timezone.utc),
            stop=datetime(2026, 4, 25, 13, 0, 0, tzinfo=timezone.utc),
            description="",
        )
        upcoming_program = Program(
            title="Next Match",
            start=datetime(2026, 4, 25, 13, 0, 0, tzinfo=timezone.utc),
            stop=datetime(2026, 4, 25, 15, 0, 0, tzinfo=timezone.utc),
            description="",
        )
        channel = type(
            "ChannelLike",
            (),
            {
                "name": "Sports 1",
                "stream_url": "http://example/stream.ts",
                "current_program": current_program,
            },
        )()
        command = format_ffmpeg_command(
            channel,
            program=upcoming_program,
            timestamp=datetime(2026, 4, 25, 11, 12, 13, tzinfo=timezone.utc),
        )
        self.assertIn("-t 02:00:00", command)
        self.assertIn('"next-match-2026-04-25T1300-sports-1.ts"', command)

    def test_parse_ffprobe_stats_summarizes_video_audio_and_container(self) -> None:
        stats = parse_ffprobe_stats(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 1920,
                        "height": 1080,
                        "avg_frame_rate": "30000/1001",
                        "bit_rate": "4500000",
                    },
                    {
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "bit_rate": "128000",
                    },
                ],
                "format": {"bit_rate": "4800000", "duration": "3600.5"},
            }
        )

        self.assertEqual("1920x1080", stats.resolution)
        self.assertEqual(
            [
                "Resolution: 1920x1080",
                "Video: h264, 29.97 fps, 4.50 Mbps",
                "Audio: aac, 128 kbps",
                "Container bitrate: 4.80 Mbps",
                "Duration: 1:00:00",
            ],
            stats.summary_lines(),
        )

    def test_stream_tool_commands_use_urls_as_arguments(self) -> None:
        stream_url = "http://example.com/live/u/p/101.ts"
        self.assertEqual(stream_url, ffprobe_command(stream_url)[-1])
        image_command = ffmpeg_image_command(stream_url)
        self.assertIn(stream_url, image_command)
        self.assertEqual("0:v:0", image_command[image_command.index("-map") + 1])
        self.assertEqual("image2pipe", image_command[image_command.index("-f") + 1])
        self.assertEqual("png", image_command[image_command.index("-vcodec") + 1])
        self.assertEqual("pipe:1", image_command[-1])
        self.assertEqual(["open", "-a", "VLC", stream_url], vlc_launch_command(stream_url, system="Darwin"))
        self.assertEqual(["vlc", stream_url], vlc_launch_command(stream_url, system="Linux"))

    def test_png_dimensions_and_terminal_image_sequences(self) -> None:
        png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x02"
            b"\x00\x00\x00\x03"
            b"\x08\x02\x00\x00\x00"
            b"\x00\x00\x00\x00"
        )
        image = StreamImage(width=2, height=3, data=png)

        self.assertEqual((2, 3), parse_png_dimensions(png))
        transmit = kitty_transmit_image_sequence(image, image_id=7, columns=40, rows=10)
        self.assertTrue(transmit.startswith(b"\x1b_Ga=T,f=100,i=7,c=40,r=10,q=2"))
        self.assertIn(b";iVBOR", transmit)
        self.assertEqual(b"\x1b_Ga=p,i=7,c=40,r=10,q=2\x1b\\", kitty_place_image_sequence(image_id=7, columns=40, rows=10))
        self.assertEqual(b"\x1b_Ga=d,d=i,i=7,q=2\x1b\\", kitty_delete_image_sequence(image_id=7))

    def test_terminal_image_protocol_detection(self) -> None:
        self.assertEqual("kitty", detect_terminal_image_protocol({"TERM_PROGRAM": "ghostty"}))
        self.assertEqual("kitty", detect_terminal_image_protocol({"KITTY_WINDOW_ID": "1"}))
        self.assertIsNone(detect_terminal_image_protocol({"TERM_PROGRAM": "Apple_Terminal"}))


if __name__ == "__main__":
    unittest.main()
