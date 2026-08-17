# encoding: utf-8
"""Initiative pages reuse the project-page lifecycle without a migration."""
import pytest

try:
    import sqlalchemy as sa
    import ckan.plugins.toolkit as tk
    from ckanext.csunesco import db
    from ckanext.csunesco.logic import blocks
    from ckanext.csunesco.logic.action import page as page_action
    HAVE_CKAN = True
except Exception:  # pragma: no cover
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(not HAVE_CKAN, reason='requires CKAN')


class _User(object):
    is_anonymous = False
    id = 'adm-1'


def _ctx():
    return {'user': 'adm-1', 'auth_user_obj': _User()}


@pytest.fixture
def session(monkeypatch):
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    db.metadata.create_all(bind=engine, tables=list(db._ALL_TABLES))
    db.Session.remove()
    db.Session.configure(bind=engine)
    monkeypatch.setattr(tk, 'check_access', lambda *args, **kwargs: True)
    try:
        yield db.Session
    finally:
        db.Session.remove()
        engine.dispose()


def test_unwritten_page_uses_a_stable_sentinel(session):
    result = page_action.csunesco_initiative_page_show(
        _ctx(), {'initiative': 'riverwatch', 'include_draft': True})
    assert result['project_id'] == '__initiative__:riverwatch'
    assert result['published_blocks'] is None
    assert result['draft_blocks'] is None


def test_update_seeds_builtins_and_publish_is_direct(session):
    saved = page_action.csunesco_initiative_page_update(
        _ctx(), {'initiative': 'riverwatch', 'blocks': [
            {'type': 'rich_text', 'html': '<p>River communities</p>'},
        ]})
    types = {item['type'] for item in saved['draft_blocks']}
    assert 'rich_text' in types
    assert set(blocks.INITIATIVE_DEFAULT_BLOCK_TYPES) <= types
    assert saved['published_blocks'] is None

    published = page_action.csunesco_initiative_page_publish(
        _ctx(), {'initiative': 'riverwatch'})
    assert published['status'] == 'approved'
    assert published['published_blocks'] == published['draft_blocks']


def test_unknown_initiative_cannot_create_a_sentinel_row(session):
    with pytest.raises(tk.ObjectNotFound):
        page_action.csunesco_initiative_page_update(
            _ctx(), {'initiative': 'forged', 'blocks': []})
    assert session.query(db.CsProjectPage).count() == 0
