"""Per-list folders. A list lives in pending/<id>/ until its playlist is made, then done/<id>/.

    list.json        parsed releases + a "status" block (playlist url, timestamps, leftovers)
    results.json     match result per release
    report.md        everything: matched, needs review, not found
    leftovers.md     just what needs a manual search (not found + needs review)
    leftovers.json   same, machine readable
"""
import json
import shutil

from . import paths

STATES = {'pending': paths.PENDING, 'done': paths.DONE}


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)


def rel(path):
    return str(path.relative_to(paths.ROOT))


def find(list_id):
    """(state, folder) for a list, or (None, None)."""
    for state, root in STATES.items():
        folder = root / list_id
        if (folder / 'list.json').exists():
            return state, folder
    return None, None


def all_lists():
    out = []
    for state, root in STATES.items():
        if root.exists():
            out += [(state, p.name) for p in sorted(root.iterdir()) if (p / 'list.json').exists()]
    return out


def resolve(selector, state=None):
    lists = [(s, i) for s, i in all_lists() if state is None or s == state]
    ids = [i for _, i in lists]
    if selector == 'all':
        return ids
    if selector in ids:
        return [selector]
    hits = [i for i in ids if selector.lower() in i.lower()]
    if len(hits) == 1:
        return hits
    if not hits:
        raise SystemExit(f'No list matches "{selector}". Known lists: {", ".join(ids) or "none"}')
    raise SystemExit(f'"{selector}" matches several lists: {", ".join(hits)}')


def load(list_id):
    state, folder = find(list_id)
    if not folder:
        raise SystemExit(f'Unknown list {list_id}')
    lst = read_json(folder / 'list.json')
    lst.setdefault('status', {})
    lst['status']['state'] = state
    return lst, folder


def save(list_id, lst):
    _, folder = find(list_id)
    write_json(folder / 'list.json', lst)


def move(list_id, state):
    """Move a list folder between pending/ and done/, updating the paths recorded in it."""
    current, folder = find(list_id)
    if current == state:
        return folder
    target = STATES[state] / list_id
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise SystemExit(f'{rel(target)} already exists, sort out the duplicate by hand before moving {list_id}')
    shutil.move(folder, target)
    lst = read_json(target / 'list.json')
    lst.setdefault('status', {})['state'] = state
    lst['status']['folder'] = rel(target)
    if lst['status'].get('leftovers'):
        lst['status']['leftovers']['file'] = rel(target / 'leftovers.md')
    write_json(target / 'list.json', lst)
    return target
