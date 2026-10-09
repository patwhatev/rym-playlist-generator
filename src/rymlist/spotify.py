"""Spotify access. Searching uses app credentials; playlists need a user login."""
import os
import re

import spotipy
from spotipy.cache_handler import CacheFileHandler, MemoryCacheHandler
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from .cache import DiskCache
from .paths import CACHE

SCOPES = 'playlist-modify-private playlist-modify-public playlist-read-private'
ALBUM_ID = re.compile(r'(?:open\.spotify\.com/album/|spotify:album:)([A-Za-z0-9]{22})')


def _credentials():
    client_id = os.environ.get('SPOTIFY_CLIENT') or os.environ.get('SPOTIPY_CLIENT_ID')
    secret = os.environ.get('SPOTIFY_SECRET') or os.environ.get('SPOTIPY_CLIENT_SECRET')
    if not client_id or not secret:
        raise SystemExit('Set SPOTIFY_CLIENT and SPOTIFY_SECRET in your environment (see README).')
    return client_id, secret


def album_id_from(value):
    m = ALBUM_ID.search(value)
    if m:
        return m.group(1)
    if re.fullmatch(r'[A-Za-z0-9]{22}', value):
        return value
    raise ValueError(f'not a spotify album url or id: {value}')


def _clean(value):
    # spotify's field filters choke on quotes and colons
    return ' '.join(re.sub(r'["“”:]', ' ', value).split())


class Spotify:
    def __init__(self, user=False, refresh=False):
        client_id, secret = _credentials()
        if user:
            auth = SpotifyOAuth(
                client_id=client_id,
                client_secret=secret,
                redirect_uri=os.environ.get('SPOTIFY_REDIRECT_URI', 'http://127.0.0.1:8888/callback'),
                scope=SCOPES,
                cache_handler=CacheFileHandler(cache_path=str(CACHE / 'spotify-token')),
            )
        else:
            auth = SpotifyClientCredentials(client_id=client_id, client_secret=secret, cache_handler=MemoryCacheHandler())
        CACHE.mkdir(exist_ok=True)
        self.sp = spotipy.Spotify(auth_manager=auth, requests_timeout=20, retries=5)
        self.search_cache = DiskCache('spotify-search', refresh)
        self.album_cache = DiskCache('spotify-album', refresh)

    def _search(self, q):
        def fetch():
            res = self.sp.search(q=q, type='album', limit=10)
            return [{
                'id': a['id'],
                'name': a['name'],
                'artists': [{'id': x['id'], 'name': x['name']} for x in a['artists']],
                'release_date': a.get('release_date'),
            } for a in res['albums']['items'] if a]

        return self.search_cache.get_or_fetch(q, fetch)

    def search_albums(self, title=None, artist=None):
        """Lightweight album hits (no upc/tracks) for pre-filtering."""
        if title and artist:
            return self._search(f'album:{_clean(title)} artist:{_clean(artist)}')
        if artist:
            return self._search(f'artist:{_clean(artist)}')
        return self._search(_clean(title))

    def search_artists(self, name):
        q = _clean(name)

        def fetch():
            res = self.sp.search(q=q, type='artist', limit=10)
            return [{'id': a['id'], 'name': a['name'], 'url': a['external_urls']['spotify']}
                    for a in res['artists']['items'] if a]

        return self.search_cache.get_or_fetch('artist-search:' + q, fetch)

    def album(self, album_id):
        def fetch():
            album = self.sp.album(album_id)
            tracks = album['tracks']['items']
            page = album['tracks']
            while page.get('next'):
                page = self.sp.next(page)
                tracks += page['items']
            return {
                'id': album['id'],
                'url': album['external_urls']['spotify'],
                'name': album['name'],
                'album_type': album['album_type'],
                'artists': [{'id': a['id'], 'name': a['name']} for a in album['artists']],
                'release_date': album['release_date'],
                'total_tracks': album['total_tracks'],
                'upc': (album.get('external_ids') or {}).get('upc'),
                'label': album.get('label'),
                'track_uris': [t['uri'] for t in tracks if t and t.get('uri')],
            }

        return self.album_cache.get_or_fetch(album_id, fetch)
