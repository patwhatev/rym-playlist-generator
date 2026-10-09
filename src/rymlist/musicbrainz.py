"""MusicBrainz lookups: an independent source used to confirm a Spotify match is the
same release by the same artist (barcodes, linked Spotify album/artist ids, track counts)."""
import os
import re
import time

import requests

from .cache import DiskCache
from .normalize import best_similarity, item_artist_names, item_titles, norm_title

API = 'https://musicbrainz.org/ws/2'
SPOTIFY_ID = re.compile(r'open\.spotify\.com/(album|artist)/([A-Za-z0-9]{22})')


def _phrase(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


class MusicBrainz:
    min_interval = 1.1  # musicbrainz allows ~1 request/second

    def __init__(self, refresh=False):
        self.cache = DiskCache('musicbrainz', refresh)
        contact = os.environ.get('MB_CONTACT', 'https://github.com/patwhatev/rymlist')
        self.session = requests.Session()
        self.session.headers['User-Agent'] = f'rymlist/0.1 ( {contact} )'
        self._last = 0.0

    def _get(self, path, **params):
        params['fmt'] = 'json'
        key = path + '?' + '&'.join(f'{k}={params[k]}' for k in sorted(params))

        def fetch():
            for attempt in range(5):
                wait = self.min_interval - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
                res = self.session.get(f'{API}/{path}', params=params, timeout=30)
                if res.status_code in (429, 503):
                    time.sleep(2 ** attempt)
                    continue
                if res.status_code == 404:
                    return None
                res.raise_for_status()
                return res.json()
            raise RuntimeError(f'musicbrainz kept rate limiting {path}')

        return self.cache.get_or_fetch(key, fetch)

    def find_release_group(self, item):
        """Best release group for a rym item, or None. Requires title, artist and year to agree."""
        seen, best = set(), None
        titles = item_titles(item)
        artists = item_artist_names(item)
        for title in titles:
            for artist in artists[:3]:
                q = f'releasegroup:{_phrase(title)} AND artist:{_phrase(artist)}'
                if q in seen:
                    continue
                seen.add(q)
                data = self._get('release-group', query=q, limit=10) or {}
                for rg in data.get('release-groups', []):
                    credit_names = [c.get('name') for c in rg.get('artist-credit', [])]
                    credit_names += [c['artist'].get('name') for c in rg.get('artist-credit', []) if c.get('artist')]
                    credit_names.append(''.join(c.get('name', '') + c.get('joinphrase', '') for c in rg.get('artist-credit', [])))
                    t = best_similarity(titles, [rg.get('title')], norm_title)
                    a = best_similarity(artists, credit_names)
                    if t < 90 or a < 90:
                        continue
                    year = (rg.get('first-release-date') or '')[:4]
                    year = int(year) if year.isdigit() else None
                    if item.get('year') and year and abs(year - item['year']) > 1:
                        continue
                    score = t + a + (10 if year == item.get('year') else 0)
                    if not best or score > best[0]:
                        best = (score, rg)
                if best and best[0] >= 200:
                    break
            if best and best[0] >= 200:
                break
        return best[1] if best else None

    def evidence_for(self, item):
        rg = self.find_release_group(item)
        if not rg:
            return None

        info = {
            'release_group_id': rg['id'],
            'url': f"https://musicbrainz.org/release-group/{rg['id']}",
            'first_release_date': rg.get('first-release-date'),
            'barcodes': set(),
            'track_counts': set(),
            'spotify_album_ids': set(),
            'spotify_artist_ids': set(),
            'various_artists': False,
        }

        releases = self._get('release', **{'release-group': rg['id'], 'inc': 'url-rels+media', 'limit': 100}) or {}
        for release in releases.get('releases', []):
            if release.get('barcode'):
                info['barcodes'].add(release['barcode'].lstrip('0'))
            tracks = sum(m.get('track-count', 0) for m in release.get('media', []))
            if tracks:
                info['track_counts'].add(tracks)
            for rel in release.get('relations', []):
                m = SPOTIFY_ID.search((rel.get('url') or {}).get('resource', ''))
                if m and m.group(1) == 'album':
                    info['spotify_album_ids'].add(m.group(2))

        for credit in rg.get('artist-credit', []):
            artist_id = (credit.get('artist') or {}).get('id')
            if artist_id == '89ad4ac3-39f7-470e-963a-56509c546377':  # "Various Artists"
                info['various_artists'] = True
                continue
            artist = self._get(f'artist/{artist_id}', inc='url-rels') if artist_id else None
            for rel in (artist or {}).get('relations', []):
                m = SPOTIFY_ID.search((rel.get('url') or {}).get('resource', ''))
                if m and m.group(1) == 'artist':
                    info['spotify_artist_ids'].add(m.group(2))
        return info
