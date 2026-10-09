"""Markdown report of what was found, what needs review and what couldn't be found."""
from collections import Counter
from urllib.parse import quote_plus

STATUS_ORDER = ['manual', 'verified', 'likely', 'uncertain', 'not_found', 'skipped']
HEADINGS = {
    'manual': 'Pinned manually',
    'verified': 'Verified',
    'likely': 'Likely',
    'uncertain': 'Needs review',
    'not_found': 'Not found',
    'skipped': 'Skipped',
}


def _cell(text):
    return str(text or '').replace('|', '\\|').replace('\n', ' ')


def _rym(r):
    artist = r['credited_as'] or ', '.join(r['artists'])
    title = r['title'] + (f' [{r["title_latin"]}]' if r.get('title_latin') else '')
    return f'{_cell(artist)} — [{_cell(title)}]({r["rym_url"]}) ({r["year"] or "?"}, {r["type_label"]})'


def _search_links(r):
    q = quote_plus(f'{r["credited_as"] or r["artists"][0]} {r["title"]}')
    return (f'[spotify](https://open.spotify.com/search/{q}) · '
            f'[bandcamp](https://bandcamp.com/search?q={q}) · '
            f'[youtube](https://www.youtube.com/results?search_query={q}) · '
            f'[discogs](https://www.discogs.com/search/?q={q})')


def leftovers(results):
    return [r for r in results if r['status'] in ('not_found', 'uncertain')]


def leftovers_json(results):
    out = []
    for r in leftovers(results):
        entry = {k: r[k] for k in ('position', 'status', 'rym_url', 'title', 'title_latin', 'year', 'type_label', 'credited_as', 'artists')}
        entry['reason'] = r.get('reason') or '; '.join(r.get('warnings', []))
        if r.get('spotify'):
            entry['best_spotify_candidate'] = r['spotify']['url']
        out.append(entry)
    return out


def render_leftovers(lst, results):
    rows = leftovers(results)
    lines = [
        f'# Leftovers: {lst["title"]}',
        '',
        f'{len(rows)} of {len(results)} releases from [the RYM list]({lst["url"]}) need a manual search. '
        'Pin a find in `overrides.toml` (or just enjoy it elsewhere).',
    ]
    for status, heading in (('uncertain', 'Needs review (a Spotify candidate exists but was not trusted)'),
                            ('not_found', 'Not on Spotify')):
        group = [r for r in rows if r['status'] == status]
        if not group:
            continue
        lines += ['', f'## {heading} ({len(group)})', '', '| # | release | why | search |', '| --- | --- | --- | --- |']
        for r in group:
            why = r.get('reason') or '; '.join(r['warnings'])
            if r.get('spotify'):
                why += f' — candidate: [{_cell(r["spotify"]["name"])}]({r["spotify"]["url"]})'
            lines.append(f'| {r["position"]} | {_rym(r)} | {_cell(why)} | {_search_links(r)} |')
    return '\n'.join(lines) + '\n'


def render(lst, results, playlist=None):
    counts = Counter(r['status'] for r in results)
    total = len(results)
    found = sum(counts[s] for s in ('manual', 'verified', 'likely'))
    lines = [
        f'# {lst["title"]}',
        '',
        f'[RYM list]({lst["url"]}) by {lst["user"]}, captured {lst["captured_at"][:10]}, {total} releases.',
        '',
        f'**{found} matched** ({found * 100 // max(total, 1)}%), '
        f'**{counts["uncertain"]} need review**, **{counts["not_found"]} not found**'
        + (f', {counts["skipped"]} skipped' if counts['skipped'] else '') + '.',
        '',
        '| status | count | meaning |',
        '| --- | --- | --- |',
        f'| verified | {counts["verified"]} | confirmed by MusicBrainz (linked album, barcode, or linked artist + year/track count) |',
        f'| likely | {counts["likely"]} | artist, title and year all match; no other Spotify artist with that name |',
        f'| needs review | {counts["uncertain"]} | names match but date, ambiguity or spelling is off |',
        f'| not found | {counts["not_found"]} | nothing on Spotify passed validation |',
    ]
    if counts['manual']:
        lines.append(f'| pinned | {counts["manual"]} | set by hand in overrides.toml |')
    if playlist:
        lines += ['', f'Playlist: [{playlist["name"]}]({playlist["url"]}) ({playlist["tracks"]} tracks from {playlist["releases"]} releases).']
    lines += ['', 'Everything that needs a manual search is also in [leftovers.md](leftovers.md).']
    lines += ['', 'Fix a match by pinning or skipping it in `overrides.toml`, then rerun `rymlist match`.']

    by_status = {s: [r for r in results if r['status'] == s] for s in STATUS_ORDER}

    for status in ('manual', 'verified', 'likely'):
        rows = by_status[status]
        if not rows:
            continue
        lines += ['', f'## {HEADINGS[status]} ({len(rows)})', '',
                  '| # | RYM | Spotify | evidence |', '| --- | --- | --- | --- |']
        for r in rows:
            sp = r['spotify']
            lines.append(f'| {r["position"]} | {_rym(r)} | [{_cell(sp["name"])}]({sp["url"]}) — '
                         f'{_cell(", ".join(sp["artists"]))} ({sp["release_date"][:4]}) | {_cell("; ".join(r["evidence"]))} |')

    rows = by_status['uncertain']
    if rows:
        lines += ['', f'## {HEADINGS["uncertain"]} ({len(rows)})', '',
                  'Not added to playlists unless you pass `--include-uncertain` or pin them.', '',
                  '| # | RYM | best Spotify candidate | why it needs review |', '| --- | --- | --- | --- |']
        for r in rows:
            sp = r['spotify']
            lines.append(f'| {r["position"]} | {_rym(r)} | [{_cell(sp["name"])}]({sp["url"]}) — '
                         f'{_cell(", ".join(sp["artists"]))} ({sp["release_date"]}, {sp["total_tracks"]} tracks) | '
                         f'{_cell("; ".join(r["warnings"]))} |')

    rows = by_status['not_found']
    if rows:
        lines += ['', f'## {HEADINGS["not_found"]} ({len(rows)})', '',
                  '| # | RYM | why | look elsewhere |', '| --- | --- | --- | --- |']
        for r in rows:
            why = r['reason']
            if r['rejected']:
                why += ': ' + '; '.join(f'"{c["name"]}" by {", ".join(c["artists"])} ({c["reason"]})' for c in r['rejected'][:3])
            lines.append(f'| {r["position"]} | {_rym(r)} | {_cell(why)} | {_search_links(r)} |')

    rows = by_status['skipped']
    if rows:
        lines += ['', f'## {HEADINGS["skipped"]} ({len(rows)})', '']
        lines += [f'- {r["position"]}. {_rym(r)}' for r in rows]

    return '\n'.join(lines) + '\n'
