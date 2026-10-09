"""Turn a capture from the browser extension into a list of releases."""
import re
from urllib.parse import urljoin, unquote

from bs4 import BeautifulSoup

RYM = 'https://rateyourmusic.com'

# trailing "[Kaoru Todoroki]" style romanization rym adds to non-latin names
TRANSLIT = re.compile(r'^(.*?)\s*\[([^\[\]]+)\]\s*$')
REL_DATE = re.compile(r'\((\d{4})\)\s*(?:\[([^\]]+)\])?')


def split_translit(text):
    """'轟かおる [Kaoru Todoroki]' -> ('轟かおる', 'Kaoru Todoroki')"""
    text = ' '.join(text.split())
    m = TRANSLIT.match(text)
    if m and m.group(1):
        return m.group(1), m.group(2)
    return text, None


def parse_artist_link(a):
    sub = a.select_one('span.subtext')
    latin = sub.get_text(strip=True).strip('[]') if sub else None
    if sub:
        sub.extract()
    name, inline_latin = split_translit(a.get_text(' ', strip=True))
    return {
        'name': name,
        'name_latin': latin or inline_latin,
        'rym_url': urljoin(RYM, unquote(a['href'])),
    }


def parse_row(tr):
    entry = tr.select_one('td.main_entry')
    album = entry.select_one('a.list_album') if entry else None
    if not album:
        return None

    h2 = entry.select_one('h2')
    credited = h2.select_one('span.credited_name') if h2 else None
    if credited:
        # the visible credit ("Mr Strictly & PC") sits before the hidden list of real artists
        credited_as = ' '.join(s.strip() for s in credited.find_all(string=True, recursive=False)).strip()
    else:
        credited_as = None
    artists = [parse_artist_link(a) for a in entry.select('a.list_artist')]
    if not credited_as and len(artists) > 1:
        # "Jon Rose & Roger Turner": keep rym's joiners as the credit
        credited_as = ' '.join(h2.get_text(' ', strip=True).split()).replace(' ,', ',')

    href = unquote(album['href'])
    title, title_latin = split_translit(album.get_text(' ', strip=True))

    year, type_label = None, None
    rel = entry.select_one('span.rel_date')
    if rel:
        m = REL_DATE.search(rel.get_text(' ', strip=True))
        if m:
            year = int(m.group(1))
            type_label = m.group(2)

    img = tr.select_one('td.list_art img')
    cover = img.get('data-src') or img.get('src') if img else None
    if cover and ('blank.png' in cover or 'blocked_art' in cover):
        cover = None

    return {
        'rym_url': urljoin(RYM, href),
        'rym_type': href.strip('/').split('/')[1],   # album, ep, comp, djmix, single, additional, unauth...
        'type_label': type_label or 'Album',
        'title': title,
        'title_latin': title_latin,
        'year': year,
        'artists': artists,
        'credited_as': credited_as,
        'cover': urljoin('https:', cover) if cover else None,
    }


def parse_capture(capture):
    items = []
    for page in sorted(capture['pages'], key=lambda p: p['page']):
        if 'Just a moment' in page['html'][:5000]:
            raise ValueError(f"page {page['page']} is a cloudflare challenge, recapture the list")
        soup = BeautifulSoup(page['html'], 'lxml')
        for tr in soup.select('tr'):
            item = parse_row(tr)
            if item:
                items.append(item)

    for position, item in enumerate(items, 1):
        item['position'] = position

    title = re.sub(r'\s*-\s*Rate Your Music\s*$', '', capture.get('title', '')).strip()
    return {
        'id': f"{capture['user']}__{capture['slug']}",
        'title': title,
        'user': capture['user'],
        'url': capture['url'],
        'captured_at': capture['captured_at'],
        'pages': len(capture['pages']),
        'items': items,
    }
