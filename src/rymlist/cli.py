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
        print(f'{"reparsed" if reparse else "ingested"} {list_id}: "{lst["title"]}", {len(lst["items"])} releases')
        ingested.append(list_id)
    return ingested


# ---------------------------------------------------------------- match

def _load_overrides():
    if not paths.OVERRIDES.exists():
        return {}
    data = tomllib.loads(paths.OVERRIDES.read_text())
    return {unquote(k).rstrip('/') + '/': v for k, v in data.items()}


def write_reports(list_id):
    from .report import leftovers_json, render, render_leftovers
    lst, folder = store.load(list_id)
    doc = store.read_json(folder / 'results.json')
    playlist = lst['status'].get('playlist')
    (folder / 'report.md').write_text(render(lst, doc['results'], playlist))
    (folder / 'leftovers.md').write_text(render_leftovers(lst, doc['results']))
    store.write_json(folder / 'leftovers.json', leftovers_json(doc['results']))


def match(list_id, spotify, mb, overrides, limit=None):
    from .match import match_item

    lst, folder = store.load(list_id)
    items = lst['items'][:limit] if limit else lst['items']
    out_path = folder / 'results.json'
    doc = {'list': {k: lst[k] for k in ('id', 'title', 'url', 'user', 'captured_at')}, 'results': []}

    print(f'\nMatching {len(items)} releases from "{lst["title"]}"'
          + ('' if mb is None else ' (MusicBrainz is rate limited to ~1/s; reruns are cached)'), flush=True)
    started = time.monotonic()
    for n, item in enumerate(items, 1):
        result = match_item(item, spotify, mb, overrides.get(item['rym_url']))
        doc['results'].append(result)
        target = result['spotify']['url'] if result.get('spotify') else result.get('reason', '')
        print(f'[{n:>4}/{len(items)}] {result["status"].upper():<9} '
              f'{result["credited_as"] or result["artists"][0]} - {item["title"]}  {target}', flush=True)
        if n % 10 == 0:
            store.write_json(out_path, doc)  # checkpoint so a long run can be inspected mid-way
    store.write_json(out_path, doc)

    counts = Counter(r['status'] for r in doc['results'])
    lst['status']['matched_at'] = _now()
    lst['status']['musicbrainz'] = mb is not None
    lst['status']['counts'] = dict(counts)
    leftovers = counts['not_found'] + counts['uncertain']
    lst['status']['leftovers'] = {
        'count': leftovers,
        'not_found': counts['not_found'],
        'needs_review': counts['uncertain'],
        'file': store.rel(folder / 'leftovers.md'),
    }
    store.save(list_id, lst)
    write_reports(list_id)
    print(f'Matched in {time.monotonic() - started:.0f}s: ' + ', '.join(f'{k} {v}' for k, v in counts.most_common()))
    return doc


def playlist(list_id, name=None, public=False, include_uncertain=False):
    from .playlist import build
    lst, folder = store.load(list_id)
    if not (folder / 'results.json').exists():
        raise SystemExit(f'Run `rymlist match {list_id}` first.')
    doc = store.read_json(folder / 'results.json')
    previous = lst['status'].get('playlist') or {}
    parts = build(doc, previous.get('parts', []), name=name, public=public, include_uncertain=include_uncertain)
    if parts:
        lst['status']['playlist'] = {
            'created_at': previous.get('created_at') or _now(),
            'updated_at': _now(),
            'url': parts[0]['url'],
            'name': parts[0]['name'],
            'tracks': sum(p['tracks'] for p in parts),
            'releases': parts[0]['releases'],
            'parts': parts,
        }
    else:
        lst['status']['playlist'] = None
        lst['status']['note'] = 'nothing on the list was found on Spotify, so no playlist was made'
    store.save(list_id, lst)
    write_reports(list_id)
    return lst['status']['playlist']


# ---------------------------------------------------------------- index

def write_index():
    rows = []
    for state, list_id in store.all_lists():
        lst, folder = store.load(list_id)
        st = lst['status']
        counts = st.get('counts', {})
        matched = sum(counts.get(k, 0) for k in ('manual', 'verified', 'likely'))
        pl = st.get('playlist')
        rows.append(
            f'| {state} | [{lst["title"]}]({lst["url"]}) | {len(lst["items"])} | '
            + (f'{matched} | {counts.get("uncertain", 0)} | {counts.get("not_found", 0)} | ' if counts else '— | — | — | ')
            + (f'[playlist]({pl["url"]}) ({pl["tracks"]} tracks) | ' if pl else ('none | ' if 'playlist' in st else '— | '))
            + (f'[leftovers]({st["leftovers"]["file"]}) · [report]({store.rel(folder / "report.md")}) |' if st.get('leftovers') else '— |')
        )
    lines = [
        '# rymlist index',
        '',
        f'Generated {_now()}. `pending/` lists still need a playlist; `done/` lists are finished and skipped by `rymlist run`.',
        '',
        '| state | list | releases | matched | needs review | not found | playlist | files |',
        '| --- | --- | --- | --- | --- | --- | --- | --- |',
        *rows,
    ]
    paths.INDEX.write_text('\n'.join(lines) + '\n')


# ---------------------------------------------------------------- commands

def cmd_run(args):
    """Ingest new captures, then match + playlist every pending list and move it to done/."""
    from .musicbrainz import MusicBrainz
    from .spotify import Spotify

    ingest()
    pending = store.resolve('all', state='pending')
    if not pending:
        print('Nothing pending. Capture a list with the extension, then run this again.')
        write_index()
        return

    spotify = Spotify(refresh=args.refresh)
    mb = None if args.no_mb else MusicBrainz(refresh=args.refresh)
    overrides = _load_overrides()
    summary = []
    for list_id in pending:
        try:
            match(list_id, spotify, mb, overrides)
            pl = playlist(list_id, public=args.public, include_uncertain=args.include_uncertain)
            lst, _ = store.load(list_id)
            lst['status']['error'] = None
            lst['status']['completed_at'] = _now()
            store.save(list_id, lst)
            folder = store.move(list_id, 'done')
            write_reports(list_id)
            summary.append(f'DONE    {list_id}: ' + (f'{pl["url"]} ({pl["tracks"]} tracks)' if pl else 'no playlist, nothing found on Spotify')
                           + f'; leftovers: {store.rel(folder / "leftovers.md")}')
        except KeyboardInterrupt:
            raise
        except BaseException as err:  # SystemExit included: one bad list shouldn't stop the batch
            lst, _ = store.load(list_id)
            lst['status']['error'] = f'{type(err).__name__}: {err}'
            lst['status']['failed_at'] = _now()
            store.save(list_id, lst)
            traceback.print_exc()
            summary.append(f'FAILED  {list_id}: {err} (left in pending/, will retry next run)')

    write_index()
    print('\n' + '\n'.join(summary))
    print(f'Index: {store.rel(paths.INDEX)}')


def cmd_ingest(args):
    ids = ingest(reparse=args.reparse)
    if not ids:
        print(f'Nothing to ingest in {paths.INBOX}. Capture a list with the browser extension first.')
    write_index()


def cmd_match(args):
    from .musicbrainz import MusicBrainz
    from .spotify import Spotify
    spotify = Spotify(refresh=args.refresh)
    mb = None if args.no_mb else MusicBrainz(refresh=args.refresh)
    overrides = _load_overrides()
    for list_id in store.resolve(args.list):
        match(list_id, spotify, mb, overrides, limit=args.limit)
    write_index()


def cmd_playlist(args):
    for list_id in store.resolve(args.list):
        pl = playlist(list_id, name=args.name, public=args.public, include_uncertain=args.include_uncertain)
        print(f'{list_id}: ' + (f'{pl["url"]} ({pl["tracks"]} tracks from {pl["releases"]} releases)' if pl else 'nothing to add'))
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
        if st.get('counts'):
            line += '  | ' + ', '.join(f'{k} {v}' for k, v in sorted(st['counts'].items()))
        if st.get('playlist'):
            line += f'  | {st["playlist"]["url"]}'
        if st.get('error'):
            line += f'  | last error: {st["error"]}'
        print(line)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='rymlist', description='RYM lists -> verified Spotify playlists')
    sub = parser.add_subparsers(dest='command', required=True)

    def playlist_flags(p):
        p.add_argument('--public', action='store_true')
        p.add_argument('--include-uncertain', action='store_true', help='also add "needs review" matches')

    def match_flags(p):
        p.add_argument('--no-mb', action='store_true', help='skip MusicBrainz (fast, but nothing can be "verified")')
        p.add_argument('--refresh', action='store_true', help='ignore cached Spotify/MusicBrainz responses')

    p = sub.add_parser('run', help='ingest new captures, then match + playlist every pending list and move it to done/')
    match_flags(p)
    playlist_flags(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser('lists', help='show every list, its state and playlist')
    p.set_defaults(func=cmd_lists)

    p = sub.add_parser('ingest', help='only import captures from ~/Downloads/rymlist')
    p.add_argument('--reparse', action='store_true', help='re-parse every stored capture (after parser fixes)')
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser('match', help='only match a list (no playlist, not moved)')
    p.add_argument('list', help='list id, part of one, or "all"')
    p.add_argument('--limit', type=int, help='only the first N releases (for testing)')
    match_flags(p)
    p.set_defaults(func=cmd_match)

    p = sub.add_parser('playlist', help='only create/refresh the playlist for a matched list')
    p.add_argument('list', help='list id, part of one, or "all"')
    p.add_argument('--name', help='playlist name (default: "RYM <list title>")')
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
