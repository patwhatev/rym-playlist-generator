import argparse
import gzip
import json
import shutil
import sys
import time
import tomllib
import traceback
from collections import Counter
from datetime import datetime, timezone
from functools import cached_property
from urllib.parse import unquote

from . import paths, store
from .parse import parse_capture


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ---------------------------------------------------------------- ingest

def ingest(reparse=False):
    """Pull captures from ~/Downloads/rymlist into pending/. A recapture of a done list
    moves it back to pending, keeping its playlist so the same playlist gets updated."""
    paths.CAPTURES.mkdir(exist_ok=True)
    if reparse:
        sources = [(p, json.loads(gzip.decompress(p.read_bytes()))) for p in sorted(paths.CAPTURES.glob('*.json.gz'))]
    else:
        inbox = sorted(paths.INBOX.glob('*.json')) if paths.INBOX.exists() else []
        sources = [(p, store.read_json(p)) for p in inbox]

    ingested = []
    for path, capture in sources:
        if capture.get('source') != 'rymlist-extension':
            print(f'skipping {path.name}: not a rymlist capture')
            continue
        lst = parse_capture(capture)
        list_id = lst['id']

        state, folder = store.find(list_id)
        if folder:
            lst['status'] = store.read_json(folder / 'list.json').get('status', {})
            if state == 'done' and not reparse:
                folder = store.move(list_id, 'pending')
        else:
            folder = paths.PENDING / list_id
            lst['status'] = {}

        if not reparse:
            lst['status'].update({'state': 'pending', 'captured_at': lst['captured_at'], 'error': None})
            lst['status']['folder'] = store.rel(folder)
        store.write_json(folder / 'list.json', lst)

        if not reparse:
            (paths.CAPTURES / f'{list_id}.json.gz').write_bytes(gzip.compress(path.read_bytes()))
            done_dir = paths.INBOX / 'ingested'
            done_dir.mkdir(exist_ok=True)
            shutil.move(path, done_dir / path.name)  # keep the original, never delete
        picked = f' from pages {", ".join(map(str, lst["page_numbers"]))}' if lst['capture_mode'] == 'manual' else ''
        print(f'{"reparsed" if reparse else "ingested"} {list_id}: "{lst["title"]}", {len(lst["items"])} releases{picked}')
        ingested.append(list_id)
    return ingested


# ---------------------------------------------------------------- match

def _load_overrides():
    if not paths.OVERRIDES.exists():
        return {}
    data = tomllib.loads(paths.OVERRIDES.read_text())
    return {unquote(k).rstrip('/') + '/': v for k, v in data.items()}


class Clients:
    """API clients for one command, created only when first needed."""

    def __init__(self, refresh=False, no_mb=False, no_discogs=False):
        self.refresh, self.no_mb, self.no_discogs = refresh, no_mb, no_discogs

    @cached_property
    def spotify(self):
        from .spotify import Spotify
        return Spotify(refresh=self.refresh)

    @cached_property
    def mb(self):
        from .musicbrainz import MusicBrainz
        return None if self.no_mb else MusicBrainz(refresh=self.refresh)

    @cached_property
    def discogs(self):
        from .discogs import Discogs
        return None if self.no_discogs else Discogs.maybe(refresh=self.refresh)

    @cached_property
    def yt_search(self):
        from .youtube import YouTubeSearch
        return YouTubeSearch(refresh=self.refresh)


def _results(lst, path):
    # results matched before pages were tracked: take the page from the list by position
    doc = store.read_json(path)
    page_of = {i['position']: i.get('page') for i in lst['items']}
    for r in doc['results']:
        if r.get('page') is None:
            r['page'] = page_of.get(r['position'])
    return doc


def _by_service(lst, folder):
    """{service: results} for every service this list has been matched on, sections applied."""
    from . import sections
    pages = sections.load(lst['id'])
    out = {}
    for service in store.SERVICES:
        path = store.results_file(folder, service)
        if path.exists():
            results = _results(lst, path)['results']
            for r in results:
                r['section'] = pages.get(r.get('page'), sections.OTHER) if pages else None
            out[service] = results
    return out


def write_reports(list_id):
    from .report import leftovers_json, render, render_leftovers
    lst, folder = store.load(list_id)
    by_service = _by_service(lst, folder)
    if not by_service:
        return
    (folder / 'report.md').write_text(render(lst, by_service, lst['status'].get('playlists') or []))
    (folder / 'leftovers.md').write_text(render_leftovers(lst, by_service))
    store.write_json(folder / 'leftovers.json', leftovers_json(by_service))
    lst['status']['leftovers'] = {'file': store.rel(folder / 'leftovers.md')}
    store.save(list_id, lst)


MAX_ERRORS_IN_A_ROW = 5


def _matcher(service, clients, lst, folder):
    """item, override -> result, for the chosen service."""
    if service == 'spotify':
        from .match import match_item
        return lambda item, override: match_item(item, clients.spotify, clients.mb, override, discogs=clients.discogs)

    from .ytmatch import Runtime, match_item
    # a trusted spotify match gives the real runtime for free
    spotify_path = store.results_file(folder, 'spotify')
    spotify_prev = {r['rym_url']: r for r in store.read_json(spotify_path)['results']} if spotify_path.exists() else {}

    def run(item, override):
        prev = spotify_prev.get(item['rym_url'])
        runtime = Runtime(item, prev, clients.spotify if prev else None, clients.mb, clients.discogs)
        return match_item(item, clients.yt_search, runtime, override)
    return run


def _target(result):
    if result.get('spotify'):
        return result['spotify']['url']
    if result.get('youtube'):
        from .youtube import fmt_duration
        return f'{result["youtube"]["url"]} ({fmt_duration(result["youtube"]["duration"])})'
    return result.get('reason', '')


def match(list_id, service, clients, overrides, limit=None):
    from .match import error_result
    from .report import coverage, coverage_line

    lst, folder = store.load(list_id)
    items = lst['items'][:limit] if limit else lst['items']
    out_path = store.results_file(folder, service)
    doc = {'list': {k: lst[k] for k in ('id', 'title', 'url', 'user', 'captured_at')}, 'service': service, 'results': []}
    match_one = _matcher(service, clients, lst, folder)

    print(f'\nMatching {len(items)} releases from "{lst["title"]}" on {service}'
          + ('' if clients.mb is None else ' (MusicBrainz is rate limited to ~1/s; reruns are cached)'), flush=True)
    started = time.monotonic()
    errors_in_a_row = 0
    for n, item in enumerate(items, 1):
        try:
            result = match_one(item, overrides.get(item['rym_url']))
            errors_in_a_row = 0
        except Exception as err:
            # one odd release shouldn't sink the list; it's marked and retried on the next match.
            # a run of failures means a service or the network is down, so stop instead.
            errors_in_a_row += 1
            if errors_in_a_row >= MAX_ERRORS_IN_A_ROW:
                store.write_json(out_path, doc)
                raise RuntimeError(f'{errors_in_a_row} releases in a row failed, stopping; last error: {err}') from err
            result = error_result(item, err)
        doc['results'].append(result)
        print(f'[{n:>4}/{len(items)}] {result["status"].upper():<9} '
              f'{result["credited_as"] or result["artists"][0]} - {item["title"]}  {_target(result)}', flush=True)
        if n % 10 == 0:
            store.write_json(out_path, doc)  # checkpoint so a long run can be inspected mid-way
    store.write_json(out_path, doc)

    counts = Counter(r['status'] for r in doc['results'])
    cov = coverage(doc['results'])
    lst['status'].setdefault('services', {})[service] = {
        'matched_at': _now(),
        'musicbrainz': clients.mb is not None,
        'discogs': clients.discogs is not None,
        'counts': dict(counts),
        'included': cov['included'],
        'not_included': cov['not_included'],
        'leftovers': {'count': counts['not_found'] + counts['uncertain'] + counts['error'],
                      'not_found': counts['not_found'], 'needs_review': counts['uncertain'], 'errors': counts['error']},
    }
    store.save(list_id, lst)
    write_reports(list_id)
    used = []
    if service == 'youtube':
        used.append(f'{clients.yt_search.searches} new YouTube searches')
    if clients.discogs:
        used.append(f'{clients.discogs.requests} Discogs requests')
    print(f'Matched in {time.monotonic() - started:.0f}s: ' + ', '.join(f'{k} {v}' for k, v in counts.most_common())
          + (f' ({", ".join(used)})' if used else ''))
    print(coverage_line(service, cov))
    return doc


def playlist(list_id, service='spotify', public=False, unlisted=False, include_uncertain=False):
    """Create/refresh this list's playlists on one service (one per section if sections.toml covers it)."""
    from . import sections
    from .playlist import build, build_youtube
    lst, folder = store.load(list_id)
    path = store.results_file(folder, service)
    if not path.exists():
        raise SystemExit(f'Run `rymlist match {list_id} --to {service}` first.')
    groups = sections.group(_results(lst, path)['results'], sections.load(list_id))
    current = lst['status'].get('playlists') or []
    previous = [p for p in current if p['service'] == service]
    others = [p for p in current if p['service'] != service]

    if service == 'spotify':
        made = build(lst['title'], groups, previous, public=public, include_uncertain=include_uncertain)
    else:
        def save(entries):  # youtube checkpoints, so a quota stop keeps what was done
            lst['status']['playlists'] = others + entries
            store.save(list_id, lst)
        privacy = 'public' if public else 'unlisted' if unlisted else 'private'
        made = build_youtube(lst['title'], groups, previous, privacy=privacy, include_uncertain=include_uncertain, save=save)
    lst['status']['playlists'] = others + made
    store.save(list_id, lst)
    write_reports(list_id)
    return made


def _playlist_lines(playlists):
    unit = {'spotify': 'tracks', 'youtube': 'videos'}
    return [f'{p["name"]} ({p["service"]}): ' + (f'{p["url"]} ({p["tracks"]} {unit[p["service"]]} from {p["releases"]} releases)'
                                                 if p['url'] else 'nothing found, no playlist')
            for p in playlists]


# ---------------------------------------------------------------- index

def _coverage_cell(st, service):
    svc = (st.get('services') or {}).get(service)
    if not svc:
        return '—'
    total = svc['included'] + svc['not_included'] if 'included' in svc else sum(svc['counts'].values())
    included = svc.get('included', sum(svc['counts'].get(k, 0) for k in ('manual', 'verified', 'likely')))
    return f'{included}/{total} ({included * 100 // max(total, 1)}%)'


def write_index():
    rows = []
    for state, list_id in store.all_lists():
        lst, folder = store.load(list_id)
        st = lst['status']
        made = [p for p in st.get('playlists') or [] if p.get('url')]
        links = ' · '.join(f'[{p["service"]}{" — " + p["section"] if p.get("section") else ""}]({p["url"]})' for p in made[:4])
        if len(made) > 4:
            links += f' · +{len(made) - 4} more (see report)'
        links = links or ('none' if 'playlists' in st else '—')
        rows.append(
            f'| {state} | [{lst["title"]}]({lst["url"]}) | {len(lst["items"])} | '
            f'{_coverage_cell(st, "spotify")} | {_coverage_cell(st, "youtube")} | {links} | '
            + (f'[leftovers]({st["leftovers"]["file"]}) · [report]({store.rel(folder / "report.md")}) |' if (st.get('leftovers') or {}).get('file') else '— |')
        )
    lines = [
        '# rymlist index',
        '',
        f'Generated {_now()}. `pending/` lists still need a playlist; `done/` lists are finished and skipped by `rymlist run`.',
        'Spotify / YouTube columns: releases included in that service\'s playlists, out of the whole list.',
        '',
        '| state | list | releases | Spotify | YouTube | playlists | files |',
        '| --- | --- | --- | --- | --- | --- | --- |',
        *rows,
    ]
    paths.INDEX.write_text('\n'.join(lines) + '\n')


# ---------------------------------------------------------------- commands

def cmd_run(args):
    """Ingest new captures, then match + playlist every pending list and move it to done/."""
    from .report import coverage_line, coverage
    from .youtube import QuotaExceeded

    ingest()
    pending = store.resolve('all', state='pending')
    if not pending:
        print('Nothing pending. Capture a list with the extension, then run this again.')
        write_index()
        return

    clients = Clients(refresh=args.refresh, no_mb=args.no_mb, no_discogs=args.no_discogs)
    overrides = _load_overrides()
    summary = []
    quota_hit = False
    for list_id in pending:
        lst, _ = store.load(list_id)
        service = args.to or lst['status'].get('target') or 'spotify'
        try:
            lst['status']['target'] = service  # a plain `run` resumes with the same service
            store.save(list_id, lst)
            doc = match(list_id, service, clients, overrides)
            if service == 'youtube' and quota_hit:
                raise QuotaExceeded('YouTube API daily quota used up earlier in this run')
            pls = playlist(list_id, service, public=args.public, unlisted=args.unlisted, include_uncertain=args.include_uncertain)
            lst, _ = store.load(list_id)
            lst['status']['error'] = None
            lst['status']['completed_at'] = _now()
            store.save(list_id, lst)
            folder = store.move(list_id, 'done')
            write_reports(list_id)
            cov = coverage(doc['results'])
            summary.append(f'DONE    {list_id}  (leftovers: {store.rel(folder / "leftovers.md")})')
            summary.append(f'          {coverage_line(service, cov)}')
            if cov['error']:
                summary.append(f'          {cov["error"]} release(s) failed to look up and were left out; '
                               f'`rymlist reset {list_id}` then `run` retries them')
            summary += ['          ' + line for line in _playlist_lines(pls)]
        except KeyboardInterrupt:
            raise
        except QuotaExceeded as err:
            quota_hit = True
            lst, _ = store.load(list_id)
            lst['status']['error'] = None
            lst['status']['paused'] = f'{err}; `rymlist run` continues the playlist'
            store.save(list_id, lst)
            write_reports(list_id)
            summary.append(f'PAUSED  {list_id}: {err}. Matching is saved; the next `rymlist run` keeps filling the playlist.')
        except BaseException as err:  # SystemExit included: one bad list shouldn't stop the batch
            lst, _ = store.load(list_id)
            lst['status']['error'] = f'{type(err).__name__}: {err}'
            lst['status']['failed_at'] = _now()
            store.save(list_id, lst)
            traceback.print_exc()
            summary.append(f'FAILED  {list_id}: {err} (left in pending/, will retry next run)')
        else:
            lst, _ = store.load(list_id)
            if lst['status'].pop('paused', None):
                store.save(list_id, lst)

    write_index()
    print('\n' + '\n'.join(summary))
    print(f'Index: {store.rel(paths.INDEX)}')


def cmd_ingest(args):
    ids = ingest(reparse=args.reparse)
    if not ids:
        print(f'Nothing to ingest in {paths.INBOX}. Capture a list with the browser extension first.')
    write_index()


def cmd_match(args):
    clients = Clients(refresh=args.refresh, no_mb=args.no_mb, no_discogs=args.no_discogs)
    overrides = _load_overrides()
    for list_id in store.resolve(args.list):
        match(list_id, args.to or 'spotify', clients, overrides, limit=args.limit)
    write_index()


def cmd_playlist(args):
    for list_id in store.resolve(args.list):
        pls = playlist(list_id, args.to or 'spotify', public=args.public, unlisted=args.unlisted,
                       include_uncertain=args.include_uncertain)
        print('\n'.join(_playlist_lines(pls)))
    write_index()


def cmd_reset(args):
    for list_id in store.resolve(args.list, state='done'):
        store.move(list_id, 'pending')
        print(f'{list_id} moved back to pending/, `rymlist run` will redo it (updating the same playlist)')
    write_index()


def cmd_lists(args):
    lists = store.all_lists()
    if not lists:
        print('No lists yet. Capture one with the extension, then run `rymlist run`.')
        return
    for state, list_id in lists:
        lst, _ = store.load(list_id)
        st = lst['status']
        line = f'{state:<7} {list_id}  "{lst["title"]}"  {len(lst["items"])} releases'
        for service in store.SERVICES:
            if service in (st.get('services') or {}):
                line += f'  | {service} {_coverage_cell(st, service)}'
        made = [p for p in st.get('playlists') or [] if p.get('url')]
        if made:
            line += f'  | {made[0]["url"]}' if len(made) == 1 else f'  | {len(made)} playlists'
        if st.get('paused'):
            line += f'  | paused: {st["paused"]}'
        if st.get('error'):
            line += f'  | last error: {st["error"]}'
        print(line)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='rymlist', description='RYM lists -> verified Spotify / YouTube playlists')
    sub = parser.add_subparsers(dest='command', required=True)

    def service_flag(p, run=False):
        p.add_argument('--to', choices=store.SERVICES,
                       help='where to make playlists (default: spotify' + (', or whatever the list last used' if run else '') + ')')

    def playlist_flags(p):
        p.add_argument('--public', action='store_true', help='public playlists (YouTube defaults to private)')
        p.add_argument('--unlisted', action='store_true', help='YouTube only: unlisted instead of private')
        p.add_argument('--include-uncertain', action='store_true', help='also add "needs review" matches')

    def match_flags(p):
        p.add_argument('--no-mb', action='store_true', help='skip MusicBrainz (fast, but less can be "verified")')
        p.add_argument('--no-discogs', action='store_true', help='never ask Discogs')
        p.add_argument('--refresh', action='store_true', help='ignore cached responses')

    p = sub.add_parser('run', help='ingest new captures, then match + playlist every pending list and move it to done/')
    service_flag(p, run=True)
    match_flags(p)
    playlist_flags(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser('lists', help='show every list, its state, coverage and playlists')
    p.set_defaults(func=cmd_lists)

    p = sub.add_parser('ingest', help='only import captures from ~/Downloads/rymlist')
    p.add_argument('--reparse', action='store_true', help='re-parse every stored capture (after parser fixes)')
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser('match', help='only match a list (no playlist, not moved)')
    p.add_argument('list', help='list id, part of one, or "all"')
    p.add_argument('--limit', type=int, help='only the first N releases (for testing)')
    service_flag(p)
    match_flags(p)
    p.set_defaults(func=cmd_match)

    p = sub.add_parser('playlist', help='only create/refresh the playlists for a matched list')
    p.add_argument('list', help='list id, part of one, or "all"')
    service_flag(p)
    playlist_flags(p)
    p.set_defaults(func=cmd_playlist)

    p = sub.add_parser('reset', help='move a done list back to pending so `run` redoes it')
    p.add_argument('list', help='list id, part of one, or "all"')
    p.set_defaults(func=cmd_reset)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit('\ninterrupted (progress up to the last checkpoint is saved)')
