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
