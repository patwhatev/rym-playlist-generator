"""Create or refresh Spotify playlists for a matched list: one per section, or one for the whole list."""
from datetime import datetime, timezone

from .spotify import Spotify

MAX_TRACKS = 10_000  # spotify's per-playlist cap
INCLUDED = {'manual', 'verified', 'likely'}
DESCRIPTION = '╭∩╮（︶︿︶）╭∩╮ Created by @patwhatev'


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def track_uris(results, include_uncertain=False):
    statuses = INCLUDED | ({'uncertain'} if include_uncertain else set())
    picked = [r for r in results if r['status'] in statuses]
    uris, seen = [], set()
    for r in picked:  # list order, then album track order
        for uri in r['track_uris']:
            if uri not in seen:
                seen.add(uri)
                uris.append(uri)
    return uris, len(picked)


def _sync(sp, name, uris, existing_ids, public):
    """Write uris to playlist(s) called name, reusing existing ids. Returns [{id, name, url, tracks}]."""
    parts = list(chunks(uris, MAX_TRACKS))
    out = []
    for n, part in enumerate(parts):
        part_name = name if len(parts) == 1 else f'{name} ({n + 1}/{len(parts)})'
        playlist_id = existing_ids[n] if n < len(existing_ids) else None
        if playlist_id:
            sp.playlist_change_details(playlist_id, name=part_name, public=public, description=DESCRIPTION)
            sp.playlist_replace_items(playlist_id, part[:100])
            rest = part[100:]
        else:
            playlist_id = sp.user_playlist_create(sp.me()['id'], part_name, public=public, description=DESCRIPTION)['id']
            rest = part
        for batch in chunks(rest, 100):
            sp.playlist_add_items(playlist_id, batch)
        out.append({'id': playlist_id, 'name': part_name,
                    'url': f'https://open.spotify.com/playlist/{playlist_id}', 'tracks': len(part)})
    return out


def playlist_name(list_title, section):
    return f'RYM {list_title}' + (f' — {section}' if section else '')


def build(list_title, groups, previous, public=False, include_uncertain=False):
    """Spotify. groups: [(section or None, results)]. previous: this service's entries from
    status['playlists']. Returns the new entries: one per section, url None when nothing matched."""
    before = {p.get('section'): p for p in previous or []}
    sp = None
    entries = []
    for section, results in groups:
        uris, releases = track_uris(results, include_uncertain)
        old = before.get(section) or {}
        name = playlist_name(list_title, section)
        entry = {
            'service': 'spotify',
            'section': section,
            'pages': sorted({r['page'] for r in results if r.get('page')}),
            'name': name,
            'releases': releases,
            'tracks': len(uris),
        }
        if not uris:
            entries.append(entry | {'url': None, 'note': 'nothing in this section was found on Spotify'})
            continue
        sp = sp or Spotify(user=True).sp
        parts = _sync(sp, name, uris, [p['id'] for p in old.get('parts', [])], public)
        entries.append(entry | {
            'url': parts[0]['url'],
            'created_at': old.get('created_at') or _now(),
            'updated_at': _now(),
            'parts': parts,
        })
    return entries


# ---------------------------------------------------------------- youtube

YT_MAX = 5000  # videos per youtube playlist


def video_ids(results, include_uncertain=False):
    statuses = INCLUDED | ({'uncertain'} if include_uncertain else set())
    picked = [r for r in results if r['status'] in statuses]
    return list(dict.fromkeys(v for r in picked for v in r.get('video_ids', []))), len(picked)


def build_youtube(list_title, groups, previous, privacy='private', include_uncertain=False, save=None):
    """YouTube. Same shape as build(). The playlist is synced to the wanted videos: what's
    already there (read back from youtube, so a resume never duplicates) is kept, the rest is
    added in list order, stale items are removed. save(entries) is called after every playlist
    and before a QuotaExceeded propagates, so progress survives a quota stop."""
    from .youtube import QuotaExceeded, YouTubeAPI, video_url

    before = {p.get('section'): p for p in previous or []}
    api = None
    entries = []

    def checkpoint(*extra):
        # finished sections, plus last run's entries for the rest so their playlist ids survive
        if save:
            done = entries + list(extra)
            sections_done = {e['section'] for e in done}
            save(done + [p for p in previous or [] if p.get('section') not in sections_done])

    for section, results in groups:
        wanted, releases = video_ids(results, include_uncertain)
        old = before.get(section) or {}
        name = playlist_name(list_title, section)
        entry = {'service': 'youtube', 'section': section,
                 'pages': sorted({r['page'] for r in results if r.get('page')}),
                 'name': name, 'releases': releases}
        if not wanted:
            entries.append(entry | {'videos': 0, 'tracks': 0, 'url': None, 'note': 'nothing in this section was found on YouTube'})
            continue
        api = api or YouTubeAPI()
        playable = api.playable(wanted)
        dropped = [v for v in wanted if v not in playable]
        wanted = [v for v in wanted if v in playable]
        parts = [wanted[i:i + YT_MAX] for i in range(0, len(wanted), YT_MAX)] or [[]]
        old_parts = old.get('parts', [])
        new_parts = []
        try:
            for n, part in enumerate(parts):
                part_name = name if len(parts) == 1 else f'{name} ({n + 1}/{len(parts)})'
                pid = old_parts[n]['id'] if n < len(old_parts) else None
                present = api.playlist_items(pid) if pid else None
                if present is None:
                    pid = api.create_playlist(part_name, DESCRIPTION, privacy)
                    present = []
                elif old_parts[n].get('name') != part_name or old.get('privacy') != privacy:
                    api.update_playlist(pid, part_name, DESCRIPTION, privacy)
                info = {'id': pid, 'name': part_name, 'url': f'https://www.youtube.com/playlist?list={pid}',
                        'tracks': len(part)}
                new_parts.append(info)
                want = set(part)
                for item_id, vid in present:
                    if vid not in want:
                        api.remove(item_id)
                have = {vid for _, vid in present}
                refused = []
                for vid in part:
                    if vid not in have:
                        if not api.add(pid, vid):
                            refused.append(vid)
                        have.add(vid)
                info['tracks'] = len(part) - len(refused)
                dropped += refused
        except BaseException as err:
            # save what exists (a new playlist's id above all) so the next run resumes instead of duplicating
            partial = entry | {'url': (new_parts or old_parts or [{}])[0].get('url'), 'parts': new_parts or old_parts,
                               'videos': len(wanted), 'tracks': sum(p['tracks'] for p in new_parts),
                               'privacy': privacy, 'incomplete': True,
                               'created_at': old.get('created_at') or _now(), 'updated_at': _now()}
            checkpoint(partial)
            if isinstance(err, QuotaExceeded):
                print(f'YouTube API quota used this run: {api.units} units (10,000/day)', flush=True)
            raise
        entries.append(entry | {
            'url': new_parts[0]['url'],
            'videos': len(wanted),
            'tracks': sum(p['tracks'] for p in new_parts),
            'unavailable': [video_url(v) for v in dropped],
            'privacy': privacy,
            'created_at': old.get('created_at') or _now(),
            'updated_at': _now(),
            'parts': new_parts,
        })
        checkpoint()
    if api:
        print(f'YouTube API quota used this run: {api.units} units (10,000/day)', flush=True)
    return entries
