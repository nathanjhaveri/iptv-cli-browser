# IPTV Browser

Local CLI for browsing Xtream IPTV metadata and printing `ffmpeg` commands.

## Commands

```bash
PYTHONPATH=src python -m iptv_browser fetch
PYTHONPATH=src python -m iptv_browser browse
PYTHONPATH=src python -m iptv_browser inspect
```

If `.env` exists in the current directory, it is loaded automatically before argument parsing. Supported keys:

```bash
IPTV_SERVER=http://example.com:8080
IPTV_USERNAME=USER
IPTV_PASSWORD=PASS
```

The tool reads and writes metadata files in the current working directory:

- `xtream_live_streams.json`
- `xtream_categories.json`
- `epg.xml`

It never plays or downloads streams. Selecting a channel in the browser prints a shell-ready `ffmpeg` command to stdout.
