"""Create or refresh the Spotify playlist for a matched list."""
from .spotify import Spotify

MAX_TRACKS = 10_000  # spotify's per-playlist cap
INCLUDED = {'manual', 'verified', 'likely'}
DESCRIPTION = '╭∩╮（︶︿︶）╭∩╮ Created by @patwhatev'


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def build(result_doc, existing, name=None, public=False, include_uncertain=False):
    statuses = INCLUDED | ({'uncertain'} if include_uncertain else set())
    picked = [r for r in result_doc['results'] if r['status'] in statuses]
    uris, seen = [], set()
    for r in picked:  # list order, then album track order
        for uri in r['track_uris']:
            if uri not in seen:
                seen.add(uri)
                uris.append(uri)
    if not uris:
        return []  # nothing on spotify, no playlist to make

    spotify = Spotify(user=True)
    sp = spotify.sp
    name = name or f'RYM {result_doc["list"]["title"]}'
    description = DESCRIPTION

    parts = list(chunks(uris, MAX_TRACKS))
    playlists = []
    for n, part in enumerate(parts):
        part_name = name if len(parts) == 1 else f'{name} ({n + 1}/{len(parts)})'
        playlist_id = existing[n]['id'] if n < len(existing) else None
        if playlist_id:
            sp.playlist_change_details(playlist_id, name=part_name, public=public, description=description)
            sp.playlist_replace_items(playlist_id, part[:100])
            rest = part[100:]
        else:
            created = sp.user_playlist_create(sp.me()['id'], part_name, public=public, description=description)
            playlist_id = created['id']
            rest = part
        for batch in chunks(rest, 100):
            sp.playlist_add_items(playlist_id, batch)
        playlists.append({
            'id': playlist_id,
            'name': part_name,
            'url': f'https://open.spotify.com/playlist/{playlist_id}',
            'tracks': len(part),
            'releases': len(picked),
        })
    return playlists
