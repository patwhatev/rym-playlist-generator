from rymlist.match import evaluate
from rymlist.normalize import norm_title

ITEM = {
    'title': 'Wet Land', 'title_latin': None, 'year': 1988, 'credited_as': None,
    'artists': [{'name': '吉村弘', 'name_latin': 'Hiroshi Yoshimura'}],
}


def album(**over):
    base = {
        'id': 'A' * 22, 'name': 'Wet Land', 'release_date': '1988-01-01', 'total_tracks': 8,
        'upc': '00123456789012', 'artists': [{'id': 'artistX', 'name': 'Hiroshi Yoshimura'}],
    }
    return base | over


def mb(**over):
    base = {'spotify_album_ids': set(), 'barcodes': set(), 'spotify_artist_ids': set(),
            'track_counts': set(), 'various_artists': False}
    return base | over


def test_romanized_name_and_year_is_likely():
    v = evaluate(ITEM, album(), None, {'artistX'})
    assert v['status'] == 'likely'


def test_barcode_verifies():
    v = evaluate(ITEM, album(), mb(barcodes={'123456789012'}), set())
    assert v['status'] == 'verified'


def test_linked_artist_plus_year_verifies():
    v = evaluate(ITEM, album(), mb(spotify_artist_ids={'artistX'}), set())
    assert v['status'] == 'verified'


def test_same_name_different_artist_rejected():
    v = evaluate(ITEM, album(), mb(spotify_artist_ids={'someoneElse'}), set())
    assert v['status'] is None
    assert 'same-name' in v['reason']


def test_other_artist_with_same_name_makes_it_uncertain():
    v = evaluate(ITEM, album(), None, {'artistX', 'artistY'})
    assert v['status'] == 'uncertain'


def test_reissue_needs_review():
    v = evaluate(ITEM, album(release_date='2020-02-21'), None, {'artistX'})
    assert v['status'] == 'uncertain'
    assert any('reissue' in w for w in v['warnings'])


def test_reissue_confirmed_by_barcode_is_verified():
    v = evaluate(ITEM, album(release_date='2020-02-21'), mb(barcodes={'123456789012'}), set())
    assert v['status'] == 'verified'


def test_spotify_release_before_original_rejected():
    v = evaluate(ITEM, album(release_date='1975'), None, set())
    assert v['status'] is None


def test_different_title_ignored():
    v = evaluate(ITEM, album(name='Green'), None, set())
    assert v['status'] is None


def test_edition_noise_stripped():
    assert norm_title('Wet Land (2019 Remaster)') == norm_title('Wet Land')
    assert norm_title('Wet Land - Remastered 2019') == 'wet land'
    assert norm_title('Wet Land (Live)') != norm_title('Wet Land')


def test_long_queries_fit_spotifys_limit():
    from rymlist.spotify import MAX_QUERY, album_query
    title = 'Electronic Music Experimental Studios in Prague, Bratislava, Munich, University of Illinois, Warsaw, Paris'
    artist = ('Experimental Studio of Electronic Music / Experimental Studio of Slovak Radio / Studio für elektronische Musik / '
              'University of Illinois Experimental Music Studio / Studio Eksperymentalne Polskiego Radia / '
              'Groupe de Recherches Musicales de la RTF')
    q = album_query(title, artist)
    assert len(q) <= MAX_QUERY
    assert q == f'album:{title} artist:Experimental Studio of Electronic Music'
    assert len(album_query(None, artist)) <= MAX_QUERY
    assert len(album_query('word ' * 80, None)) <= MAX_QUERY
    assert len(album_query('word ' * 80, 'x' * 300)) <= MAX_QUERY
    # short queries are unchanged, so existing cache entries still hit
    assert album_query('Octopussy', 'Yuki Nakayamate') == 'album:Octopussy artist:Yuki Nakayamate'


def test_a_failing_release_does_not_stop_the_list(monkeypatch, tmp_path):
    from rymlist import cli, match as match_mod, store

    items = [{'position': n, 'page': 1, 'rym_url': f'https://rateyourmusic.com/release/album/a/{n}/', 'rym_type': 'album',
              'type_label': 'Album', 'title': f't{n}', 'title_latin': None, 'year': 2000, 'credited_as': None,
              'artists': [{'name': 'a', 'name_latin': None}]} for n in (1, 2, 3)]
    lst = {'id': 'x', 'title': 'X', 'url': 'u', 'user': 'me', 'captured_at': 'now', 'items': items, 'status': {}}
    monkeypatch.setattr(store, 'load', lambda _id: (lst, tmp_path))
    monkeypatch.setattr(store, 'save', lambda *_: None)
    monkeypatch.setattr(cli, 'write_reports', lambda *_: None)
    monkeypatch.setattr(store, 'rel', str)

    def fake_match(item, *_, **__):
        if item['position'] == 2:
            raise ValueError('Query exceeds maximum length of 250 characters')
        return match_mod._base(item) | {'status': 'not_found', 'reason': 'nope', 'evidence': [], 'warnings': [], 'rejected': []}

    monkeypatch.setattr(match_mod, 'match_item', fake_match)
    from types import SimpleNamespace
    clients = SimpleNamespace(spotify=None, mb=None, discogs=None)
    doc = cli.match('x', 'spotify', clients, {})
    assert [r['status'] for r in doc['results']] == ['not_found', 'error', 'not_found']
    assert 'maximum length' in doc['results'][1]['reason']
    assert lst['status']['services']['spotify']['counts']['error'] == 1
    assert lst['status']['services']['spotify']['not_included'] == 3

    lst['items'] = items * 3
    monkeypatch.setattr(match_mod, 'match_item', lambda *_, **__: (_ for _ in ()).throw(ConnectionError('down')))
    try:
        cli.match('x', 'spotify', clients, {})
        assert False, 'a run of failures should stop the list'
    except RuntimeError as err:
        assert 'in a row' in str(err)
