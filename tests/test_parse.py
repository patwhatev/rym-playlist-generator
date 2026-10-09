import json
from pathlib import Path

import pytest

from rymlist.parse import parse_capture, split_translit

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'capture.json').read_text())


@pytest.fixture(scope='module')
def items():
    return {i['title']: i for i in parse_capture(FIXTURE)['items']}


def test_list_metadata():
    lst = parse_capture(FIXTURE)
    assert lst['id'] == 'tester__fixture'
    assert lst['title'] == 'Fixture List'
    assert [i['position'] for i in lst['items']] == list(range(1, len(lst['items']) + 1))


def test_romanized_artist_from_subtext(items):
    item = items["Uncle Calvin's Private Life"]
    assert item['artists'][0]['name'] == '轟かおる'
    assert item['artists'][0]['name_latin'] == 'Kaoru Todoroki'
    assert item['year'] == 1985
    assert item['rym_type'] == 'album'


def test_romanized_artist_inline(items):
    item = items['みんなたのしく']
    assert item['artists'][0]['name'] == '少年ナイフ'
    assert item['artists'][0]['name_latin'] == 'Shonen Knife'
    assert item['title_latin'] == 'Minna tanoshiku'


def test_credited_name_keeps_real_artists(items):
    item = items['Blech']
    assert item['credited_as'] == 'Mr Strictly & PC'
    assert [a['name'] for a in item['artists']] == ['Kevin Foakes', 'Patrick Carpenter']


def test_plain_collaboration(items):
    item = items['Trouble in the Woodwork']
    assert item['credited_as'] == 'Jon Rose & Roger Turner'
    assert len(item['artists']) == 2


def test_missing_year(items):
    item = items['บูรณาการ']
    assert item['year'] is None
    assert item['title_latin'] == 'Buranakan'


def test_blocked_cover_dropped(items):
    assert items['Hej Hitler']['cover'] is None


def test_cloudflare_page_rejected():
    bad = dict(FIXTURE, pages=[{'page': 1, 'url': 'x', 'html': '<title>Just a moment...</title>'}])
    with pytest.raises(ValueError):
        parse_capture(bad)


def test_split_translit():
    assert split_translit('Plain Title') == ('Plain Title', None)
    assert split_translit('夜想曲 [Yasoukyoku]') == ('夜想曲', 'Yasoukyoku')
