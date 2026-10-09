"""Markdown report of what was found, what needs review and what couldn't be found, per service,
led by how much of the list each service actually covers."""
from collections import Counter
from urllib.parse import quote_plus

from .youtube import fmt_duration

SERVICE_NAMES = {'spotify': 'Spotify', 'youtube': 'YouTube'}
INCLUDED = ('manual', 'verified', 'likely')
HEADINGS = {
    'manual': 'Pinned manually',
    'verified': 'Verified',
    'likely': 'Likely',
    'uncertain': 'Needs review',
    'not_found': 'Not found',
    'error': 'Lookup failed',
    'skipped': 'Skipped',
}
RETRY = '`rymlist reset <list>` then `rymlist run` retries them'


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


def _candidate(r):
    """Markdown link to the matched/best Spotify album or YouTube video, or ''."""
    if r.get('spotify'):
        sp = r['spotify']
        return f'[{_cell(sp["name"])}]({sp["url"]}) — {_cell(", ".join(sp["artists"]))} ({(sp["release_date"] or "")[:4]})'
    if r.get('youtube'):
        yt = r['youtube']
        return f'[{_cell(yt["title"])}]({yt["url"]}) — {_cell(yt["channel"])}, {fmt_duration(yt["duration"])}'
    return ''


# ---------------------------------------------------------------- coverage

def coverage(results):
    c = Counter(r['status'] for r in results)
    total = len(results)
    included = sum(c[s] for s in INCLUDED)
    return {'total': total, 'included': included, 'not_included': total - included,
            'uncertain': c['uncertain'], 'not_found': c['not_found'], 'error': c['error'], 'skipped': c['skipped']}


def _pct(n, total):
    return f'{n * 100 // max(total, 1)}%'


def coverage_line(service, cov):
    extra = ''.join(f', {cov[k]} {label}' for k, label in (('error', 'failed to look up'), ('skipped', 'skipped')) if cov[k])
    return (f'{SERVICE_NAMES[service]}: {cov["included"]} of {cov["total"]} releases included ({_pct(cov["included"], cov["total"])}), '
            f'{cov["not_included"]} not included ({cov["not_found"]} not found, {cov["uncertain"]} need review{extra})')


def _coverage_section(by_service):
    lines = ['| service | included | not included | not found | needs review | failed |', '| --- | --- | --- | --- | --- | --- |']
    covs = {s: coverage(rs) for s, rs in by_service.items()}
    for s, cov in covs.items():
        lines.append(f'| {SERVICE_NAMES[s]} | **{cov["included"]}** ({_pct(cov["included"], cov["total"])}) | '
                     f'**{cov["not_included"]}** ({_pct(cov["not_included"], cov["total"])}) | {cov["not_found"]} | '
                     f'{cov["uncertain"]} | {cov["error"]} |')
    if len(by_service) == 2:
        included = {s: {r['position'] for r in rs if r['status'] in INCLUDED} for s, rs in by_service.items()}
        (a, ia), (b, ib) = included.items()
        total = max(c['total'] for c in covs.values())
        lines += ['',
                  f'{len(ia - ib)} releases are only on {SERVICE_NAMES[a]}, {len(ib - ia)} only on {SERVICE_NAMES[b]}, '
                  f'{len(ia & ib)} on both; {total - len(ia | ib)} of {total} are on neither.']
    return lines


# ---------------------------------------------------------------- leftovers

def leftovers(results):
    return [r for r in results if r['status'] in ('not_found', 'uncertain', 'error')]


def leftovers_json(by_service):
    out = []
    for service, results in by_service.items():
        for r in leftovers(results):
            entry = {'service': service} | {k: r.get(k) for k in ('position', 'page', 'section', 'status', 'rym_url', 'title',
                                                                  'title_latin', 'year', 'type_label', 'credited_as', 'artists')}
            entry['reason'] = r.get('reason') or '; '.join(r.get('warnings', []))
            if r.get('spotify'):
                entry['best_spotify_candidate'] = r['spotify']['url']
            if r.get('youtube'):
                entry['best_youtube_candidate'] = r['youtube']['url']
            out.append(entry)
    return out


def _leftover_tables(rows, level, service):
    lines = []
    name = SERVICE_NAMES[service]
    for status, heading in (('uncertain', 'Needs review (a candidate exists but was not trusted)'),
                            ('not_found', f'Not on {name}'),
                            ('error', f'Lookup failed ({RETRY})')):
        group = [r for r in rows if r['status'] == status]
        if not group:
            continue
        lines += ['', f'{level} {heading} ({len(group)})', '', '| # | release | why | search |', '| --- | --- | --- | --- |']
        for r in group:
            why = r.get('reason') or '; '.join(r['warnings'])
            if _candidate(r):
                why += f' — candidate: {_candidate(r)}'
            lines.append(f'| {r["position"]} | {_rym(r)} | {_cell(why)} | {_search_links(r)} |')
    return lines


def render_leftovers(lst, by_service):
    lines = [f'# Leftovers: {lst["title"]}', '']
    for service, results in by_service.items():
        lines.append(f'- {coverage_line(service, coverage(results))}')
    lines += ['', f'Everything below needs a manual search ([the RYM list]({lst["url"]})). '
                  'Pin a find in `overrides.toml` (or just enjoy it elsewhere).']
    several = len(by_service) > 1
    for service, results in by_service.items():
        rows = leftovers(results)
        top = '##' if several else '#'
        if several:
            lines += ['', f'## {SERVICE_NAMES[service]} ({len(rows)})']
        sections = list(dict.fromkeys(r.get('section') for r in results))
        if sections == [None]:
            lines += _leftover_tables(rows, top + '#', service)
            continue
        for section in sections:
            group = [r for r in rows if r.get('section') == section]
            if group:
                lines += ['', f'{top}# {section} ({len(group)})']
                lines += _leftover_tables(group, top + '##', service)
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- full report

def _spotify_body(results):
    counts = Counter(r['status'] for r in results)
    lines = ['', '| status | count | meaning |', '| --- | --- | --- |',
             f'| verified | {counts["verified"]} | confirmed by MusicBrainz (linked album, barcode, or linked artist + year/track count) or a Discogs barcode |',
             f'| likely | {counts["likely"]} | artist, title and year all match; no other Spotify artist with that name |',
             f'| needs review | {counts["uncertain"]} | names match but date, ambiguity or spelling is off |',
             f'| not found | {counts["not_found"]} | nothing on Spotify passed validation |']
    if counts['manual']:
        lines.append(f'| pinned | {counts["manual"]} | set by hand in overrides.toml |')

    by_status = {s: [r for r in results if r['status'] == s] for s in HEADINGS}
    for status in INCLUDED:
        rows = by_status[status]
        if rows:
            lines += ['', f'### {HEADINGS[status]} ({len(rows)})', '', '| # | RYM | Spotify | evidence |', '| --- | --- | --- | --- |']
            lines += [f'| {r["position"]} | {_rym(r)} | {_candidate(r)} | {_cell("; ".join(r["evidence"]))} |' for r in rows]

    rows = by_status['uncertain']
    if rows:
        lines += ['', f'### {HEADINGS["uncertain"]} ({len(rows)})', '',
                  'Not added to playlists unless you pass `--include-uncertain` or pin them.', '',
                  '| # | RYM | best Spotify candidate | why it needs review |', '| --- | --- | --- | --- |']
        for r in rows:
            tracks = f', {r["spotify"]["total_tracks"]} tracks' if r.get('spotify') else ''
            lines.append(f'| {r["position"]} | {_rym(r)} | {_candidate(r)}{tracks} | {_cell("; ".join(r["warnings"]))} |')

    rows = by_status['not_found']
    if rows:
        lines += ['', f'### {HEADINGS["not_found"]} ({len(rows)})', '', '| # | RYM | why | look elsewhere |', '| --- | --- | --- | --- |']
        for r in rows:
            why = r['reason']
            if r['rejected']:
                why += ': ' + '; '.join(f'"{c["name"]}" by {", ".join(c["artists"])} ({c["reason"]})' for c in r['rejected'][:3])
            lines.append(f'| {r["position"]} | {_rym(r)} | {_cell(why)} | {_search_links(r)} |')
    return lines + _tail(by_status)


def _youtube_body(results):
    counts = Counter(r['status'] for r in results)
    lines = ['', '| status | count | meaning |', '| --- | --- | --- |',
             f'| verified | {counts["verified"]} | names match and the length is within 5% (or 90s) of the real runtime |',
             f'| likely | {counts["likely"]} | names match and the length is within 10%, or no runtime is known but it is labelled "full" / on the artist\'s channel and long enough |',
             f'| needs review | {counts["uncertain"]} | names match but the length is off, short (punk?) or can\'t be checked |',
             f'| not found | {counts["not_found"]} | no upload passed |']
    if counts['manual']:
        lines.append(f'| pinned | {counts["manual"]} | set by hand in overrides.toml |')

    def runtime(r):
        rt = r.get('runtime')
        return f'{fmt_duration(rt["seconds"])} ({rt["source"]})' if rt else 'unknown'

    by_status = {s: [r for r in results if r['status'] == s] for s in HEADINGS}
    for status in INCLUDED:
        rows = by_status[status]
        if rows:
            lines += ['', f'### {HEADINGS[status]} ({len(rows)})', '',
                      '| # | RYM | YouTube | real runtime | evidence |', '| --- | --- | --- | --- | --- |']
            lines += [f'| {r["position"]} | {_rym(r)} | {_candidate(r)} | {runtime(r)} | {_cell("; ".join(r["evidence"]))} |' for r in rows]

    rows = by_status['uncertain']
    if rows:
        lines += ['', f'### {HEADINGS["uncertain"]} ({len(rows)})', '',
                  'Not added to playlists unless you pass `--include-uncertain` or pin them.', '',
                  '| # | RYM | best upload | real runtime | why it needs review |', '| --- | --- | --- | --- | --- |']
        lines += [f'| {r["position"]} | {_rym(r)} | {_candidate(r)} | {runtime(r)} | {_cell("; ".join(r["warnings"]))} |' for r in rows]

    rows = by_status['not_found']
    if rows:
        lines += ['', f'### {HEADINGS["not_found"]} ({len(rows)})', '', '| # | RYM | why | look elsewhere |', '| --- | --- | --- | --- |']
        for r in rows:
            why = r['reason']
            if r['rejected']:
                why += ': ' + '; '.join(f'[{c["title"]}]({c["url"]}) ({c["reason"]})' for c in r['rejected'][:3])
            lines.append(f'| {r["position"]} | {_rym(r)} | {_cell(why)} | {_search_links(r)} |')
    return lines + _tail(by_status)


def _tail(by_status):
    lines = []
    rows = by_status['error']
    if rows:
        lines += ['', f'### {HEADINGS["error"]} ({len(rows)})', '', f'The lookup itself failed, so these were not judged. {RETRY}.', '',
                  '| # | RYM | error | look elsewhere |', '| --- | --- | --- | --- |']
        lines += [f'| {r["position"]} | {_rym(r)} | {_cell(r["reason"])} | {_search_links(r)} |' for r in rows]
    rows = by_status['skipped']
    if rows:
        lines += ['', f'### {HEADINGS["skipped"]} ({len(rows)})', '']
        lines += [f'- {r["position"]}. {_rym(r)}' for r in rows]
    return lines


BODIES = {'spotify': _spotify_body, 'youtube': _youtube_body}


def render(lst, by_service, playlists=()):
    total = len(next(iter(by_service.values()), []))
    lines = [
        f'# {lst["title"]}',
        '',
        f'[RYM list]({lst["url"]}) by {lst["user"]}, captured {lst["captured_at"][:10]}, {total} releases.',
        '',
        '## Coverage',
        '',
        *_coverage_section(by_service),
    ]
    made = [p for p in playlists if p.get('service') in by_service]
    if made:
        lines += ['', '| service | playlist | tracks / videos | releases |', '| --- | --- | --- | --- |']
        for p in made:
            name = f'[{_cell(p["name"])}]({p["url"]})' if p.get('url') else f'{_cell(p["name"])} (nothing found)'
            if p.get('incomplete'):
                name += ' (incomplete: the next `run` keeps filling it)'
            lines.append(f'| {SERVICE_NAMES[p["service"]]} | {name} | {p["tracks"]} | {p["releases"]} |')
    lines += ['', 'Everything that needs a manual search is also in [leftovers.md](leftovers.md).',
              'Fix a match by pinning or skipping it in `overrides.toml`, then rerun `rymlist match`.']
    for service, results in by_service.items():
        lines += ['', f'## {SERVICE_NAMES[service]}']
        lines += BODIES[service](results)
    return '\n'.join(lines) + '\n'
