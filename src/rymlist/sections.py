"""Optional per-list sections from sections.toml: page number -> section name.

    ["DazedandBrutal__some-list"]     # list id, or any unique part of it
    1 = "Finnish Death Metal"
    2 = "Raw Black Metal"
    36 = "Spookies: Body Horror"

Each section becomes its own playlist. Pages sharing a name are merged; pages not
listed go into an "Other" section.
"""
import tomllib

from . import paths

OTHER = 'Other'


def load(list_id):
    if not paths.SECTIONS.exists():
        return {}
    config = tomllib.loads(paths.SECTIONS.read_text())
    keys = [k for k in config if k == list_id] or [k for k in config if k.lower() in list_id.lower()]
    if not keys:
        return {}
    if len(keys) > 1:
        raise SystemExit(f'sections.toml: several entries match {list_id}: {", ".join(keys)}')
    pages = {}
    for page, name in config[keys[0]].items():
        if not str(page).isdigit():
            raise SystemExit(f'sections.toml [{keys[0]}]: "{page}" is not a page number')
        pages[int(page)] = str(name).strip()
    return pages


def group(results, pages):
    """[(section name or None, [results])] in list order. No config -> one group named None."""
    if not pages:
        return [(None, list(results))]
    groups = {}
    for r in results:
        name = pages.get(r.get('page'), OTHER)
        groups.setdefault(name, []).append(r)
    return list(groups.items())
