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

    def get_or_fetch(self, key, fetch):
        path = self._path(key)
        if path.exists() and not self.refresh:
            return json.loads(path.read_text())
        value = fetch()
        path.write_text(json.dumps(value))
        return value
