"""Tiny on-disk json cache so re-running a list doesn't re-hit Spotify or MusicBrainz."""
import hashlib
import json

from .paths import CACHE


class DiskCache:
    def __init__(self, namespace, refresh=False):
        self.dir = CACHE / namespace
        self.dir.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh

    def _path(self, key):
        return self.dir / (hashlib.sha1(key.encode()).hexdigest() + '.json')

    def get_or_fetch(self, key, fetch, valid=None):
        """Cached value, or fetch() and cache it. `valid(value)` False refetches an outdated entry."""
        path = self._path(key)
        if path.exists() and not self.refresh:
            value = json.loads(path.read_text())
            if valid is None or valid(value):
                return value
        value = fetch()
        path.write_text(json.dumps(value))
        return value
