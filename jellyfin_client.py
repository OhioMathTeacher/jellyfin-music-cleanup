"""
Jellyfin Music Client

Minimal client for Jellyfin music libraries using the REST API + API key.
"""

from dataclasses import dataclass
from typing import Optional
import requests
from rapidfuzz import fuzz


@dataclass
class ArtistInfo:
    """Lightweight artist info for display"""
    title: str
    item_id: str
    album_count: int = 0
    track_count: int = 0
    sort_name: Optional[str] = None


class JellyfinClient:
    """Client for interacting with Jellyfin music library"""

    def __init__(self, base_url: str, api_key: str, user_id: Optional[str] = None):
        if not base_url.startswith("http://") and not base_url.startswith("https://"):
            raise ValueError("Base URL must start with http:// or https://")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.user_id = user_id or self._fetch_first_user_id()

        if not self.user_id:
            raise ValueError("Could not determine Jellyfin user id; please provide one explicitly.")

        self._artist_cache: list[ArtistInfo] | None = None

    def _headers(self) -> dict:
        return {
            "X-Emby-Token": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = requests.get(f"{self.base_url}{path}", headers=self._headers(), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json_body: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        resp = requests.post(
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=json_body,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def _fetch_first_user_id(self) -> Optional[str]:
        try:
            data = self._get("/Users")
            users = data if isinstance(data, list) else data.get("Items", [])
            if users:
                return users[0].get("Id")
        except Exception:
            return None
        return None

    def get_all_artists(self, refresh: bool = False) -> list[ArtistInfo]:
        if self._artist_cache is not None and not refresh:
            return self._artist_cache

        params = {
            "IncludeItemTypes": "MusicArtist",
            "Recursive": "true",
            "Fields": "ChildCount,SortName",
            "UserId": self.user_id,
            "Limit": 50000,
        }
        data = self._get("/Items", params=params)
        items = data.get("Items", [])

        artists: list[ArtistInfo] = []
        for item in items:
            artists.append(
                ArtistInfo(
                    title=item.get("Name", ""),
                    item_id=item.get("Id", ""),
                    album_count=item.get("ChildCount", 0),
                    track_count=item.get("RunTimeTicks", 0),
                    sort_name=item.get("SortName"),
                )
            )

        self._artist_cache = artists
        return artists

    def search_artists(self, query: str) -> list[ArtistInfo]:
        q_lower = query.lower()
        return [a for a in self.get_all_artists() if q_lower in a.title.lower()]

    def rename_artist(self, item_id: str, new_name: str, sort_name: Optional[str] = None) -> None:
        body = {"Name": new_name}
        if sort_name:
            body["SortName"] = sort_name
        self._post(f"/Items/{item_id}", json_body=body)
        if self._artist_cache:
            for artist in self._artist_cache:
                if artist.item_id == item_id:
                    artist.title = new_name
                    if sort_name:
                        artist.sort_name = sort_name
                    break

    def find_track(self, artist_name: str, track_name: str) -> Optional[dict]:
        params = {
            "SearchTerm": track_name,
            "IncludeItemTypes": "Audio",
            "Recursive": "true",
            "Fields": "Artist,Album,SortName",
            "UserId": self.user_id,
            "Limit": 50,
        }
        data = self._get("/Items", params=params)
        items = data.get("Items", [])

        candidates = []
        for item in items:
            item_artist = " ".join(item.get("Artists", [])) or item.get("AlbumArtist", "")
            if self._fuzzy_match(item_artist, artist_name):
                candidates.append(item)

        if not candidates:
            return None

        candidates.sort(key=lambda i: i.get("RunTimeTicks", 0))
        return candidates[0]

    def create_playlist(self, name: str, item_ids: list[str]) -> str:
        params = {
            "Name": name,
            "Ids": ",".join(item_ids),
            "UserId": self.user_id,
        }
        data = self._post("/Playlists", params=params)
        playlist_id = data.get("Id") or data.get("PlaylistId")
        if not playlist_id:
            raise ValueError("Jellyfin did not return a playlist id")
        return playlist_id

    def add_tracks_to_playlist(self, playlist_id: str, item_ids: list[str]) -> None:
        params = {"Ids": ",".join(item_ids)}
        self._post(f"/Playlists/{playlist_id}/Items", params=params)

    def _fuzzy_match(self, a: str, b: str) -> bool:
        if not a or not b:
            return False
        a_norm = a.lower().strip()
        b_norm = b.lower().strip()
        if a_norm == b_norm:
            return True
        return fuzz.token_sort_ratio(a_norm, b_norm) >= 85
