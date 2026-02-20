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
import re
from jellyfin_client import JellyfinClient
from spotify_client import SpotifyClient
from ssh_client import SSHClient
from duplicate_finder import DuplicateFinder, DuplicateGroup
import random
from typing import Any
from rapidfuzz import fuzz

jellyfin_client: JellyfinClient | None = None
spotify_client: SpotifyClient | None = None
ssh_client_instance: SSHClient | None = None

duplicate_groups: list[DuplicateGroup] = []
current_group_index: int = 0
pending_playlist: dict = {}

# --- Cleanup feature state ---
bogus_playlists: list[dict] = []
junk_artist_candidates: list[dict] = []
artist_duplicate_pairs: list[tuple[dict, dict]] = []
missing_artwork_items: list[dict] = []
m3u_scan_results: list[str] = []


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


# ---------------------------------------------------------------------------
# Bogus Playlists
# ---------------------------------------------------------------------------

def _playlist_label(p: dict) -> str:
    """Build a human-readable label for a bogus playlist checkbox entry."""
    name = p.get("Name", p["Id"])
    track_count = p.get("ChildCount", 0)
    path = p.get("Path") or ""
    matched_album = p.get("_matched_album") or ""
    parts = [name]
    if matched_album:
        parts.append(f"✅ album exists: {matched_album}")
    if track_count:
        parts.append(f"{track_count} track{'s' if track_count != 1 else ''}")
    if path:
        parts.append(f"source: {path}")
    return "  |  ".join(parts)


def _match_playlist_to_album(playlist_name: str, albums: list[dict]) -> str | None:
    """
    Try to find an album in Jellyfin that corresponds to a playlist name.
    Playlist names are typically "Artist - Album Title".
    Returns a display string "Album Title (by Artist)" if matched, else None.
    """
    if " - " not in playlist_name:
        return None
    # Split on first " - " only
    artist_part, album_part = playlist_name.split(" - ", 1)
    artist_norm = artist_part.strip().lower()
    album_norm = album_part.strip().lower()
    for album in albums:
        a_name = album.get("Name", "").lower()
        a_artist = album.get("AlbumArtist", "").lower()
        name_score = fuzz.token_sort_ratio(a_name, album_norm)
        artist_score = fuzz.token_sort_ratio(a_artist, artist_norm)
        if name_score >= 85 and artist_score >= 75:
            return f"{album.get('Name')} (by {album.get('AlbumArtist', '?')})"
    return None


def scan_bogus_playlists() -> tuple[str, Any]:
    global bogus_playlists
    if not jellyfin_client:
        return "❌ Connect to Jellyfin first", gr.update(choices=[], visible=False)
    try:
        playlists = jellyfin_client.get_all_playlists()
        albums = jellyfin_client.get_all_albums_raw()

        confirmed: list[dict] = []
        unconfirmed: list[dict] = []
        for p in playlists:
            name = p.get("Name", "")
            if " - " not in name:
                continue
            matched = _match_playlist_to_album(name, albums)
            if matched:
                p = dict(p)  # don't mutate original
                p["_matched_album"] = matched
                confirmed.append(p)
            else:
                unconfirmed.append(p)

        bogus_playlists = confirmed + unconfirmed  # confirmed first
        if not confirmed and not unconfirmed:
            return "✅ No album-named playlists found", gr.update(choices=[], visible=False)

        choices = [_playlist_label(p) for p in bogus_playlists]
        # Pre-select only those where the album was confirmed to exist
        preselected = [_playlist_label(p) for p in confirmed]
        summary_lines = [
            f"Found **{len(confirmed)}** playlist(s) with a matching album in Jellyfin (pre-selected — safe to delete).",
        ]
        if unconfirmed:
            summary_lines.append(
                f"Found **{len(unconfirmed)}** album-named playlist(s) with **no matching album detected** "
                f"(not pre-selected — review carefully before deleting)."
            )
        return "\n\n".join(summary_lines), gr.update(choices=choices, value=preselected, visible=True)
    except Exception as e:
        return f"❌ Scan failed: {e}", gr.update(choices=[], visible=False)


def delete_selected_playlists(selected: list[str]) -> str:
    if not jellyfin_client:
        return "❌ Connect to Jellyfin first"
    if not selected:
        return "❌ No playlists selected"
    # Build label → playlist mapping so we match correctly even with duplicate names
    label_to_playlist = {_playlist_label(p): p for p in bogus_playlists}
    deleted, errors, m3u_paths = 0, [], []
    for label in selected:
        p = label_to_playlist.get(label)
        if not p:
            continue
        try:
            jellyfin_client.delete_item(p["Id"])
            deleted += 1
            if p.get("Path"):
                m3u_paths.append(p["Path"])
        except Exception as e:
            errors.append(f"{p.get('Name', p['Id'])}: {e}")
    msg = f"✅ Deleted {deleted} playlist(s) from Jellyfin's database."
    msg += "\n\n**Your audio files and albums are untouched** — only the playlist entries were removed."
    if m3u_paths:
        msg += (
            "\n\n⚠️ **These playlists may reappear** after a library rescan if Jellyfin re-reads "
            "the source files below. To prevent this, either delete those files or go to "
            "Jellyfin → Dashboard → Libraries → [your music library] → \"Import playlists from "
            "media folders\" and disable it.\n\n"
            "**Source files detected:**\n" + "\n".join(f"- `{p}`" for p in m3u_paths)
        )
    if errors:
        msg += "\n\n❌ Errors:\n" + "\n".join(errors)
    return msg


# ---------------------------------------------------------------------------
# Junk Artists
# ---------------------------------------------------------------------------

_JUNK_RULES: list[tuple[str, str]] = [
    (r'^\d+$', "numeric only"),
    (r'^.{1,2}$', "≤2 characters"),
    (r'^\d', "starts with digit"),
    (r'^[A-Za-z0-9]{12,}$', "looks like a username/ID"),
]


def scan_junk_artists() -> tuple[str, Any]:
    global junk_artist_candidates
    if not jellyfin_client:
        return "❌ Connect to Jellyfin first", gr.update(choices=[], visible=False)
    try:
        raw = jellyfin_client.get_all_artists_raw()
        flagged: list[tuple[dict, list[str]]] = []
        for a in raw:
            name = a.get("Name", "")
            reasons = [label for pattern, label in _JUNK_RULES if re.match(pattern, name)]
            if reasons:
                flagged.append((a, reasons))
        junk_artist_candidates = [a for a, _ in flagged]
        if not flagged:
            return "✅ No junk artists found", gr.update(choices=[], visible=False)
        choices = [f"{a['Name']}  [{', '.join(r)}]" for a, r in flagged]
        return f"Found {len(flagged)} junk artist(s) — review and deselect any to keep:", gr.update(choices=choices, value=choices, visible=True)
    except Exception as e:
        return f"❌ Scan failed: {e}", gr.update(choices=[], visible=False)


def delete_selected_junk_artists(selected: list[str]) -> str:
    if not jellyfin_client:
        return "❌ Connect to Jellyfin first"
    if not selected:
        return "❌ No artists selected"
    # Build lookup: display label -> item_id
    raw = junk_artist_candidates
    deleted, errors = 0, []
    for a in raw:
        label_prefix = a["Name"] + "  ["
        if any(s.startswith(label_prefix) or s == a["Name"] for s in selected):
            try:
                jellyfin_client.delete_item(a["Id"])
                deleted += 1
            except Exception as e:
                errors.append(f"{a['Name']}: {e}")
    msg = f"✅ Deleted {deleted} artist(s)"
    if errors:
        msg += "\n\n❌ Errors:\n" + "\n".join(errors)
    return msg


# ---------------------------------------------------------------------------
# Duplicate Artists (The X / X, The and fuzzy matches)
# ---------------------------------------------------------------------------

def _normalize_for_dedup(name: str) -> str:
    n = name.lower().strip()
    if n.endswith(", the"):
        n = "the " + n[:-5]
    return n


def scan_artist_duplicates(threshold: int) -> tuple[str, Any]:
    global artist_duplicate_pairs
    if not jellyfin_client:
        return "❌ Connect to Jellyfin first", gr.update(choices=[], visible=False)
    try:
        raw = jellyfin_client.get_all_artists_raw()
        pairs: list[tuple[dict, dict]] = []
        seen: set[frozenset] = set()
        for i, a in enumerate(raw):
            na = _normalize_for_dedup(a.get("Name", ""))
            for b in raw[i + 1:]:
                nb = _normalize_for_dedup(b.get("Name", ""))
                key = frozenset([a["Id"], b["Id"]])
                if key in seen:
                    continue
                score = fuzz.token_sort_ratio(na, nb)
                if score >= threshold:
                    pairs.append((a, b))
                    seen.add(key)
        artist_duplicate_pairs = pairs
        if not pairs:
            return "✅ No duplicate artists found", gr.update(choices=[], visible=False)
        choices = [f"{a['Name']}  ↔  {b['Name']}" for a, b in pairs]
        return f"Found {len(pairs)} likely duplicate pair(s):", gr.update(choices=choices, value=[], visible=True)
    except Exception as e:
        return f"❌ Scan failed: {e}", gr.update(choices=[], visible=False)


def merge_selected_artist_pairs(selected: list[str], preferred_side: str) -> str:
    """Rename both artists in each selected pair to the preferred side's name."""
    if not jellyfin_client:
        return "❌ Connect to Jellyfin first"
    if not selected:
        return "❌ No pairs selected"
    merged, errors = 0, []
    for a, b in artist_duplicate_pairs:
        label = f"{a['Name']}  ↔  {b['Name']}"
        if label not in selected:
            continue
        canonical = a["Name"] if preferred_side == "left" else b["Name"]
        for artist in (a, b):
            if artist["Name"] != canonical:
                try:
                    jellyfin_client.rename_artist(artist["Id"], canonical, canonical)
                    merged += 1
                except Exception as e:
                    errors.append(f"{artist['Name']}: {e}")
    msg = f"✅ Renamed {merged} artist(s)"
    if errors:
        msg += "\n\n❌ Errors:\n" + "\n".join(errors)
    return msg


# ---------------------------------------------------------------------------
# Missing Artwork
# ---------------------------------------------------------------------------

def scan_missing_artwork(item_type: str) -> tuple[str, Any]:
    global missing_artwork_items
    if not jellyfin_client:
        return "❌ Connect to Jellyfin first", gr.update(choices=[], visible=False)
    try:
        if item_type == "Artists":
            items = jellyfin_client.get_all_artists_raw()
        else:
            items = jellyfin_client.get_all_albums_raw()
        missing = [i for i in items if not i.get("ImageTags", {}).get("Primary")]
        missing_artwork_items = missing
        if not missing:
            return f"✅ All {item_type.lower()} have artwork", gr.update(choices=[], visible=False)
        choices = [i.get("Name", i["Id"]) for i in missing]
        return f"Found {len(missing)} {item_type.lower()} missing artwork:", gr.update(choices=choices, value=[], visible=True)
    except Exception as e:
        return f"❌ Scan failed: {e}", gr.update(choices=[], visible=False)


def refresh_selected_artwork(selected: list[str]) -> str:
    if not jellyfin_client:
        return "❌ Connect to Jellyfin first"
    if not selected:
        return "❌ Nothing selected"
    refreshed, errors = 0, []
    name_to_id = {i.get("Name", i["Id"]): i["Id"] for i in missing_artwork_items}
    for name in selected:
        item_id = name_to_id.get(name)
        if not item_id:
            continue
        try:
            jellyfin_client.refresh_item_metadata(item_id)
            refreshed += 1
        except Exception as e:
            errors.append(f"{name}: {e}")
    msg = f"✅ Queued metadata refresh for {refreshed} item(s) — Jellyfin will fetch artwork in the background"
    if errors:
        msg += "\n\n❌ Errors:\n" + "\n".join(errors)
    return msg


# ---------------------------------------------------------------------------
# Playlist generation (existing)
# ---------------------------------------------------------------------------

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



# ---------------------------------------------------------------------------
# SSH M3U Cleanup
# ---------------------------------------------------------------------------

def _clean_hostname(raw: str) -> str:
    """Strip protocol, path, and port from a pasted URL so only the hostname remains."""
    h = raw.strip()
    # Remove protocol prefix
    for prefix in ("https://", "http://"):
        if h.startswith(prefix):
            h = h[len(prefix):]
    # Remove path
    h = h.split("/")[0]
    # Remove port
    h = h.split(":")[0]
    return h


def connect_ssh(hostname: str, port_str: str, username: str, password: str, key_path: str) -> str:
    global ssh_client_instance
    if not hostname or not username:
        return "❌ Hostname and username are required"
    try:
        port = int(port_str) if port_str.strip() else 22
        cleaned = _clean_hostname(hostname)
        ssh_client_instance = SSHClient(
            hostname=cleaned,
            username=username.strip(),
            port=port,
            password=password.strip() or None,
            key_path=key_path.strip() or None,
        )
        who = ssh_client_instance.whoami()
        return f"✅ Connected to **{cleaned}** as **{who}**"
    except Exception as e:
        ssh_client_instance = None
        return f"❌ SSH connection failed: {e}"


def scan_m3u_files(music_path: str) -> tuple[str, Any]:
    global m3u_scan_results
    if not ssh_client_instance:
        return "❌ Connect via SSH first", gr.update(choices=[], visible=False)
    if not music_path.strip():
        return "❌ Enter the music library path on the server", gr.update(choices=[], visible=False)
    try:
        files = ssh_client_instance.find_playlist_files(music_path.strip())
        m3u_scan_results = files
        if not files:
            return f"✅ No .m3u/.m3u8 files found under `{music_path}`", gr.update(choices=[], visible=False)
        return (
            f"Found **{len(files)}** playlist file(s) — all pre-selected. "
            f"Deselect any you want to keep, then click **Delete Selected Files**.",
            gr.update(choices=files, value=files, visible=True),
        )
    except Exception as e:
        return f"❌ Scan failed: {e}", gr.update(choices=[], visible=False)


def delete_selected_m3u(selected: list[str]) -> str:
    if not ssh_client_instance:
        return "❌ Connect via SSH first"
    if not selected:
        return "❌ Nothing selected"
    try:
        # Check write access on a sample file first
        if not ssh_client_instance.test_write(selected[0]):
            return (
                f"❌ The SSH user doesn't have write permission to delete files in "
                f"`{selected[0][:selected[0].rfind('/')]}`. "
                f"You may need to run the app as a user with access to that directory."
            )
        results = ssh_client_instance.delete_files(selected)
        deleted = [p for p, err in results.items() if err is None]
        failed = [(p, err) for p, err in results.items() if err is not None]
        msg = f"✅ Deleted **{len(deleted)}** file(s) from the server."
        msg += (
            "\n\nJellyfin will remove the corresponding playlist entries "
            "the next time you run a library scan. You can trigger one now from "
            "**Jellyfin → Dashboard → Libraries → [your library] → Scan All Libraries**."
        )
        if failed:
            msg += "\n\n❌ Failed:\n" + "\n".join(f"- `{p}`: {e}" for p, e in failed)
        return msg
    except Exception as e:
        return f"❌ Delete failed: {e}"


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

        # -------------------------------------------------------------------
        with gr.Tab("🗑 Bogus Playlists"):
            gr.Markdown("""
### What this tool does
Scans your Jellyfin playlists for entries whose names look like `Artist - Album Title` — a pattern
that Jellyfin creates automatically when it finds `.m3u` or `.m3u8` files inside music folders during a library scan.

Critically, it then **cross-references each playlist against your actual Jellyfin albums**.
Only playlists where the corresponding album already exists in Jellyfin are pre-selected,
because that's the only case where deletion is clearly safe.

### What "Delete" means
- ✅ Removes only the **playlist entry** from Jellyfin's database
- ✅ Your **audio files on disk are never touched**
- ✅ The **album, artist, and track entries** in Jellyfin are completely unaffected — music keeps playing normally
- ❌ Does **not** delete the `.m3u`/`.m3u8` source file on disk — see below

### ⚠️ They may come back after a library rescan
If the original `.m3u`/`.m3u8` file still exists on disk, Jellyfin will recreate the playlist
on the next library scan. The source file path is shown next to each playlist after scanning.
To stop them coming back, either:
1. **Delete the `.m3u` files** from disk (paths are listed in results after deletion), or
2. In Jellyfin: **Dashboard → Libraries → [your music library] → uncheck "Import playlists from media folders"**
            """)
            bp_scan_btn = gr.Button("Scan for Album-Named Playlists")
            bp_status = gr.Markdown("")
            bp_list = gr.CheckboxGroup(
                label="Playlists found  (format: Name  |  matched album  |  track count  |  source file)",
                choices=[],
                visible=False,
            )
            bp_delete_btn = gr.Button("Delete Selected", variant="stop")
            bp_result = gr.Markdown("")

            bp_scan_btn.click(scan_bogus_playlists, outputs=[bp_status, bp_list])
            bp_delete_btn.click(delete_selected_playlists, inputs=[bp_list], outputs=bp_result)

        # -------------------------------------------------------------------
        with gr.Tab("🧹 Junk Artists"):
            gr.Markdown(
                "Flags artists with numeric-only names (e.g. `01`, `02`), "
                "very short names, names starting with a digit, or long alphanumeric strings "
                "that look like import artifacts. Deselect any you want to keep before deleting."
            )
            ja_scan_btn = gr.Button("Scan for Junk Artists")
            ja_status = gr.Markdown("")
            ja_list = gr.CheckboxGroup(label="Select artists to delete", choices=[], visible=False)
            ja_delete_btn = gr.Button("Delete Selected", variant="stop")
            ja_result = gr.Markdown("")

            ja_scan_btn.click(scan_junk_artists, outputs=[ja_status, ja_list])
            ja_delete_btn.click(delete_selected_junk_artists, inputs=[ja_list], outputs=ja_result)

        # -------------------------------------------------------------------
        with gr.Tab("🔀 Duplicate Artists"):
            gr.Markdown(
                "Finds artist pairs using fuzzy matching — catches 'The Beatles' vs 'Beatles, The', "
                "slight spelling differences, etc. Select pairs to merge and choose which name to keep."
            )
            da_threshold = gr.Slider(70, 99, value=90, step=1, label="Similarity Threshold")
            da_scan_btn = gr.Button("Scan for Duplicate Artists")
            da_status = gr.Markdown("")
            da_list = gr.CheckboxGroup(label="Select pairs to merge", choices=[], visible=False)
            da_preferred = gr.Radio(
                choices=[("Keep left name (first listed)", "left"), ("Keep right name (second listed)", "right")],
                value="left",
                label="Which name to keep",
            )
            da_merge_btn = gr.Button("Merge Selected Pairs", variant="primary")
            da_result = gr.Markdown("")

            da_scan_btn.click(scan_artist_duplicates, inputs=[da_threshold], outputs=[da_status, da_list])
            da_merge_btn.click(merge_selected_artist_pairs, inputs=[da_list, da_preferred], outputs=da_result)

        # -------------------------------------------------------------------
        with gr.Tab("🖼 Missing Artwork"):
            gr.Markdown(
                "Finds artists or albums with no primary image. "
                "Selecting items and clicking Refresh tells Jellyfin to re-fetch metadata "
                "and artwork from its configured metadata providers (MusicBrainz, etc.)."
            )
            mw_type = gr.Radio(
                choices=["Artists", "Albums"],
                value="Artists",
                label="Scan",
            )
            mw_scan_btn = gr.Button("Scan for Missing Artwork")
            mw_status = gr.Markdown("")
            mw_list = gr.CheckboxGroup(label="Select items to refresh", choices=[], visible=False)
            mw_refresh_btn = gr.Button("Refresh Metadata for Selected", variant="primary")
            mw_result = gr.Markdown("")

            mw_scan_btn.click(scan_missing_artwork, inputs=[mw_type], outputs=[mw_status, mw_list])
            mw_refresh_btn.click(refresh_selected_artwork, inputs=[mw_list], outputs=mw_result)

        # -------------------------------------------------------------------
        with gr.Tab("🔑 SSH: Delete M3U Files"):
            gr.Markdown("""
### What this tool does
Connects to your Jellyfin server over SSH and deletes the `.m3u`/`.m3u8` playlist files
directly from the music folder. After deletion, a Jellyfin library scan will automatically
remove the corresponding playlist entries — no Jellyfin write-permission issues.

### Authentication
You can authenticate with a **password** or an **SSH key file**. If neither is provided,
the app will try your default SSH keys (`~/.ssh/id_rsa`, `~/.ssh/id_ed25519`, etc.).
The SSH connection is made from **this machine** to the Jellyfin server.
            """)
            with gr.Row():
                ssh_host = gr.Textbox(label="Hostname / IP", placeholder="fedora.tail7162dd.ts.net  (just the hostname — you can paste your Jellyfin URL and it will be cleaned up)")
                ssh_port = gr.Textbox(label="Port", value="22", scale=0)
            with gr.Row():
                ssh_user = gr.Textbox(label="Username", placeholder="todd")
                ssh_pass = gr.Textbox(label="Password (optional)", type="password")
                ssh_key  = gr.Textbox(label="SSH Key Path (optional)", placeholder="~/.ssh/id_ed25519")
            ssh_connect_btn = gr.Button("Connect via SSH")
            ssh_status = gr.Markdown("")

            gr.Markdown("---")
            ssh_music_path = gr.Textbox(
                label="Music library path on the server",
                placeholder="/mnt/music",
            )
            ssh_scan_btn = gr.Button("Scan for .m3u / .m3u8 Files")
            ssh_scan_status = gr.Markdown("")
            ssh_file_list = gr.CheckboxGroup(label="Files found", choices=[], visible=False)
            ssh_delete_btn = gr.Button("Delete Selected Files", variant="stop")
            ssh_delete_result = gr.Markdown("")

            ssh_connect_btn.click(
                connect_ssh,
                inputs=[ssh_host, ssh_port, ssh_user, ssh_pass, ssh_key],
                outputs=ssh_status,
            )
            ssh_scan_btn.click(scan_m3u_files, inputs=[ssh_music_path], outputs=[ssh_scan_status, ssh_file_list])
            ssh_delete_btn.click(delete_selected_m3u, inputs=[ssh_file_list], outputs=ssh_delete_result)

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch()
