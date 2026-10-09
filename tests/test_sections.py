import pytest

from rymlist import paths, sections


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / 'sections.toml'
    monkeypatch.setattr(paths, 'SECTIONS', path)
    return path


def test_no_config_is_one_group(config):
    results = [{'page': 1}, {'page': 2}]
    assert sections.group(results, sections.load('u__list')) == [(None, results)]


def test_pages_grouped_merged_and_other(config):
    config.write_text('["some-list"]\n1 = "Doom"\n2 = "Noise"\n3 = "Doom"\n')
    pages = sections.load('user__some-list')
    results = [{'page': 1, 'n': 'a'}, {'page': 2, 'n': 'b'}, {'page': 3, 'n': 'c'}, {'page': 4, 'n': 'd'}]
    groups = dict(sections.group(results, pages))
    assert [r['n'] for r in groups['Doom']] == ['a', 'c']
    assert [r['n'] for r in groups['Noise']] == ['b']
    assert [r['n'] for r in groups[sections.OTHER]] == ['d']


def test_bad_page_key(config):
    config.write_text('["some-list"]\nfirst = "Doom"\n')
    with pytest.raises(SystemExit):
        sections.load('user__some-list')
