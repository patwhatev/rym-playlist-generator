import pytest

from rymlist import paths, store


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'ROOT', tmp_path)
    monkeypatch.setattr(store, 'STATES', {'pending': tmp_path / 'pending', 'done': tmp_path / 'done'})
    folder = tmp_path / 'pending' / 'u__list'
    store.write_json(folder / 'list.json', {
        'id': 'u__list', 'items': [],
        'status': {'state': 'pending', 'leftovers': {'count': 3, 'file': 'pending/u__list/leftovers.md'}},
    })
    (folder / 'leftovers.md').write_text('x')
    return tmp_path


def test_move_to_done_carries_files_and_updates_paths(repo):
    store.move('u__list', 'done')
    assert not (repo / 'pending' / 'u__list').exists()
    assert (repo / 'done' / 'u__list' / 'leftovers.md').exists()
    lst, folder = store.load('u__list')
    assert lst['status']['state'] == 'done'
    assert lst['status']['folder'] == 'done/u__list'
    assert lst['status']['leftovers']['file'] == 'done/u__list/leftovers.md'


def test_done_lists_are_not_pending(repo):
    store.move('u__list', 'done')
    assert store.all_lists() == [('done', 'u__list')]
    assert store.resolve('all', state='pending') == []
    with pytest.raises(SystemExit):
        store.resolve('u__list', state='pending')


def test_move_refuses_to_overwrite(repo):
    store.write_json(repo / 'done' / 'u__list' / 'list.json', {'id': 'u__list', 'items': []})
    with pytest.raises(SystemExit):
        store.move('u__list', 'done')
    assert (repo / 'pending' / 'u__list' / 'leftovers.md').exists()
