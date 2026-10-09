"""Discogs lookups, used sparingly: only when nothing cheaper gave a runtime for a YouTube
candidate, or to firm up an "uncertain" Spotify match by barcode. Everything is cached.

Auth is the app's consumer key + secret in a header (no OAuth, no callback url needed).
"""
import os
import re
import time

import requests

from .cache import DiskCache
from .normalize import best_similarity, item_artist_names, item_titles, norm_title

API = 'https://api.discogs.com'
MAX_DETAIL_LOOKUPS = 3  # master + a couple of pressings, then give up on a runtime


def parse_duration(text):
    """'4:31' -> 271, '1:02:03' -> 3723, '' -> None"""
    parts = (text or '').strip().split(':')
    if not all(p.isdigit() for p in parts) or len(parts) not in (2, 3):
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds


def tracklist_runtime(tracklist):
    """(total seconds or None, track count). None unless every track has a duration."""
    tracks = []
    for t in tracklist or []:
        if t.get('type_') == 'index':
            tracks += t.get('sub_tracks') or [t]
        elif t.get('type_', 'track') == 'track':
            tracks.append(t)
    durations = [parse_duration(t.get('duration')) for t in tracks]
    if tracks and all(durations):
        return sum(durations), len(tracks)
    return None, len(tracks)


def _digits(barcode):
    return re.sub(r'\D', '', barcode or '').lstrip('0')


class Discogs:
    min_interval = 1.1  # authenticated limit is 60/min

    def __init__(self, refresh=False):
        key = os.environ.get('DISCOGS_CONSUMER')
        secret = os.environ.get('DISCOGS_SECRET')
        if not key or not secret:
            raise SystemExit('Set DISCOGS_CONSUMER and DISCOGS_SECRET in your environment (see README).')
        self.cache = DiskCache('discogs', refresh)
        self.session = requests.Session()
        self.session.headers['Authorization'] = f'Discogs key={key}, secret={secret}'
        contact = os.environ.get('MB_CONTACT', 'https://github.com/patwhatev/rymlist')
        self.session.headers['User-Agent'] = f'rymlist/0.1 +{contact}'
        self._last = 0.0
        self.requests = 0  # uncached requests this run, for the summary

    @classmethod
    def maybe(cls, refresh=False):
        """A client if credentials are set, else None (discogs is optional)."""
        if os.environ.get('DISCOGS_CONSUMER') and os.environ.get('DISCOGS_SECRET'):
            return cls(refresh)
        return None

    def _get(self, path, **params):
        key = path + '?' + '&'.join(f'{k}={params[k]}' for k in sorted(params))

        def fetch():
            for attempt in range(5):
                wait = self.min_interval - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
                self.requests += 1
                res = self.session.get(f'{API}/{path}', params=params, timeout=30)
                if res.status_code == 429:
                    time.sleep(10 * (attempt + 1))
                    continue
                if res.status_code == 404:
                    return None
                res.raise_for_status()
                return res.json()
            raise RuntimeError(f'discogs kept rate limiting {path}')

        return self.cache.get_or_fetch(key, fetch)

    def releases(self, item):
        """Search results for this release whose artist, title and year agree with rym."""
        titles, artists = item_titles(item), item_artist_names(item)
        artist = item.get('credited_as') or item['artists'][0]['name']
        data = self._get('database/search', type='release', artist=artist, release_title=item['title'], per_page=25) or {}
        out = []
        for r in data.get('results', []):
            # "Artist - Title"; artists can contain " - " too, so try every split point
            pieces = r.get('title', '').split(' - ')
            splits = [(' - '.join(pieces[:i]), ' - '.join(pieces[i:])) for i in range(1, len(pieces))]
            if not any(best_similarity(artists, [a]) >= 90 and best_similarity(titles, [t], norm_title) >= 90
                       for a, t in splits):
                continue
            year = int(r['year']) if str(r.get('year', '')).isdigit() else None
            if item.get('year') and year and year < item['year'] - 1:
                continue  # can't predate the original
            out.append(r)
        return out

    def barcodes(self, item):
        """Barcodes (digits only) of every matching pressing. One search request."""
        return {_digits(b) for r in self.releases(item) for b in r.get('barcode') or [] if _digits(b)}

    def runtime(self, item):
        """{'seconds', 'tracks', 'url'} from the first matching master/pressing that lists every
        track duration, or None. At most one search + MAX_DETAIL_LOOKUPS detail requests."""
        found = self.releases(item)
        if not found:
            return None
        lookups = []
        for master_id in dict.fromkeys(r.get('master_id') for r in found if r.get('master_id')):
            lookups.append(f'masters/{master_id}')
        # cds and digital releases list durations far more often than vinyl or tapes
        by_format = sorted(found, key=lambda r: not ({'CD', 'File'} & set(r.get('format') or [])))
        lookups += [f'releases/{r["id"]}' for r in by_format]
        for path in list(dict.fromkeys(lookups))[:MAX_DETAIL_LOOKUPS]:
            detail = self._get(path)
            seconds, tracks = tracklist_runtime((detail or {}).get('tracklist'))
            if seconds:
                return {'seconds': seconds, 'tracks': tracks, 'url': (detail.get('uri') or f'https://www.discogs.com/{path}')}
        return None
