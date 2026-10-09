from rymlist.discogs import parse_duration, tracklist_runtime
from rymlist.report import coverage, render
from rymlist.ytmatch import Runtime, judge, match_item, names, queries, tolerances


def item(title='Nespithe', artist='Demilich', rym_type='album', year=1993, **extra):
    return {'position': 1, 'page': 1, 'rym_url': f'https://rateyourmusic.com/release/{rym_type}/x/{title}/',
            'rym_type': rym_type, 'type_label': {'album': 'Album', 'ep': 'EP', 'single': 'Single'}[rym_type],
            'title': title, 'title_latin': None, 'year': year, 'credited_as': None,
            'artists': [{'name': artist, 'name_latin': None}]} | extra


def video(title, duration, channel='Some Uploader', vid='aaaaaaaaaaa', views=1000):
    return {'id': vid, 'title': title, 'duration': duration, 'channel': channel, 'channel_id': 'c', 'views': views}


NESPITHE = {'seconds': 2350, 'source': 'MusicBrainz'}


def test_duration_against_known_runtime():
    assert judge(item(), video('Demilich - Nespithe (Full Album)', 2352), NESPITHE)['status'] == 'verified'
    assert judge(item(), video('Demilich - Nespithe (Full Album)', 2350 + 200), NESPITHE)['status'] == 'likely'
    assert judge(item(), video('Demilich - Nespithe (Full Album)', 2350 + 420), NESPITHE)['status'] == 'uncertain'
    wrong = judge(item(), video('Demilich Nespithe (Full Album)', 4417), NESPITHE)
    assert wrong['status'] is None and 'wrong length' in wrong['reason']


def test_floors_without_runtime():
    full = 'Demilich - Nespithe (Full Album)'
    assert judge(item(), video(full, 38 * 60), None)['status'] == 'likely'
    assert judge(item(), video('Demilich - Nespithe', 38 * 60), None)['status'] == 'uncertain'  # not labelled
    short = judge(item(), video(full, 9 * 60), None)  # punk/grind length: kept, but for review
    assert short['status'] == 'uncertain' and 'short' in short['warnings'][0]
    assert judge(item(), video(full, 3 * 60), None)['status'] is None  # a single track
    assert judge(item(rym_type='ep'), video('Demilich - Nespithe (Full EP)', 12 * 60), None)['status'] == 'likely'
    assert judge(item(rym_type='ep'), video('Demilich - Nespithe EP', 2 * 60), None)['status'] is None
    assert judge(item(), video(full, 5 * 3600), None)['status'] == 'uncertain'  # discography upload?


def test_names_and_junk():
    assert judge(item(), video('Demilich - Nespithe REVIEW', 2352), NESPITHE)['reason'].startswith('looks like a review')
    assert judge(item(), video('Demilich - Nespithe live at Tuska 2014', 2352), NESPITHE)['status'] is None
    assert judge(item(title='Live Evil', artist='Black Sabbath'),
                 video('Black Sabbath - Live Evil (Full Album)', 5000), None)['status'] == 'likely'
    assert judge(item(), video('Demilich - Two Independent Primitive Expressions', 2352), NESPITHE)['status'] is None
    # artist as the channel ("Artist - Topic" or their own channel) counts for the artist
    a, t, official = names(item(), video('Nespithe', 2352, channel='Demilich - Topic'))
    assert a >= 90 and t >= 90 and official
    # a long rym title isn't "found" inside a short video title
    long = item(title='Electronic Music: Experimental Studios in Prague, Bratislava, Munich')
    assert names(long, video('Demilich - Electronic Music', 2000))[1] < 90


def test_queries():
    qs = queries(item())
    assert qs[0] == '"Demilich" "Nespithe" full album'
    assert qs[-1] == 'Demilich Nespithe'
    assert queries(item(rym_type='ep'))[0].endswith('full EP')
    jp = item(title='うんたん', artist='三毛猫ホームレス', title_latin='Untan')
    jp['artists'][0]['name_latin'] = 'Mikeneko Homeless'
    assert '"Mikeneko Homeless" "Untan" full album' in queries(jp)


class FakeSearch:
    def __init__(self, results):
        self.results, self.queries = results, []

    def search(self, q):
        self.queries.append(q)
        return self.results.get(q, [])


class FakeSource:
    def __init__(self, value):
        self.value, self.calls = value, 0

    def runtime(self, item):
        self.calls += 1
        return self.value


def test_match_item_picks_right_length_and_only_asks_discogs_when_needed():
    hits = [video('Demilich Nespithe (Full Album)', 4417, vid='longlonglon', views=9999),
            video('Demilich - Nespithe (Full Album)', 2352, vid='rightrightr')]
    search = FakeSearch({queries(item())[0]: hits})
    mb, discogs = FakeSource({'seconds': 2350, 'tracks': 11, 'url': 'mb'}), FakeSource(None)
    r = match_item(item(), search, Runtime(item(), mb=mb, discogs=discogs))
    assert r['status'] == 'verified' and r['video_ids'] == ['rightrightr']
    assert r['rejected'][0]['reason'].startswith('wrong length')
    assert len(search.queries) == 1  # stopped once something solid turned up
    assert discogs.calls == 0  # musicbrainz had the runtime

    # nothing matching the names: no runtime lookups at all, discogs untouched
    mb, discogs = FakeSource(None), FakeSource(None)
    r = match_item(item(), FakeSearch({}), Runtime(item(), mb=mb, discogs=discogs))
    assert r['status'] == 'not_found' and mb.calls == 0 and discogs.calls == 0

    # candidate but no musicbrainz runtime: now discogs is asked
    mb, discogs = FakeSource(None), FakeSource({'seconds': 2350, 'tracks': 11, 'url': 'dg'})
    r = match_item(item(), search, Runtime(item(), mb=mb, discogs=discogs))
    assert r['status'] == 'verified' and discogs.calls == 1 and r['runtime']['source'] == 'Discogs'


def test_discogs_durations():
    assert parse_duration('4:31') == 271 and parse_duration('1:02:03') == 3723 and parse_duration('') is None
    assert tracklist_runtime([{'type_': 'track', 'duration': '2:00'}, {'type_': 'heading', 'duration': ''},
                              {'type_': 'track', 'duration': '1:30'}]) == (210, 2)
    assert tracklist_runtime([{'type_': 'track', 'duration': '2:00'}, {'type_': 'track', 'duration': ''}]) == (None, 2)


def test_report_shows_what_was_left_out_per_service():
    sp = [{'position': n, 'status': s} for n, s in enumerate(['likely', 'not_found', 'not_found', 'uncertain'], 1)]
    yt = [{'position': n, 'status': s} for n, s in enumerate(['likely', 'verified', 'not_found', 'likely'], 1)]
    assert coverage(sp)['not_included'] == 3 and coverage(yt)['included'] == 3
    base = {'rym_url': 'u', 'title': 't', 'title_latin': None, 'year': 2000, 'type_label': 'Album', 'credited_as': None,
            'artists': ['a'], 'evidence': [], 'warnings': [], 'rejected': [], 'reason': 'x', 'page': 1}
    lst = {'title': 'L', 'url': 'u', 'user': 'me', 'captured_at': '2026-10-09'}
    md = render(lst, {'spotify': [base | r for r in sp], 'youtube': [base | r | ({'youtube': {
        'url': 'v', 'title': 'v', 'channel': 'c', 'duration': 100}} if r['status'] != 'not_found' else {}) for r in yt]})
    assert '| Spotify | **1** (25%) | **3** (75%) |' in md
    assert '| YouTube | **3** (75%) | **1** (25%) |' in md
    assert '0 releases are only on Spotify, 2 only on YouTube, 1 on both; 1 of 4 are on neither.' in md


def test_short_releases_get_tighter_tolerances():
    single = item(title='San Francisco Night', artist='Chris', rym_type='single')
    ref = {'seconds': 334, 'source': 'MusicBrainz'}
    assert judge(single, video('Chris - San Francisco Night', 330), ref)['status'] == 'verified'
    assert judge(single, video('Chris - San Francisco Night', 244), ref)['status'] == 'uncertain'  # 90s short
    assert judge(single, video('Chris - San Francisco Night (Mike Burns Edit)', 334), ref)['status'] is None
    assert tolerances(2400)[0] == 120  # albums: plain 5%


def test_discogs_skipped_when_one_clear_upload():
    clear = [video('Demilich - Nespithe (Full Album)', 2352, vid='rightrightr')]
    mb, discogs = FakeSource(None), FakeSource({'seconds': 2350, 'tracks': 11, 'url': 'dg'})
    r = match_item(item(), FakeSearch({queries(item())[0]: clear}), Runtime(item(), mb=mb, discogs=discogs))
    assert r['status'] == 'likely' and discogs.calls == 0 and mb.calls == 1
    # same upload but not labelled "full": only "needs review" on its own, so discogs is worth asking
    plain = [video('Demilich - Nespithe', 2352, vid='rightrightr')]
    discogs = FakeSource({'seconds': 2350, 'tracks': 11, 'url': 'dg'})
    r = match_item(item(), FakeSearch({queries(item())[0]: plain}), Runtime(item(), mb=FakeSource(None), discogs=discogs))
    assert r['status'] == 'verified' and discogs.calls == 1
