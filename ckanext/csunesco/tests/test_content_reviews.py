import copy
import pytest
import ckan.plugins.toolkit as tk
from ckanext.csunesco import db
from ckanext.csunesco.logic import content_reviews as reviews, portal
from ckanext.csunesco.logic.action import content as actions
from ckanext.csunesco.tests.test_project_portal import store, ctx


def envelope(project, revision=1, title="Sampling day", intent="submit"):
    payload = {"content_type": "cs-event", "title": title, "body": "<p>Join us</p>",
               "publish_date": "2026-09-20T09:30:00", "location": "River shore", "visibility": "public"}
    return {"schema_version": 2, "app_project_id": 42, "app_content_id": 73,
            "project": {"id": project.id}, "actor": {"username": "author"},
            "revision": revision, "checksum": portal.checksum(payload), "intent": intent,
            "content_type": "cs-event", "payload": payload}


def pending(project):
    return db.Session.query(db.CsContent).filter_by(project_id=project.id, status="pending").one()


def test_review_uses_existing_queue_and_preserves_stable_public_url(store, monkeypatch):
    project, _ = store
    callbacks = []
    monkeypatch.setattr(reviews, "callback", lambda row: callbacks.append(reviews.result(row)))
    first = envelope(project)
    assert reviews.apply(ctx(), first)["status"] == "pending"
    row = pending(project)
    assert row.created_by == "author"
    assert reviews.apply(ctx(), first)["status"] == "pending"
    assert db.Session.query(db.CsContent).count() == 1
    published = actions.csunesco_content_approve(ctx("reviewer"), {"id": row.id})
    assert published["location"] == "River shore"
    assert published["publish_date"] == "2026-09-20T09:30:00"
    target = db.get_content(published["id"])
    original_slug = target.slug
    reviews.apply(ctx(), envelope(project, 2, "Edited event"))
    assert target.title == "Sampling day"
    actions.csunesco_content_reject(ctx("reviewer"), {"id": pending(project).id, "reason": "Clarify the venue"})
    assert target.status == "approved"
    assert target.title == "Sampling day"
    reviews.apply(ctx(), envelope(project, 3, "Corrected event"))
    actions.csunesco_content_approve(ctx("reviewer"), {"id": pending(project).id})
    assert target.title == "Corrected event"
    assert target.slug == original_slug
    assert callbacks[-1]["revision"] == 3
    assert db.Session.query(db.CsContent).filter_by(status="approved").count() == 1


def test_withdrawal_supersedes_pending_review_and_retries_are_idempotent(store, monkeypatch):
    project, _ = store
    monkeypatch.setattr(reviews, "callback", lambda row: None)
    reviews.apply(ctx(), envelope(project))
    row = pending(project)
    withdrawal = envelope(project, 2, intent="withdraw")
    assert reviews.apply(ctx(), withdrawal)["status"] == "withdrawn"
    assert reviews.apply(ctx(), withdrawal)["status"] == "withdrawn"
    with pytest.raises(tk.ValidationError):
        actions.csunesco_content_approve(ctx("reviewer"), {"id": row.id})
    assert db.Session.query(db.CsContent).filter_by(status="approved").count() == 0


def test_transport_actor_checksum_and_revision_boundaries(store):
    project, _ = store
    data = envelope(project)
    with pytest.raises(tk.NotAuthorized):
        reviews.apply(ctx("author"), data)
    forged = copy.deepcopy(data)
    forged["actor"] = {"username": "outsider"}
    with pytest.raises(tk.NotAuthorized):
        reviews.apply(ctx(), forged)
    forged = copy.deepcopy(data)
    forged["payload"]["title"] = "Changed after signing"
    with pytest.raises(tk.ValidationError):
        reviews.apply(ctx(), forged)
    reviews.apply(ctx(), data)
    with pytest.raises(tk.ValidationError):
        reviews.apply(ctx(), envelope(project, title="Reused revision"))


def test_parameter_chart_references_are_normalized_and_bounded():
    from ckanext.csunesco.logic import blocks
    value = blocks.normalize_block({"type": "builtin_data", "parameter_charts": [
        {"parameter": "<b>pH</b>", "field": "ph", "data_source_id": "source-1", "automatic": False},
        {"parameter": "Bad", "field": "x<script>", "data_source_id": "https://outside.test"},
    ]})
    assert value["parameter_charts"][0]["parameter"] == "pH"
    assert value["parameter_charts"][0]["automatic"] is False
    assert value["parameter_charts"][1]["field"] == "x"
    assert not value["parameter_charts"][1]["data_source_id"]


@pytest.mark.parametrize('content_type,fields', [
    ('cs-news', {'header_image_url': 'https://example.test/header.jpg',
                 'header_image_alt': 'Sampling volunteers', 'header_focal_x': 25,
                 'gallery': [{'url': 'https://example.test/photo.jpg', 'alt': 'River'}]}),
    ('cs-publication', {'media': ['https://example.test/paper.pdf'],
                        'authors': 'River team', 'doi': '10.1234/river'}),
    ('cs-map', {'terria_url': 'https://maps.example/terria/#share=river'}),
])
def test_each_content_type_preserves_its_fields_through_review(store, monkeypatch, content_type, fields):
    from ckanext.csunesco.logic import validators
    monkeypatch.setattr(validators, 'terria_allowed_bases', lambda: ['https://maps.example/terria'])
    monkeypatch.setattr(reviews, 'callback', lambda row: None)
    project, _ = store
    data = envelope(project)
    data['content_type'] = data['payload']['content_type'] = content_type
    data['payload'].update(fields)
    data['checksum'] = portal.checksum(data['payload'])
    reviews.apply(ctx(), data)
    published = actions.csunesco_content_approve(ctx('reviewer'), {'id': pending(project).id})
    for field, value in fields.items():
        if field == 'gallery':
            assert published[field][0]['url'] == value[0]['url']
        else:
            assert published[field] == value
