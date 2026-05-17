# IPTV Browser

Local CLI for browsing Xtream IPTV metadata, inspecting streams, and printing `ffmpeg` commands.

## Commands

```bash
PYTHONPATH=src python3 -m iptv_browser fetch
PYTHONPATH=src python3 -m iptv_browser browse
PYTHONPATH=src python3 -m iptv_browser inspect
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

The browser can launch streams in external tools, but recordings only happen if you run the shell-ready `ffmpeg` command it prints.

## Browser controls

- Type to filter channels. Arrow keys move through the active list.
- `Tab` toggles between channels and the selected channel's programs.
- `Ctrl-R` selects the visible recording command so it prints to stdout when the browser exits.
- `Ctrl-F` captures one full-resolution PNG frame, renders it in the upper-right terminal pane, and shows `ffprobe` stats such as bitrate and resolution.
- `Ctrl-O` opens the selected stream in VLC.
- `Ctrl-C` clears the search text, or quits if the search is already empty.

`Ctrl-F` requires `ffmpeg` and `ffprobe` on `PATH`. Inline image rendering currently uses the Kitty/Ghostty terminal image protocol. `Ctrl-O` requires VLC.
