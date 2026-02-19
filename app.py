#!/usr/bin/env python3
"""
Jellyfin Music Library Manager - Web UI

A Gradio-based app for:
1. Finding and consolidating duplicate artists in Jellyfin
2. Generating playlists based on Spotify's top tracks

Run with: python app.py
Then open http://localhost:7860 in your browser
"""
from __future__ import annotations

import gradio as gr
from jellyfin_client import JellyfinClient
from spotify_client import SpotifyClient
from duplicate_finder import DuplicateFinder, DuplicateGroup
import random
from typing import Any

jellyfin_client: JellyfinClient | None = None
spotify_client: SpotifyClient | None = None

duplicate_groups: list[DuplicateGroup] = []
current_group_index: int = 0
pending_playlist: dict = {}


def connect_jellyfin(url: str, api_key: str, user_id: str) -> str:
    global jellyfin_client

    if not url or not api_key:
        return "❌ Please enter Jellyfin URL and API key"

    try:
        jellyfin_client = JellyfinClient(url.strip(), api_key.strip(), user_id.strip() or None)
        artists = jellyfin_client.get_all_artists()
        return f"✅ Connected to Jellyfin. Found {len(artists)} artists."
    except Exception as e:
        jellyfin_client = None
        return f"❌ Connection failed: {e}"


def connect_spotify(client_id: str, client_secret: str) -> str:
    global spotify_client

    if not client_id or not client_secret:
        return "❌ Please enter Spotify Client ID and Secret"

    try:
        spotify_client = SpotifyClient(client_id.strip(), client_secret.strip())
        return "✅ Connected to Spotify API"
    except Exception as e:
        spotify_client = None
        return f"❌ Spotify connection failed: {e}"


def scan_duplicates(threshold: int) -> tuple[str, str, Any, Any]:
    global duplicate_groups, current_group_index

    if not jellyfin_client:
        return "❌ Connect to Jellyfin first", "", gr.update(visible=False), gr.update(visible=False)

    try:
        finder = DuplicateFinder(threshold=threshold)
        duplicate_groups = finder.find_duplicates(jellyfin_client.get_all_artists(refresh=True))
        current_group_index = 0
        if not duplicate_groups:
            return "✅ No duplicates found", "", gr.update(visible=False), gr.update(visible=False)
        return (
            f"🔍 Found {len(duplicate_groups)} potential duplicate groups. Click Next to review.",
            "",
            gr.update(visible=True),
            gr.update(visible=True),
        )
    except Exception as e:
        duplicate_groups = []
        return f"❌ Scan failed: {e}", "", gr.update(visible=False), gr.update(visible=False)


def _current_group_display() -> tuple[str, str]:
    if not duplicate_groups:
        return "No duplicates to review", ""
    if current_group_index >= len(duplicate_groups):
        return "✅ All groups reviewed", ""

    group = duplicate_groups[current_group_index]
    display = f"## Group {current_group_index + 1} of {len(duplicate_groups)}\n\n"
    display += f"**Suggested name:** `{group.canonical_name}`\n\n"
    display += "### Artists in this group:\n\n"
    for i, artist in enumerate(group.artists, 1):
        display += f"{i}. **{artist.title}**\n"
        display += f"   - Albums: {artist.album_count} | Tracks: {artist.track_count}\n"
        display += f"   - Item Id: `{artist.item_id}`\n\n"
    return display, group.canonical_name


def next_group() -> tuple[str, str]:
    global current_group_index
    current_group_index += 1
    return _current_group_display()


def prev_group() -> tuple[str, str]:
    global current_group_index
    current_group_index = max(0, current_group_index - 1)
    return _current_group_display()


def apply_rename(preferred_name: str) -> str:
    global duplicate_groups, current_group_index

    if not jellyfin_client:
        return "❌ Jellyfin not connected"
    if not duplicate_groups or current_group_index >= len(duplicate_groups):
        return "❌ No group selected"
    if not preferred_name.strip():
        return "❌ Enter a preferred name"

    group = duplicate_groups[current_group_index]
    preferred_name = preferred_name.strip()

    try:
        renamed = 0
        for artist in group.artists:
            if artist.title != preferred_name:
                jellyfin_client.rename_artist(artist.item_id, preferred_name, preferred_name)
                renamed += 1
        return f"✅ Renamed {renamed} artists to '{preferred_name}'"
    except Exception as e:
        return f"❌ Rename failed: {e}"


def generate_playlist_preview(artist_input: str, playlist_style: str, track_count: int) -> tuple[str, str]:
    global pending_playlist
    pending_playlist = {}

    if not jellyfin_client:
        return "❌ Connect to Jellyfin first", ""
    if not spotify_client:
        return "❌ Connect to Spotify first", ""
    if not artist_input:
        return "❌ Enter artist name(s)", ""

    artist_names = []
    for part in artist_input.replace(" and ", ",").replace(" & ", ",").split(","):
        name = part.strip()
        if name:
            artist_names.append(name)

    if not artist_names:
        return "❌ No valid artist names", ""

    try:
        result_lines = []
        matched_track_ids: list[str] = []
        matched_tracks: list[str] = []

        for artist_name in artist_names:
            spotify_tracks = spotify_client.get_top_tracks(artist_name, limit=track_count * 3)
            for track in spotify_tracks:
                if len(matched_track_ids) >= track_count:
                    break
                jf_track = jellyfin_client.find_track(artist_name, track['name'])
                if jf_track:
                    matched_track_ids.append(jf_track.get("Id"))
                    matched_tracks.append(f"{track['name']} — {artist_name}")
            result_lines.append(f"🎵 {artist_name}: matched {len(matched_track_ids)} tracks so far")

        if not matched_track_ids:
            return "❌ No matching tracks found in Jellyfin", ""

        playlist_name = _build_playlist_name(artist_names, playlist_style)
        pending_playlist = {
            "name": playlist_name,
            "track_ids": matched_track_ids,
        }

        preview = f"Playlist: **{playlist_name}**\n\n" + "\n".join(matched_tracks[:track_count])
        return preview, ""
    except Exception as e:
        pending_playlist = {}
        return f"❌ Playlist generation failed: {e}", ""


def _build_playlist_name(artists: list[str], style: str) -> str:
    if style == "experience":
        return f"The {' & '.join(artists)} Experience"
    if style == "bangers":
        return f"Certified {' & '.join(artists)} Bangers"
    return f"Why {' & '.join(artists)} Slaps"


def save_playlist() -> str:
    global pending_playlist

    if not jellyfin_client:
        return "❌ Connect to Jellyfin first"
    if not pending_playlist:
        return "❌ No playlist preview ready"

    try:
        playlist_id = jellyfin_client.create_playlist(pending_playlist['name'], pending_playlist['track_ids'])
        return f"✅ Created playlist '{pending_playlist['name']}' (id: {playlist_id})"
    except Exception as e:
        return f"❌ Failed to create playlist: {e}"


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Jellyfin Music Cleanup") as demo:
        gr.Markdown("# Jellyfin Music Cleanup\nManage duplicates and generate playlists using Jellyfin + Spotify.")

        with gr.Tab("Connect"):
            gr.Markdown("### Jellyfin")
            jf_url = gr.Textbox(label="Jellyfin Server URL", value="http://localhost:8096")
            jf_key = gr.Textbox(label="API Key", type="password")
            jf_user = gr.Textbox(label="User Id (optional)")
            connect_jf_btn = gr.Button("Connect to Jellyfin")
            jf_status = gr.Markdown("")

            connect_jf_btn.click(connect_jellyfin, inputs=[jf_url, jf_key, jf_user], outputs=jf_status)

            gr.Markdown("### Spotify")
            sp_id = gr.Textbox(label="Client ID")
            sp_secret = gr.Textbox(label="Client Secret", type="password")
            connect_sp_btn = gr.Button("Connect to Spotify")
            sp_status = gr.Markdown("")

            connect_sp_btn.click(connect_spotify, inputs=[sp_id, sp_secret], outputs=sp_status)

        with gr.Tab("Find Duplicates"):
            threshold = gr.Slider(60, 95, value=80, step=1, label="Similarity Threshold")
            scan_btn = gr.Button("Scan for Duplicates")
            scan_status = gr.Markdown("")
            group_display = gr.Markdown(visible=False)
            preferred_name = gr.Textbox(label="Preferred Name", visible=False)

            with gr.Row():
                prev_btn = gr.Button("⬅️ Previous", visible=False)
                next_btn = gr.Button("Next ➡️", visible=False)
                apply_btn = gr.Button("Apply Rename", visible=False)

            scan_btn.click(
                scan_duplicates,
                inputs=[threshold],
                outputs=[scan_status, preferred_name, group_display, preferred_name],
            )
            next_btn.click(next_group, outputs=[group_display, preferred_name])
            prev_btn.click(prev_group, outputs=[group_display, preferred_name])
            apply_btn.click(apply_rename, inputs=[preferred_name], outputs=scan_status)

            def _toggle_buttons(scan_msg, pref_value, group_vis, pref_vis):
                visible = "✅" not in scan_msg or "No duplicates" not in scan_msg
                return gr.update(visible=visible), gr.update(visible=visible), gr.update(visible=visible)

            scan_btn.click(
                lambda status, *_: (gr.update(visible=True), gr.update(visible=True)),
                inputs=[scan_status],
                outputs=[group_display, preferred_name],
            )
            scan_btn.click(lambda: gr.update(visible=True), outputs=prev_btn)
            scan_btn.click(lambda: gr.update(visible=True), outputs=next_btn)
            scan_btn.click(lambda: gr.update(visible=True), outputs=apply_btn)

        with gr.Tab("Playlists"):
            artist_input = gr.Textbox(label="Artist name(s) (comma-separated)")
            playlist_style = gr.Dropdown(
                label="Playlist style",
                choices=[
                    ("Why X Slaps", "slaps"),
                    ("Certified X Bangers", "bangers"),
                    ("The X Experience", "experience"),
                ],
                value="slaps",
            )
            track_count = gr.Slider(5, 50, value=20, step=1, label="Max tracks")
            preview_btn = gr.Button("Preview Playlist")
            preview_md = gr.Markdown("")
            save_btn = gr.Button("Save to Jellyfin")
            save_status = gr.Markdown("")

            preview_btn.click(
                generate_playlist_preview,
                inputs=[artist_input, playlist_style, track_count],
                outputs=[preview_md, save_status],
            )
            save_btn.click(save_playlist, outputs=save_status)

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch()
