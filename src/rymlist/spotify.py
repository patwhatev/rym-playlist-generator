"""Spotify access. Searching uses app credentials; playlists need a user login."""
import os
import re
import sys

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.cache_handler import CacheFileHandler, MemoryCacheHandler
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from .cache import DiskCache
from .paths import CACHE

SCOPES = 'playlist-modify-private playlist-modify-public playlist-read-private'
MAX_QUERY = 250  # spotify rejects longer search queries with a 400
CREDIT_SPLIT = re.compile(r'\s+/\s+|\s*;\s*|,\s+|\s+&\s+|\s+(?:and|with|feat\.?|ft\.?|x)\s+', re.I)
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


def _shorten(text, limit):
    """Cut at a word boundary so the query stays a sensible search."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0]
    return cut if cut else text[:limit]


def album_query(title=None, artist=None):
    """Spotify search query for an album, kept under MAX_QUERY. A long multi-artist credit
    ("Studio A / Studio B / ...") is cut to its first artist before anything is trimmed."""
    title = _clean(title) if title else None
    artist = _clean(artist) if artist else None
    if title and artist:
        q = f'album:{title} artist:{artist}'
        if len(q) > MAX_QUERY:
            artist = CREDIT_SPLIT.split(artist)[0].strip() or artist
            room = MAX_QUERY - len('album: artist:')
            artist = _shorten(artist, max(room // 3, room - len(title)))
            title = _shorten(title, room - len(artist))
            q = f'album:{title} artist:{artist}'
        return q
    if artist:
        if len('artist:' + artist) > MAX_QUERY:
            artist = _shorten(CREDIT_SPLIT.split(artist)[0], MAX_QUERY - len('artist:'))
        return 'artist:' + artist
    return _shorten(title, MAX_QUERY)


def _bad_query(err, q):
    """A 400 is about this one query (too long, odd characters): no result, keep going.
    Anything else (auth, rate limit after retries, outage) is raised."""
    if isinstance(err, SpotifyException) and err.http_status == 400:
        print(f'  spotify rejected the search {q[:80]!r}…: {err.msg.splitlines()[-1].strip()}', file=sys.stderr, flush=True)
        return True
    return False


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

        try:
            return self.search_cache.get_or_fetch(q, fetch)
        except SpotifyException as err:
            if _bad_query(err, q):
                return []  # not cached, so a fixed query gets a real try next time
            raise

    def search_albums(self, title=None, artist=None):
        """Lightweight album hits (no upc/tracks) for pre-filtering."""
        return self._search(album_query(title, artist))

    def search_artists(self, name):
        q = _shorten(_clean(name), MAX_QUERY)

        def fetch():
            res = self.sp.search(q=q, type='artist', limit=10)
            return [{'id': a['id'], 'name': a['name'], 'url': a['external_urls']['spotify']}
                    for a in res['artists']['items'] if a]

        try:
            return self.search_cache.get_or_fetch('artist-search:' + q, fetch)
        except SpotifyException as err:
            if _bad_query(err, q):
                return []
            raise

    def album(self, album_id, need_duration=False):
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
                'duration_s': round(sum(t.get('duration_ms') or 0 for t in tracks if t) / 1000),
            }

        # entries cached before durations were kept are fetched again, but only when asked for
        valid = (lambda a: 'duration_s' in a) if need_duration else None
        return self.album_cache.get_or_fetch(album_id, fetch, valid=valid)
