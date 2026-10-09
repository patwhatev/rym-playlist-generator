"""YouTube access. Searching reads public YouTube search results through yt-dlp (metadata only,
nothing is downloaded, no API quota). Playlists are written with the official YouTube Data API,
which has a daily quota: each video added costs 50 of 10,000 units.
"""
import re
import sys
import time

from . import paths
from .cache import DiskCache

SCOPES = ['https://www.googleapis.com/auth/youtube']
CLIENT_FILE = paths.SECRETS / 'youtube-client.json'
TOKEN_FILE = paths.SECRETS / 'youtube-token.json'
VIDEO_ID = re.compile(r'(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})')
MAX_TITLE = 150


class QuotaExceeded(Exception):
    """The YouTube API's daily quota is used up. It resets at midnight Pacific time."""


def video_id_from(value):
    m = VIDEO_ID.search(value)
    if m:
        return m.group(1)
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
        return value
    raise ValueError(f'not a youtube video url or id: {value}')


def video_url(video_id):
    return f'https://www.youtube.com/watch?v={video_id}'


def fmt_duration(seconds):
    if seconds is None:
        return '?'
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f'{h}:{m:02}:{s:02}' if h else f'{m}:{s:02}'


class YouTubeSearch:
    min_interval = 1.5  # be a polite searcher; uncached searches only

    def __init__(self, refresh=False):
        self.cache = DiskCache('youtube-search', refresh)
        self._ydl = None
        self._last = 0.0
        self.searches = 0

    def _client(self):
        if self._ydl is None:
            import yt_dlp
            self._ydl = yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'extract_flat': True, 'skip_download': True})
        return self._ydl

    def search(self, query, n=15):
        def fetch():
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.searches += 1
            info = self._client().extract_info(f'ytsearch{n}:{query}', download=False)
            return [{
                'id': e['id'],
                'title': e.get('title') or '',
                'duration': e.get('duration'),
                'channel': e.get('channel') or e.get('uploader') or '',
                'channel_id': e.get('channel_id'),
                'views': e.get('view_count'),
            } for e in info.get('entries') or [] if e and e.get('id') and e.get('live_status') not in ('is_live', 'is_upcoming')]

        return self.cache.get_or_fetch(f'{n}:{query}', fetch)


class YouTubeAPI:
    """Just the playlist calls rymlist needs, with a running count of quota units used."""
    COST = {'list': 1, 'insert': 50, 'update': 50, 'delete': 50}

    def __init__(self):
        from googleapiclient.discovery import build
        self.yt = build('youtube', 'v3', credentials=self._credentials(), cache_discovery=False)
        self.units = 0

    @staticmethod
    def _credentials():
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not CLIENT_FILE.exists():
            raise SystemExit(f'YouTube needs an OAuth client file at secrets/{CLIENT_FILE.name} (see README, "YouTube setup").')
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES) if TOKEN_FILE.exists() else None
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                creds = None  # testing-mode apps get logged out every 7 days: log in again
        if not creds or not creds.valid:
            print('Opening a Google login in your browser for YouTube...', flush=True)
            creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES).run_local_server(port=0)
        TOKEN_FILE.parent.mkdir(exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json())
        return creds

    def _call(self, kind, request):
        from googleapiclient.errors import HttpError
        for attempt in range(4):
            try:
                res = request.execute()
                self.units += self.COST[kind]
                return res
            except HttpError as err:
                reason = str(err.content or b'', 'utf-8', 'replace')
                if 'quotaExceeded' in reason or 'dailyLimitExceeded' in reason:
                    raise QuotaExceeded('YouTube API daily quota used up; it resets at midnight Pacific time') from err
                # 409 "operation aborted" / SERVICE_UNAVAILABLE: youtube tripping over back-to-back edits
                if err.resp.status in (409, 429, 500, 503) or 'rateLimitExceeded' in reason or 'SERVICE_UNAVAILABLE' in reason:
                    time.sleep(5 * 2 ** attempt)
                    continue
                raise
        return request.execute()

    def create_playlist(self, title, description, privacy):
        body = {'snippet': {'title': title[:MAX_TITLE], 'description': description}, 'status': {'privacyStatus': privacy}}
        return self._call('insert', self.yt.playlists().insert(part='snippet,status', body=body))['id']

    def update_playlist(self, playlist_id, title, description, privacy):
        body = {'id': playlist_id, 'snippet': {'title': title[:MAX_TITLE], 'description': description},
                'status': {'privacyStatus': privacy}}
        self._call('update', self.yt.playlists().update(part='snippet,status', body=body))

    def playlist_items(self, playlist_id):
        """[(playlist item id, video id)] currently in the playlist. 1 unit per 50."""
        from googleapiclient.errors import HttpError
        out, token = [], None
        while True:
            try:
                res = self._call('list', self.yt.playlistItems().list(
                    part='contentDetails', playlistId=playlist_id, maxResults=50, pageToken=token))
            except HttpError as err:
                if err.resp.status == 404:
                    return None  # playlist was deleted on youtube
                raise
            out += [(i['id'], i['contentDetails']['videoId']) for i in res.get('items', [])]
            token = res.get('nextPageToken')
            if not token:
                return out

    def add(self, playlist_id, video_id):
        """True if added, False if youtube refuses this video (deleted, private, blocked)."""
        from googleapiclient.errors import HttpError
        body = {'snippet': {'playlistId': playlist_id, 'resourceId': {'kind': 'youtube#video', 'videoId': video_id}}}
        time.sleep(0.5)  # back-to-back inserts into one playlist trip youtube's 409s
        try:
            self._call('insert', self.yt.playlistItems().insert(part='snippet', body=body))
            return True
        except HttpError as err:
            if err.resp.status in (400, 403, 404):
                print(f'  youtube refused video {video_id}: {err.reason}', file=sys.stderr, flush=True)
                return False
            raise

    def remove(self, playlist_item_id):
        self._call('delete', self.yt.playlistItems().delete(id=playlist_item_id))

    def playable(self, video_ids):
        """The subset of video_ids that still exist and are public or unlisted. 1 unit per 50."""
        ok = set()
        for i in range(0, len(video_ids), 50):
            res = self._call('list', self.yt.videos().list(part='status', id=','.join(video_ids[i:i + 50]), maxResults=50))
            ok |= {v['id'] for v in res.get('items', [])
                   if v['status'].get('privacyStatus') in ('public', 'unlisted') and v['status'].get('uploadStatus') == 'processed'}
        return ok
