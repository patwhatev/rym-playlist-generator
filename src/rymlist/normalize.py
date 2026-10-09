"""Name/title normalization and similarity used for match validation."""
import re
import unicodedata

from rapidfuzz import fuzz

# edition noise streaming services append that rym doesn't use
EDITION_WORDS = r'(remaster(ed)?|deluxe|expanded|edition|reissue|anniversary|bonus|special|collector|re-?issue|digital)'
EDITION_PAREN = re.compile(r'\s*[\(\[][^\)\]]*' + EDITION_WORDS + r'[^\)\]]*[\)\]]', re.I)
EDITION_DASH = re.compile(r'\s+-\s+[^-]*' + EDITION_WORDS + r'.*$', re.I)


def norm(text):
    if not text:
        return ''
    text = unicodedata.normalize('NFKC', text).casefold()
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    text = text.replace('&', ' and ').replace('+', ' and ')
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'^the\s+', '', text.strip())
    return ' '.join(text.split())


def norm_title(text):
    if not text:
        return ''
    text = EDITION_DASH.sub('', EDITION_PAREN.sub('', text))
    return norm(text)


def similarity(a, b):
    """0-100. short strings must match exactly, fuzzy matching is meaningless there."""
    if not a or not b:
        return 0
    if a == b:
        return 100
    if min(len(a), len(b)) <= 3:
        return 0
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))


def best_similarity(left, right, normalizer=norm):
    left = {normalizer(x) for x in left if x}
    right = {normalizer(x) for x in right if x}
    return max((similarity(a, b) for a in left for b in right), default=0)


def item_artist_names(item):
    names = [item.get('credited_as')]
    for artist in item['artists']:
        names += [artist['name'], artist['name_latin']]
    if len(item['artists']) > 1:
        names.append(' & '.join(a['name'] for a in item['artists']))
        names.append(' & '.join(a['name_latin'] or a['name'] for a in item['artists']))
    return [n for n in names if n]


def item_titles(item):
    return [t for t in (item['title'], item.get('title_latin')) if t]
