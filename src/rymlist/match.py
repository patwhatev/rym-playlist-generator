"""Find the Spotify release for a rym item and decide how much to trust it.

Statuses, strongest first:
  verified   independent confirmation: musicbrainz links this exact spotify album, the
             barcode matches, or the spotify artist is the one musicbrainz links and the
             year/track count agree
  likely     artist + title match closely, year agrees, no contradictions, and no other
             spotify artist with the same name turned up
  uncertain  names match but something is off (reissue date, no year, ambiguous artist
             name, near-miss spelling) - needs a human look
  not_found  nothing on spotify passed, rejected candidates are kept with the reason
"""
from .normalize import best_similarity, item_artist_names, item_titles, norm, norm_title

RANK = {'manual': 4, 'verified': 3, 'likely': 2, 'uncertain': 1}
NAME_THRESHOLD = 90
NEAR_MISS = 85


def _year(date):
    return int(date[:4]) if date and date[:4].isdigit() else None


def _summary(album):
    return {k: album[k] for k in ('id', 'url', 'name', 'album_type', 'release_date', 'total_tracks', 'upc', 'label')} | {
        'artists': [a['name'] for a in album['artists']],
    }


def evaluate(item, album, mb, same_name_artist_ids):
    """Judge one spotify album against a rym item. Returns a dict with status or rejection."""
    sp_artist_names = [a['name'] for a in album['artists']]
    sp_artist_names.append(' & '.join(sp_artist_names))
    artist_score = best_similarity(item_artist_names(item), sp_artist_names)
    title_score = best_similarity(item_titles(item), [album['name']], norm_title)

    if artist_score < NEAR_MISS or title_score < NEAR_MISS:
        return {'status': None, 'reason': f'artist {artist_score:.0f} / title {title_score:.0f} similarity too low'}

    evidence, warnings = [], []
    evidence.append(f'artist similarity {artist_score:.0f}, title similarity {title_score:.0f}')

    rym_year, sp_year = item.get('year'), _year(album['release_date'])
    sp_artist_ids = {a['id'] for a in album['artists']}
    upc = (album.get('upc') or '').lstrip('0')

    confirmed_release = False
    confirmed_artist = False
    if mb:
        if album['id'] in mb['spotify_album_ids']:
            confirmed_release = True
            evidence.append('MusicBrainz links this exact Spotify album')
        if upc and upc in mb['barcodes']:
            confirmed_release = True
            evidence.append(f'barcode {album["upc"]} matches a MusicBrainz release')
        if mb['spotify_artist_ids'] and not mb['various_artists']:
            if sp_artist_ids & mb['spotify_artist_ids']:
                confirmed_artist = True
                evidence.append('Spotify artist is the one MusicBrainz links')
            elif not confirmed_release:
                return {'status': None, 'reason': 'same-name artist: MusicBrainz links this artist to a different Spotify profile'}
        if album['total_tracks'] in mb['track_counts']:
            evidence.append(f'track count {album["total_tracks"]} matches MusicBrainz')

    year_ok = False
    if rym_year and sp_year:
        if abs(sp_year - rym_year) <= 1:
            year_ok = True
            evidence.append(f'year {sp_year} matches')
        elif sp_year < rym_year - 1 and not confirmed_release:
            return {'status': None, 'reason': f'Spotify release ({sp_year}) predates the RYM release ({rym_year})'}
        else:
            warnings.append(f'Spotify date {album["release_date"]} vs RYM {rym_year}: probably a reissue')
    elif not rym_year:
        warnings.append('RYM has no year for this release')

    ambiguous = same_name_artist_ids - sp_artist_ids
    if ambiguous:
        warnings.append(f'{len(ambiguous)} other Spotify artist(s) share this name')

    if min(artist_score, title_score) < NAME_THRESHOLD:
        warnings.append('name is a near-miss, check spelling/edition')

    if confirmed_release or (confirmed_artist and (year_ok or any('track count' in e for e in evidence))):
        status = 'verified'
    elif not warnings and year_ok:
        status = 'likely'
    else:
        status = 'uncertain'

    return {
        'status': status,
        'score': artist_score + title_score + (20 if year_ok else 0) + (50 if confirmed_release else 0),
        'evidence': evidence,
        'warnings': warnings,
    }


def _names_match(item, hit, threshold):
    names = [a['name'] for a in hit['artists']] + [' & '.join(a['name'] for a in hit['artists'])]
    return (best_similarity(item_titles(item), [hit['name']], norm_title) >= threshold
            and best_similarity(item_artist_names(item), names) >= threshold)


def candidates(spotify, item):
    """Search spotify a few ways (stopping once a strong name match shows up), then fetch
    full album details only for hits whose names are close enough to be worth judging."""
    titles = item_titles(item)
    artists = item_artist_names(item)
    queries = [(t, a) for t in titles for a in artists[:4]]
    queries += [(None, a) for a in artists[:3]]          # artist's albums, catches title spelling differences
    queries += [(f'{t} {artists[0]}', None) for t in titles]

    hits, seen = [], set()
    for title, artist in queries:
        new = [h for h in spotify.search_albums(title, artist) if h['id'] not in seen]
        seen.update(h['id'] for h in new)
        hits += new
        if any(_names_match(item, h, NAME_THRESHOLD) for h in new):
            break

    return [spotify.album(h['id']) for h in hits if _names_match(item, h, NEAR_MISS)], hits


def artist_on_spotify(spotify, item):
    names = item_artist_names(item)
    for artist in item['artists'][:2]:
        for hit in spotify.search_artists(artist['name_latin'] or artist['name']):
            if best_similarity(names, [hit['name']]) >= NAME_THRESHOLD:
                return hit['name']
    return None


def match_item(item, spotify, mb_client=None, override=None):
    base = {k: item[k] for k in ('position', 'rym_url', 'rym_type', 'type_label', 'title', 'title_latin', 'year', 'credited_as')}
    base['artists'] = [a['name'] + (f' [{a["name_latin"]}]' if a['name_latin'] else '') for a in item['artists']]

    if override and override.get('skip'):
        return base | {'status': 'skipped', 'evidence': ['skipped in overrides.toml'], 'warnings': [], 'rejected': []}

    mb = mb_client.evidence_for(item) if mb_client else None
    mb_out = None
    if mb:
        mb_out = {'url': mb['url'], 'first_release_date': mb['first_release_date'],
                  'linked_spotify_artists': sorted(mb['spotify_artist_ids'])}

    if override and override.get('spotify'):
        from .spotify import album_id_from
        album = spotify.album(album_id_from(override['spotify']))
        return base | {'status': 'manual', 'spotify': _summary(album), 'track_uris': album['track_uris'],
                       'evidence': ['pinned in overrides.toml'], 'warnings': [], 'rejected': [], 'musicbrainz': mb_out}

    albums, hits = candidates(spotify, item)

    # every spotify artist whose name matches ours: more than one means a same-name collision is possible
    artist_names = item_artist_names(item)
    same_name_ids = {a['id'] for hit in hits for a in hit['artists']
                     if best_similarity(artist_names, [a['name']]) >= NAME_THRESHOLD and norm(a['name']) != 'various artists'}

    best, rejected = None, []
    for album in albums:
        verdict = evaluate(item, album, mb, same_name_ids)
        if not verdict['status']:
            if 'too low' not in verdict['reason']:
                rejected.append(_summary(album) | {'reason': verdict['reason']})
            continue
        key = (RANK[verdict['status']], verdict['score'])
        if not best or key > best[0]:
            best = (key, album, verdict)

    if not best:
        if rejected:
            reason = 'no Spotify candidates passed validation'
        else:
            on_spotify = artist_on_spotify(spotify, item)
            reason = (f'an artist named {on_spotify} is on Spotify, but not this release' if on_spotify
                      else 'artist not found on Spotify')
        return base | {'status': 'not_found', 'reason': reason, 'evidence': [], 'warnings': [],
                       'rejected': rejected, 'musicbrainz': mb_out}

    _, album, verdict = best
    return base | {
        'status': verdict['status'],
        'spotify': _summary(album),
        'track_uris': album['track_uris'],
        'evidence': verdict['evidence'],
        'warnings': verdict['warnings'],
        'rejected': rejected,
        'musicbrainz': mb_out,
    }
