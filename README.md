# Jellyfin Music Cleanup

Gradio web app for cleaning up and managing Jellyfin music libraries. Requires Python 3.9+.

## Features

| Tab | What it does |
|---|---|
| **Connect** | Enter Jellyfin server URL + API key to authenticate |
| **Find Duplicates** | Fuzzy-match duplicate tracks within an artist's library |
| **Playlists** | Generate Jellyfin playlists from Spotify top tracks (filter by era/decade, track source) |
| **Bogus Playlists** | Detect and delete auto-imported playlists from .m3u/.m3u8 files |
| **Junk Artists** | Flag and delete artist entries that look like imports artifacts (numeric IDs, category labels, etc.) |
| **Duplicate Artists** | Fuzzy-match artist name pairs (catches AC/DC vs AC-DC, Alice In Chains vs Alice in Chains, etc.) and merge at the track level |
| **Missing Artwork** | Find albums with no cover art and trigger a Jellyfin metadata refresh |
| **SSH: Delete M3U Files** | SSH into the media server and delete .m3u/.m3u8 files that cause bogus playlists to reappear |

## Setup

```bash
git clone --recurse-submodules https://github.com/OhioMathTeacher/music-cleanup.git
cd music-cleanup/jellyfin
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

Then open http://localhost:7860 in a browser.

> **Network:** The app connects directly to your Jellyfin server. Run it on a machine that can reach the server (same LAN or VPN).

## Credentials

**Jellyfin**
- Server URL — e.g. `https://192.168.1.100:8920` or `http://jellyfin.local:8096`
- API key — Jellyfin Dashboard → API Keys → New API Key
- User ID — optional; the first admin user is used if omitted
- Self-signed certs are handled automatically (no extra config needed)

**Spotify** (only needed for Playlist tab)
- Client ID + Client Secret from [developer.spotify.com](https://developer.spotify.com/dashboard)

## Jellyfin user permissions

The API key user needs:
- **Manage Server** — to create/delete playlists and refresh metadata
- **Delete media from all libraries** — to remove junk artists and duplicates

Grant these in Jellyfin Dashboard → Users → (username) → Permissions.

## SSH tab requirements

The SSH tab connects from this machine to the media server as an OS user (not the Jellyfin account). That user needs read/write access to the music directory. If your music is on a shared mount, add the user to the appropriate group (e.g. `media`) and ensure the directory has group-write permissions (`chmod g+w`).
