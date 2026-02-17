"""
Spotify API Client

Handles interactions with the Spotify Web API for fetching artist top tracks.
"""

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials


class SpotifyClient:
    """Client for interacting with Spotify API"""

    def __init__(self, client_id: str, client_secret: str):
        """
        Initialize Spotify client with credentials.

        Args:
            client_id: Spotify API Client ID
            client_secret: Spotify API Client Secret
        """
        auth_manager = SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret
        )
        self.spotify = spotipy.Spotify(auth_manager=auth_manager)

        # Test connection
        self.spotify.search(q="test", type="artist", limit=1)

    def search_artist(self, name: str) -> dict | None:
        """
        Search for an artist on Spotify.

        Args:
            name: Artist name to search for

        Returns:
            Artist dict if found, None otherwise
        """
        results = self.spotify.search(q=f'artist:"{name}"', type='artist', limit=5)

        artists = results.get('artists', {}).get('items', [])

        if not artists:
            # Try broader search
            results = self.spotify.search(q=name, type='artist', limit=5)
            artists = results.get('artists', {}).get('items', [])

        if not artists:
            return None

        # Find best match (prefer exact match, then most followers)
        name_lower = name.lower()
        for artist in artists:
            if artist['name'].lower() == name_lower:
                return artist

        # Return most popular if no exact match
        return max(artists, key=lambda a: a.get('followers', {}).get('total', 0))

    def get_top_tracks(self, artist_name: str, limit: int = 50) -> list[dict]:
        """
        Get top tracks for an artist from Spotify, sorted by popularity.

        Args:
            artist_name: Name of the artist
            limit: Maximum number of tracks to return (default 50 for better matching)

        Returns:
            List of track dicts with 'name', 'album', 'popularity' keys, sorted by popularity
        """
        artist = self.search_artist(artist_name)

        if not artist:
            return []

        artist_id = artist['id']
        tracks = []
        seen_track_ids = set()

        # Get top tracks (Spotify returns max 10 per market)
        try:
            top_tracks = self.spotify.artist_top_tracks(artist_id, country='US')
            for t in top_tracks.get('tracks', []):
                if t['id'] not in seen_track_ids:
                    tracks.append(t)
                    seen_track_ids.add(t['id'])
        except Exception:
            pass

        # Get more tracks from albums to reach our limit
        if len(tracks) < limit:
            try:
                albums = self.spotify.artist_albums(
                    artist_id,
                    album_type='album,single',
                    limit=20
                )

                for album in albums.get('items', []):
                    if len(tracks) >= limit:
                        break

                    try:
                        album_tracks = self.spotify.album_tracks(album['id'])
                        for track in album_tracks.get('items', []):
                            if track['id'] in seen_track_ids:
                                continue
                            full_track = self.spotify.track(track['id'])
                            if full_track:
                                tracks.append(full_track)
                                seen_track_ids.add(track['id'])
                                if len(tracks) >= limit:
                                    break
                    except Exception:
                        continue
            except Exception:
                pass

        tracks.sort(key=lambda t: t.get('popularity', 0), reverse=True)
        tracks = tracks[:limit]

        return [
            {
                'name': t['name'],
                'album': t.get('album', {}).get('name', 'Unknown'),
                'popularity': t.get('popularity', 0),
                'spotify_id': t['id'],
                'release_date': t.get('album', {}).get('release_date', ''),
                'release_year': int(t.get('album', {}).get('release_date', '0')[:4]) if t.get('album', {}).get('release_date', '') else 0
            }
            for t in tracks
        ]

    def get_artist_info(self, artist_name: str) -> dict | None:
        """
        Get detailed artist information from Spotify.

        Args:
            artist_name: Name of the artist

        Returns:
            Dict with artist info or None if not found
        """
        artist = self.search_artist(artist_name)

        if not artist:
            return None

        return {
            'name': artist['name'],
            'genres': artist.get('genres', []),
            'popularity': artist.get('popularity', 0),
            'followers': artist.get('followers', {}).get('total', 0),
            'image_url': artist['images'][0]['url'] if artist.get('images') else None,
            'spotify_url': artist.get('external_urls', {}).get('spotify')
        }
