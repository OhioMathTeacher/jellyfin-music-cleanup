# Jellyfin Music Cleanup

Gradio web app for Jellyfin music libraries:
- Find and consolidate duplicate artists (fuzzy matching)
- Generate Jellyfin playlists from Spotify top tracks

## Quick start
```bash
cd jellyfin
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python app.py
```
Then open http://localhost:7860

## Credentials
- Jellyfin: server URL + API key (Dashboard → API Keys → New API Key). User Id optional; first user is used if omitted.
- Spotify: Client ID + Client Secret (developer.spotify.com dashboard).

## Notes
- Rename flow updates duplicate artists to a single preferred name (Jellyfin lacks a true merge endpoint).
- Playlist creation searches your Jellyfin library for the Spotify top tracks and creates a new playlist with matches.
