"""Find a full-album upload on public YouTube for a rym item and decide how much to trust it.

A match needs the artist and the title in the video title (or the artist as the channel), no
review/reaction/cover-style words, and a plausible length:

  with a known runtime (a Spotify match or MusicBrainz; Discogs only when those have nothing
  and its answer could change the outcome, see needs_refining)
    verified   within 5% (at least 90s, less for short releases, see tolerances)
    likely     within 10% (at least 3 min)
    uncertain  within 20% (at least 5 min)
    rejected   further off
  without one, floors by release type (a punk album can be 9 minutes, most albums are 15+)
    album      15 min+ and labelled "full"/official channel -> likely, 15 min+ -> uncertain,
               5-15 min -> uncertain (short: punk/grind?), under 5 min -> rejected
    ep         8 min+ likely/uncertain as above, 3-8 min uncertain, under 3 min rejected
    single     1 min+ -> likely if official channel, else uncertain
"""
import math
import re

from rapidfuzz import fuzz

from .normalize import item_artist_names, item_titles, norm, norm_title
from .youtube import fmt_duration, video_id_from, video_url

RANK = {'manual': 4, 'verified': 3, 'likely': 2, 'uncertain': 1}
NAME_THRESHOLD = 90
INCLUDED = {'manual', 'verified', 'likely'}

FULL_MARKER = re.compile(r'\b(full|complete|entire|whole)\b', re.I)
JUNK = re.compile(r'\b(reaction|reacts?|reviews?|reviewed|covers?|covered|tribute|remix(es|ed)?|top\s?\d+|ranking|ranked|'
                  r'tier\s?list|unboxing|tutorial|lessons?|karaoke|playthrough|interview|documentary|trailer|teaser|'
                  r'snippets?|preview|explained|breakdown|vs\.?|mashup|slowed|reverb|sped\s?up|nightcore|8d|edit|rework|re-?edit)\b', re.I)
LIVE = re.compile(r'\b(live|concert|gig)\b', re.I)

# (likely floor, hard floor, suspiciously long) in seconds, used only without a reference runtime
FLOORS = {
    'album': (15 * 60, 5 * 60, 3 * 3600),
    'ep': (8 * 60, 3 * 60, 50 * 60),
    'single': (60, 45, 25 * 60),
}


def tolerances(r):
    """(verified, likely, needs review) allowed difference in seconds for a runtime of r seconds:
    5% / 10% / 20% for albums, with a floor of 90s / 3 min / 5 min that shrinks for short releases
    (a 4:04 edit of a 5:34 single shouldn't pass)."""
    return (max(0.05 * r, min(90, 0.15 * r)),
            max(0.10 * r, min(180, 0.25 * r)),
            max(0.20 * r, min(300, 0.40 * r)))


def kind(item):
    if item.get('rym_type') in ('single', 'musicvideo', 'video'):
        return 'single'
    if item.get('rym_type') == 'ep':
        return 'ep'
    return 'album'


def queries(item):
    suffix = {'album': 'full album', 'ep': 'full EP', 'single': ''}[kind(item)]
    artist = item.get('credited_as') or item['artists'][0]['name']
    artist = re.split(r'\s+/\s+|;\s*', artist)[0]  # "Studio A / Studio B / ..." -> first credit
    latin_artist = ' & '.join(a['name_latin'] or a['name'] for a in item['artists'][:2])
    latin_title = item.get('title_latin') or item['title']
    out = [f'"{artist}" "{item["title"]}" {suffix}']
    if (latin_artist, latin_title) != (artist, item['title']):
        out.append(f'"{latin_artist}" "{latin_title}" {suffix}')
    out.append(f'{artist} {item["title"]} {suffix}')
    out.append(f'{artist} {item["title"]}')  # uploads not labelled "full album"
    return [' '.join(q.split()) for q in dict.fromkeys(out)]


def contains(needles, hay, normalizer=norm):
    """How well any needle appears inside hay, 0-100. Short needles must appear as whole words."""
    hay = norm(hay)
    best = 0
    for n in {normalizer(x) for x in needles if x}:
        if not n or not hay:
            continue
        if len(n) <= 4:
            score = 100 if re.search(rf'(?<!\w){re.escape(n)}(?!\w)', hay) else 0
        elif len(n) > len(hay):
            score = fuzz.ratio(n, hay)  # hay inside a longer needle doesn't count
        else:
            score = fuzz.partial_ratio(n, hay)
        best = max(best, score)
    return best


def _channel_name(video):
    return re.sub(r'\s*-\s*Topic$', '', video.get('channel') or '')


def names(item, video):
    """(artist score, title score, artist is the channel)."""
    artists = item_artist_names(item)
    various = any(norm(a) == 'various artists' for a in artists)
    title_score = contains(item_titles(item), video['title'], norm_title)
    channel_score = 0 if various else max(contains(artists, _channel_name(video)),
                                          100 if norm(_channel_name(video)) in {norm(a) for a in artists} else 0)
    if various:
        artist_score = 100
    else:
        artist_score = max(contains(artists, video['title']), channel_score if channel_score >= NAME_THRESHOLD else 0)
    return artist_score, title_score, channel_score >= NAME_THRESHOLD


def _junk(item, video):
    rym_words = norm(' '.join([item['title'], item.get('title_latin') or '', item.get('type_label') or '']
                              + item_artist_names(item)))
    m = JUNK.search(video['title'])
    if m and norm(m.group(0)) not in rym_words.split():
        return f'looks like a {m.group(0).lower()}, not the release'
    m = LIVE.search(video['title'])
    if m and 'live' not in rym_words.split():
        return 'a live recording, RYM lists a studio release'
    return None


def judge(item, video, ref):
    """Status for one video. ref: {'seconds', 'source', ...} or None."""
    artist_score, title_score, official = names(item, video)
    if artist_score < NAME_THRESHOLD or title_score < NAME_THRESHOLD:
        return {'status': None, 'reason': 'names', 'quiet': True}
    junk = _junk(item, video)
    if junk:
        return {'status': None, 'reason': junk}
    d = video.get('duration')
    if not d:
        return {'status': None, 'reason': 'no duration (stream or premiere)'}

    evidence = [f'artist similarity {artist_score:.0f}, title similarity {title_score:.0f}']
    warnings = []
    labelled = bool(FULL_MARKER.search(video['title']))
    if labelled:
        evidence.append('labelled as the full release')
    if official:
        evidence.append(f'uploaded by the artist\'s channel ({video["channel"]})')

    if ref:
        r = ref['seconds']
        diff = abs(d - r)
        detail = f'{fmt_duration(d)} vs {fmt_duration(r)} expected ({ref["source"]})'
        verified_tol, likely_tol, review_tol = tolerances(r)
        if diff <= verified_tol:
            status = 'verified'
            evidence.append(f'duration {detail}')
        elif diff <= likely_tol:
            status = 'likely'
            evidence.append(f'duration close: {detail}')
        elif diff <= review_tol:
            status = 'uncertain'
            warnings.append(f'duration off: {detail}')
        else:
            return {'status': None, 'reason': f'wrong length: {detail}'}
        closeness = 1 - diff / review_tol
    else:
        k = kind(item)
        likely_floor, hard_floor, too_long = FLOORS[k]
        if d < hard_floor:
            return {'status': None, 'reason': f'{fmt_duration(d)} is too short for a full {k}'}
        if d < likely_floor:
            status = 'uncertain'
            warnings.append(f'short for a full {k} ({fmt_duration(d)}): fine for punk/grind, check it')
        elif d > too_long:
            status = 'uncertain'
            warnings.append(f'{fmt_duration(d)} is long for one {k}: maybe a discography or compilation upload')
        elif official or (labelled and k != 'single'):
            status = 'likely'
        else:
            status = 'uncertain'
            warnings.append('not labelled as a full release, from a channel that isn\'t the artist\'s')
        evidence.append(f'{fmt_duration(d)} long, no known runtime to compare')
        closeness = 0

    score = (artist_score + title_score + (20 if labelled else 0) + (25 if official else 0)
             + 40 * closeness + math.log10((video.get('views') or 0) + 1))
    return {'status': status, 'score': score, 'evidence': evidence, 'warnings': warnings}


class Runtime:
    """The release's real runtime, looked up lazily. get() tries the cheap sources (a Spotify
    match, MusicBrainz); from_discogs() is separate so it's only asked when it can change the
    outcome (see needs_refining)."""

    def __init__(self, item, spotify_result=None, spotify=None, mb=None, discogs=None):
        self.item, self.spotify_result = item, spotify_result
        self.spotify, self.mb, self.discogs = spotify, mb, discogs
        self.value, self.done, self.asked_discogs = None, False, False

    def get(self):
        if self.done:
            return self.value
        self.done = True
        sr = self.spotify_result
        if self.spotify and sr and sr.get('status') in INCLUDED and sr.get('spotify'):
            album = self.spotify.album(sr['spotify']['id'], need_duration=True)
            if album.get('duration_s'):
                self.value = {'seconds': album['duration_s'], 'tracks': album['total_tracks'],
                              'source': 'Spotify', 'url': album['url']}
                return self.value
        found = self.mb.runtime(self.item) if self.mb else None
        if found:
            self.value = found | {'source': 'MusicBrainz'}
        return self.value

    def from_discogs(self):
        if self.value or self.asked_discogs or not self.discogs:
            return self.value
        self.asked_discogs = True
        found = self.discogs.runtime(self.item)
        if found:
            self.value = found | {'source': 'Discogs'}
        return self.value


def needs_refining(best, collected, accepted):
    """Without a known runtime, is a Discogs runtime worth fetching? Yes if uploads matched the
    names but none passed, the best is only "needs review", or the passing uploads disagree on
    length (which one is the real thing?)."""
    if not best or best[2]['status'] == 'uncertain':
        return bool(collected)
    d = best[1]['duration']
    return any(abs(v['duration'] - d) > 0.10 * d for v in accepted)


def _base(item):
    from .match import _base as base
    return base(item)


def _video(v):
    return {'id': v['id'], 'url': video_url(v['id']), 'title': v['title'], 'channel': v['channel'],
            'duration': v.get('duration'), 'views': v.get('views')}


def _pick(item, videos, ref):
    """(best (key, video, verdict) or None, rejected, accepted) for videos that passed the name check."""
    best, rejected, accepted = None, [], []
    for v in videos:
        verdict = judge(item, v, ref)
        if not verdict['status']:
            rejected.append(_video(v) | {'reason': verdict['reason']})
            continue
        accepted.append(v)
        key = (RANK[verdict['status']], verdict['score'])
        if not best or key > best[0]:
            best = (key, v, verdict)
    return best, rejected, accepted


def match_item(item, search, runtime, override=None):
    base = _base(item)
    if override and override.get('skip'):
        return base | {'status': 'skipped', 'evidence': ['skipped in overrides.toml'], 'warnings': [], 'rejected': []}
    if override and override.get('youtube'):
        vid = video_id_from(override['youtube'])
        return base | {'status': 'manual', 'youtube': {'id': vid, 'url': video_url(vid), 'title': override['youtube'],
                                                       'channel': '', 'duration': None, 'views': None},
                       'video_ids': [vid], 'evidence': ['pinned in overrides.toml'], 'warnings': [], 'rejected': []}

    seen, collected, best, rejected, accepted = set(), [], None, [], []
    qs = queries(item)
    for q in qs:
        if best and RANK[best[2]['status']] >= RANK['likely']:
            break
        if q == qs[-1] and collected:
            break  # the unlabelled fallback is only for when nothing matched the names at all
        for v in search.search(q):
            if v['id'] not in seen:
                seen.add(v['id'])
                a, t, _ = names(item, v)
                if a >= NAME_THRESHOLD and t >= NAME_THRESHOLD:
                    collected.append(v)
        if collected:
            best, rejected, accepted = _pick(item, collected, runtime.get())

    if collected and runtime.value is None and needs_refining(best, collected, accepted):
        if runtime.from_discogs():
            best, rejected, accepted = _pick(item, collected, runtime.value)

    ref = runtime.value
    if not best:
        reason = (f'{len(rejected)} upload(s) matched the names but were rejected' if rejected
                  else 'no YouTube upload matched the artist and title')
        return base | {'status': 'not_found', 'reason': reason, 'evidence': [], 'warnings': [],
                       'rejected': rejected[:5], 'runtime': ref}
    _, v, verdict = best
    return base | {
        'status': verdict['status'],
        'youtube': _video(v),
        'video_ids': [v['id']],
        'evidence': verdict['evidence'],
        'warnings': verdict['warnings'],
        'rejected': rejected[:5],
        'runtime': ref,
    }
