# encoding: utf-8
"""Database models for ckanext-csunesco (Citizen Science / UNESCO).

Classic SQLAlchemy ``Table`` + ``mapper`` style (mirroring ckanext-colab and
ckanext-pages) bound to CKAN's shared metadata so the tables live in the same
database as core CKAN.

Design notes (see .mix/plan.md):
  * Frequently-filtered fields (slug, status, project_id, initiative_group,
    created_by, ...) are NATIVE, INDEXED columns for query performance.
  * Everything else lives in a per-row ``extras`` Text column (JSON) so the
    schema stays small -- the same "Page + extras" shape as water-family.

Increment 1 = scaffold only: tables + an idempotent ``ensure_tables()`` plus a
conservative ``_ensure_columns()`` auto-heal. No domain logic yet.
"""
import datetime
import json
import logging
import re
import uuid

from sqlalchemy import (
    Table,
    Column,
    types,
    Index,
    UniqueConstraint,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer
import sqlalchemy as sa

from ckan.model.meta import metadata, mapper, Session  # noqa: F401
from ckan.model.domain_object import DomainObject

log = logging.getLogger(__name__)


def make_uuid():
    return str(uuid.uuid4())


def _utcnow():
    return datetime.datetime.utcnow()


# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------

cs_project_table = Table(
    'cs_project', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('slug', types.UnicodeText, index=True, unique=True),
    Column('title', types.UnicodeText),
    Column('short_description', types.UnicodeText),
    Column('initiative_group', types.UnicodeText, index=True),
    Column('countries', types.Text),            # JSON
    Column('biosphere_reserve', types.UnicodeText),
    Column('region_geojson', types.Text),
    Column('project_document_url', types.UnicodeText),
    Column('landing_content', types.Text),
    Column('organization_id', types.UnicodeText, index=True),
    Column('status', types.UnicodeText, index=True, default=u'pending'),
    Column('created_by', types.UnicodeText, index=True),
    Column('reviewed_by', types.UnicodeText),
    Column('reviewed_at', types.DateTime),
    Column('rejection_reason', types.Text),
    Column('extras', types.Text, default=u'{}'),
    Column('created', types.DateTime, default=_utcnow),
    Column('modified', types.DateTime, default=_utcnow),
)

cs_project_member_table = Table(
    'cs_project_member', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('project_id', types.UnicodeText, index=True),
    Column('user_id', types.UnicodeText, index=True),
    Column('role', types.UnicodeText, default=u'scientist'),
    Column('status', types.UnicodeText, index=True, default=u'pending'),
    Column('source', types.UnicodeText, default=u'ckan'),
    Column('created', types.DateTime, default=_utcnow),
    UniqueConstraint('project_id', 'user_id',
                     name='uq_cs_project_member_project_user'),
)

cs_content_table = Table(
    'cs_content', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    # Native slug so /news/<slug> and /events/<slug> resolve with a single
    # indexed lookup (never a scan). Globally unique across content types.
    Column('slug', types.UnicodeText, index=True, unique=True),
    Column('content_type', types.UnicodeText, index=True),
    Column('project_id', types.UnicodeText, index=True),
    Column('initiative_group', types.UnicodeText, index=True),
    Column('title', types.UnicodeText),
    Column('body', types.Text),
    Column('media', types.Text),                # JSON
    Column('publish_date', types.DateTime),
    Column('end_date', types.DateTime),
    Column('status', types.UnicodeText, index=True, default=u'draft'),
    Column('featured', types.Boolean, default=False),
    Column('created_by', types.UnicodeText, index=True),
    Column('extras', types.Text, default=u'{}'),
    Column('created', types.DateTime, default=_utcnow),
    Column('modified', types.DateTime, default=_utcnow),
    # Composite indexes for the two hot listing paths, both filtered by status
    # and ordered by publish_date:
    #   * a single project's content of one type (landing / project page),
    #   * all content of one type (public /news and /events indexes).
    Index('ix_cs_content_project_type_status_date',
          'project_id', 'content_type', 'status', 'publish_date'),
    Index('ix_cs_content_type_status_date',
          'content_type', 'status', 'publish_date'),
)

cs_project_stats_table = Table(
    'cs_project_stats', metadata,
    Column('project_id', types.UnicodeText, primary_key=True, index=True),
    Column('citizen_scientists', types.Integer, default=0),
    Column('observations', types.Integer, default=0),
    Column('sites_monitored', types.Integer, default=0),
    Column('member_states', types.Integer, default=0),
    Column('modified', types.DateTime, default=_utcnow),
)

cs_data_source_table = Table(
    'cs_data_source', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('project_id', types.UnicodeText, index=True),
    # The CS Toolbox (ofform) form whose PUBLIC endpoints feed this source.
    Column('form_id', types.Integer),
    Column('title', types.UnicodeText),
    Column('description', types.Text),
    # pending -> approved / rejected; approval is sysadmin-only and ALWAYS
    # required (even sysadmin-created sources start pending).
    Column('status', types.UnicodeText, index=True, default=u'pending'),
    # Where the connect request originated: this portal ('ckan') or the app.
    Column('source', types.UnicodeText, default=u'ckan'),
    Column('created_by', types.UnicodeText, index=True),
    Column('reviewed_by', types.UnicodeText),
    Column('reviewed_at', types.DateTime),
    Column('rejection_reason', types.Text),
    # The CKAN package created on approval (proxy-backed resources).
    Column('ckan_package_id', types.UnicodeText),
    Column('extras', types.Text, default=u'{}'),
    Column('created', types.DateTime, default=_utcnow),
    Column('modified', types.DateTime, default=_utcnow),
    UniqueConstraint('project_id', 'form_id',
                     name='uq_cs_data_source_project_form'),
)

cs_citizen_scientist_table = Table(
    'cs_citizen_scientist', metadata,
    Column('id', types.UnicodeText, primary_key=True, default=make_uuid),
    Column('user_id', types.UnicodeText, index=True, unique=True),
    # Optional self-declared country captured at registration (UNESCO member
    # state). Free text -- kept for profile/reporting, never used for auth.
    Column('country', types.UnicodeText),
    # Email-verification state for web self-registration. API/ofform-created
    # accounts are trusted and land already verified. ``verification_token`` is
    # a single-use, unguessable token (indexed for the /verify lookup) that is
    # cleared once the address is confirmed; ``token_created`` drives expiry.
    Column('email_verified', types.Boolean, default=False),
    Column('verification_token', types.UnicodeText, index=True),
    Column('token_created', types.DateTime),
    Column('created', types.DateTime, default=_utcnow),
)


# All tables this plugin owns; ``ensure_tables`` only ever touches these so we
# never accidentally reach for core CKAN tables.
_ALL_TABLES = [
    cs_project_table,
    cs_project_member_table,
    cs_content_table,
    cs_project_stats_table,
    cs_citizen_scientist_table,
    cs_data_source_table,
]


# ---------------------------------------------------------------------------
# Domain objects (classic mapper style)
# ---------------------------------------------------------------------------

class CsProject(DomainObject):
    pass


class CsProjectMember(DomainObject):
    pass


class CsContent(DomainObject):
    pass


class CsProjectStats(DomainObject):
    pass


class CsCitizenScientist(DomainObject):
    pass


class CsDataSource(DomainObject):
    pass


_mapped = False


def _ensure_mappers():
    """Wire the classic mappers exactly once."""
    global _mapped
    if _mapped:
        return
    mapper(CsProject, cs_project_table)
    mapper(CsProjectMember, cs_project_member_table)
    mapper(CsContent, cs_content_table)
    mapper(CsProjectStats, cs_project_stats_table)
    mapper(CsCitizenScientist, cs_citizen_scientist_table)
    mapper(CsDataSource, cs_data_source_table)
    _mapped = True


def ensure_mappers():
    """Public wrapper: wire the classic mappers if they are not wired yet.

    Action modules that build ORM queries directly (e.g. project_list) call this
    so they never have to reach for the private helper across module boundaries.
    """
    _ensure_mappers()


# ---------------------------------------------------------------------------
# Auto-heal whitelist
# ---------------------------------------------------------------------------
#
# SECURITY: every identifier below is a HARD-CODED constant. We NEVER build
# ALTER TABLE statements from user-supplied names, so there is no SQL-injection
# surface here. This list is the place to register any column added *after* a
# table's first release so existing deployments self-heal on startup.
#
# Tuples are (table_name, column_name, column_sql_type).
_AUTO_HEAL_COLUMNS = [
    ('cs_project', 'biosphere_reserve', 'TEXT'),
    ('cs_project', 'reviewed_by', 'TEXT'),
    ('cs_project', 'reviewed_at', 'TIMESTAMP'),
    ('cs_project', 'rejection_reason', 'TEXT'),
    ('cs_project', 'extras', "TEXT DEFAULT '{}'"),
    ('cs_content', 'featured', 'BOOLEAN DEFAULT FALSE'),
    ('cs_content', 'extras', "TEXT DEFAULT '{}'"),
    ('cs_content', 'slug', 'TEXT'),
    ('cs_project_stats', 'member_states', 'INTEGER DEFAULT 0'),
    ('cs_citizen_scientist', 'country', 'TEXT'),
    ('cs_citizen_scientist', 'email_verified', 'BOOLEAN DEFAULT FALSE'),
    ('cs_citizen_scientist', 'verification_token', 'TEXT'),
    ('cs_citizen_scientist', 'token_created', 'TIMESTAMP'),
]


def _ensure_columns(engine):
    """Add any missing whitelisted columns via ALTER TABLE.

    Only adds columns that are (a) on the hard-coded whitelist above and
    (b) genuinely absent according to the DB inspector. Keeps existing
    deployments in sync without a migration for simple additive changes.
    """
    inspector = sa.inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table_name, column_name, column_type in _AUTO_HEAL_COLUMNS:
        if table_name not in existing_tables:
            # Table will be created wholesale by create_all; nothing to alter.
            continue
        existing_columns = {c['name'] for c in inspector.get_columns(table_name)}
        if column_name in existing_columns:
            continue
        # Identifiers come only from the hard-coded whitelist -> safe.
        alter_sql = 'ALTER TABLE {t} ADD COLUMN {c} {ty}'.format(
            t=table_name, c=column_name, ty=column_type,
        )
        try:
            with engine.begin() as conn:
                conn.execute(sa.text(alter_sql))
            log.info("ckanext-csunesco: added missing column %s.%s",
                     table_name, column_name)
        except Exception:
            log.error("ckanext-csunesco: could not auto-heal a table column")


def _ensure_indexes(engine):
    """Create any missing composite index on cs_content (checkfirst=True).

    ``create_all`` only builds indexes when it first creates the owning table, so
    an index added AFTER a table's first release would never appear on existing
    deployments. This creates each ``cs_content`` index by name if absent -- run
    AFTER ``_ensure_columns`` so index columns (e.g. the auto-healed ``slug``)
    already exist. Failures are logged generically and never break startup.
    """
    for index in cs_content_table.indexes:
        try:
            index.create(bind=engine, checkfirst=True)
        except Exception:
            log.error("ckanext-csunesco: could not auto-heal a table index")


def ensure_tables():
    """Create the plugin tables if they do not exist and wire the mappers.

    Idempotent: ``create_all(..., checkfirst=True)`` skips tables that already
    exist, and the mapper wiring guards against being run twice.
    """
    from ckan.model import meta

    engine = meta.engine
    _ensure_mappers()
    metadata.create_all(bind=engine, tables=_ALL_TABLES, checkfirst=True)
    _ensure_columns(engine)
    _ensure_indexes(engine)


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------

def get_citizen_scientist(user_id):
    """Fetch the ``CsCitizenScientist`` profile for a user id, or None."""
    _ensure_mappers()
    if not user_id:
        return None
    return (
        Session.query(CsCitizenScientist)
        .filter(CsCitizenScientist.user_id == user_id)
        .first()
    )


def get_citizen_scientist_by_token(token):
    """Fetch a profile by its (single-use) verification token, or None.

    Empty/blank tokens never match -- a NULL ``verification_token`` (already
    verified) must not be reachable by an empty query string.
    """
    _ensure_mappers()
    if not token:
        return None
    return (
        Session.query(CsCitizenScientist)
        .filter(CsCitizenScientist.verification_token == token)
        .first()
    )


def get_or_create_citizen_scientist(user_id, country=None,
                                    verification_token=None):
    """Idempotently mark a CKAN user as a Citizen Scientist.

    Inserts one ``cs_citizen_scientist`` row per ``user_id``; if a row already
    exists (unique constraint on ``user_id``) it is returned unchanged and no
    second row is created. On first insert it records the optional ``country``
    and, when ``verification_token`` is given, stamps the token + its creation
    time and leaves ``email_verified`` False (web self-registration); with no
    token the profile is considered already verified (API/ofform path).

    Callers own transaction control: the mappers are wired lazily here so the
    helper is safe to call before ``ensure_tables()`` has run this process.
    """
    _ensure_mappers()

    existing = (
        Session.query(CsCitizenScientist)
        .filter(CsCitizenScientist.user_id == user_id)
        .first()
    )
    if existing is not None:
        return existing

    profile = CsCitizenScientist()
    profile.user_id = user_id
    profile.country = country or None
    if verification_token:
        profile.email_verified = False
        profile.verification_token = verification_token
        profile.token_created = _utcnow()
    else:
        # No token -> trusted server-to-server creation; nothing to verify.
        profile.email_verified = True
    Session.add(profile)
    Session.commit()
    return profile


def set_verification_token(user_id, token):
    """Stamp a fresh verification token on an UNVERIFIED profile (commits).

    Used by the resend flow. Returns the profile, or None when there is no
    profile or it is already verified (nothing to resend for).
    """
    _ensure_mappers()
    profile = get_citizen_scientist(user_id)
    if profile is None or profile.email_verified:
        return None
    profile.verification_token = token
    profile.token_created = _utcnow()
    Session.commit()
    return profile


def verify_citizen_scientist(profile):
    """Mark a profile verified and clear its token (commits). Idempotent."""
    _ensure_mappers()
    if profile is None:
        return None
    profile.email_verified = True
    profile.verification_token = None
    profile.token_created = None
    Session.commit()
    return profile


# ---------------------------------------------------------------------------
# Project / member / stats helpers (Increment 3)
# ---------------------------------------------------------------------------
#
# CONVENTION: these helpers NEVER commit. They query, mutate mapped objects or
# run atomic UPDATEs on the caller's (shared, scoped) ``Session`` and leave the
# transaction boundary to the calling action, so a whole approve/join flow can
# commit exactly once.

# Hard-coded whitelist of the integer counters ``stats_increment`` may touch.
# The field name is interpolated into the UPDATE below, so it MUST be validated
# against this set first -- there is no other source of column names.
_STATS_FIELDS = frozenset({
    'citizen_scientists', 'observations', 'sites_monitored', 'member_states',
})


def _load_json(raw, default):
    """Parse a JSON text column, returning ``default`` on empty/invalid input."""
    if raw in (None, ''):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def _slugify(text):
    """Lowercase, hyphenate and strip a title down to a URL-safe slug base."""
    text = (text or '').strip().lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = re.sub(r'-{2,}', '-', text).strip('-')
    return text


def _slug_taken(slug):
    return (
        Session.query(CsProject.id)
        .filter(CsProject.slug == slug)
        .first()
        is not None
    )


def unique_slug(base):
    """Return a slug derived from ``base`` that is not already used.

    Appends ``-2``, ``-3``, ... on collision. Falls back to a short random
    token when ``base`` slugifies to nothing (e.g. all non-ascii input).
    """
    _ensure_mappers()
    slug = _slugify(base)
    if not slug:
        slug = make_uuid()[:8]
    candidate = slug
    suffix = 2
    while _slug_taken(candidate):
        candidate = '{0}-{1}'.format(slug, suffix)
        suffix += 1
    return candidate


def get_project(id_or_slug):
    """Fetch a ``CsProject`` by primary key OR slug (None if not found)."""
    _ensure_mappers()
    if not id_or_slug:
        return None
    return (
        Session.query(CsProject)
        .filter(sa.or_(CsProject.id == id_or_slug,
                       CsProject.slug == id_or_slug))
        .first()
    )


def project_member(project_id, user_id):
    """Fetch the ``CsProjectMember`` row for a (project, user) pair, or None."""
    _ensure_mappers()
    if not project_id or not user_id:
        return None
    return (
        Session.query(CsProjectMember)
        .filter(CsProjectMember.project_id == project_id)
        .filter(CsProjectMember.user_id == user_id)
        .first()
    )


def set_member_status(project_id, user_id, status, reviewed_by=None):
    """Set a membership's status in place (no commit). Returns the member/None.

    ``reviewed_by`` is accepted for symmetry with the project review flow; the
    ``cs_project_member`` table has no reviewer column yet, so the value is not
    persisted here (reserved for a future additive column).
    """
    member = project_member(project_id, user_id)
    if member is None:
        return None
    member.status = status
    return member


def get_stats(project_id):
    """Fetch the ``CsProjectStats`` row for a project, or None."""
    _ensure_mappers()
    if not project_id:
        return None
    return (
        Session.query(CsProjectStats)
        .filter(CsProjectStats.project_id == project_id)
        .first()
    )


def ensure_stats(project_id):
    """Idempotently create the stats row for a project (no commit).

    Tries the insert inside a SAVEPOINT so a concurrent insert that wins the
    race only rolls back the nested transaction, never the caller's outer one.
    """
    _ensure_mappers()
    existing = get_stats(project_id)
    if existing is not None:
        return existing
    stats = CsProjectStats()
    stats.project_id = project_id
    stats.citizen_scientists = 0
    stats.observations = 0
    stats.sites_monitored = 0
    stats.member_states = 0
    stats.modified = _utcnow()
    savepoint = Session.begin_nested()
    try:
        Session.add(stats)
        Session.flush()
        savepoint.commit()
        return stats
    except IntegrityError:
        # Another writer inserted the row first -> reuse it.
        savepoint.rollback()
        return get_stats(project_id)


def stats_increment(project_id, field, delta):
    """Atomically add ``delta`` to a whitelisted counter; return its new value.

    SECURITY: ``field`` is validated against the hard-coded ``_STATS_FIELDS``
    whitelist before being interpolated into the SQL. ``delta`` and
    ``project_id`` are always bound parameters -- never interpolated -- and the
    counter is bumped with a single ``SET x = x + :delta`` (never read-then-write)
    so concurrent increments do not lose updates. Runs in the caller's session.
    """
    _ensure_mappers()
    if field not in _STATS_FIELDS:
        raise ValueError('Unknown stats field: %r' % field)
    Session.execute(
        sa.text(
            'UPDATE cs_project_stats SET {field} = {field} + :delta, '
            'modified = :modified WHERE project_id = :pid'.format(field=field)
        ),
        {'delta': delta, 'modified': _utcnow(), 'pid': project_id},
    )
    return Session.execute(
        sa.text('SELECT {field} FROM cs_project_stats '
                'WHERE project_id = :pid'.format(field=field)),
        {'pid': project_id},
    ).scalar()


def aggregate_stats():
    """Sum the four counters across all APPROVED projects in ONE query.

    Joins each project's counter row to its project and restricts to
    ``status='approved'``, wrapping every ``SUM`` in ``COALESCE(..., 0)`` so the
    result is always four integers (zeros when there are no approved projects,
    or no counter rows at all). Read-only: a single SELECT on the caller's
    session, never commits. The ``status`` literal is a bound parameter -- there
    is no SQL-injection surface here.
    """
    _ensure_mappers()
    row = Session.execute(
        sa.text(
            'SELECT '
            'COALESCE(SUM(s.citizen_scientists), 0), '
            'COALESCE(SUM(s.observations), 0), '
            'COALESCE(SUM(s.sites_monitored), 0), '
            'COALESCE(SUM(s.member_states), 0) '
            'FROM cs_project_stats s '
            'JOIN cs_project p ON p.id = s.project_id '
            'WHERE p.status = :status'
        ),
        {'status': 'approved'},
    ).first()
    if row is None:
        return {'citizen_scientists': 0, 'observations': 0,
                'sites_monitored': 0, 'member_states': 0}
    return {
        'citizen_scientists': int(row[0] or 0),
        'observations': int(row[1] or 0),
        'sites_monitored': int(row[2] or 0),
        'member_states': int(row[3] or 0),
    }


def project_dictize(project):
    """Flatten a ``CsProject`` to a plain dict.

    Native columns become top-level keys; ``countries`` is parsed from its JSON
    string into a list and the ``extras`` JSON blob is merged in without
    clobbering native keys (water-family ``table_dictize`` shape).
    """
    if project is None:
        return None
    result = {
        'id': project.id,
        'slug': project.slug,
        'title': project.title,
        'short_description': project.short_description,
        'initiative_group': project.initiative_group,
        'biosphere_reserve': project.biosphere_reserve,
        'region_geojson': project.region_geojson,
        'project_document_url': project.project_document_url,
        'landing_content': project.landing_content,
        'organization_id': project.organization_id,
        'status': project.status,
        'created_by': project.created_by,
        'reviewed_by': project.reviewed_by,
        'reviewed_at': (project.reviewed_at.isoformat()
                        if getattr(project, 'reviewed_at', None) else None),
        'rejection_reason': getattr(project, 'rejection_reason', None),
        'created': project.created.isoformat() if project.created else None,
        'modified': project.modified.isoformat() if project.modified else None,
    }
    result['countries'] = _load_json(project.countries, [])
    extras = _load_json(project.extras, {})
    if isinstance(extras, dict):
        for key, value in extras.items():
            result.setdefault(key, value)
    return result


def member_dictize(member):
    """Flatten a ``CsProjectMember`` to a plain dict."""
    if member is None:
        return None
    return {
        'id': member.id,
        'project_id': member.project_id,
        'user_id': member.user_id,
        'role': member.role,
        'status': member.status,
        'source': member.source,
        'created': member.created.isoformat() if member.created else None,
    }


# ---------------------------------------------------------------------------
# Content helpers (Increment 5 -- news / events)
# ---------------------------------------------------------------------------
#
# Same "commit is the caller's job" convention as the project helpers above.


def _iso(value):
    """ISO-format a datetime column, or ``None`` when it is unset."""
    return value.isoformat() if value else None


def content_dictize(content, summary=False):
    """Flatten a ``CsContent`` to a plain dict (water-family ``extras`` shape).

    Native columns become top-level keys; ``media`` is parsed from its JSON
    string into a list and the ``extras`` JSON blob is merged in without
    clobbering native keys.

    When ``summary`` is True the (potentially large) ``body`` column is NEVER
    read -- callers list with ``defer(body)`` so touching it would trigger an
    extra query per row. The truncated ``excerpt`` (stored in ``extras`` at write
    time) carries the teaser instead.
    """
    if content is None:
        return None
    result = {
        'id': content.id,
        'slug': content.slug,
        'content_type': content.content_type,
        'project_id': content.project_id,
        'initiative_group': content.initiative_group,
        'title': content.title,
        'publish_date': _iso(content.publish_date),
        'end_date': _iso(content.end_date),
        'status': content.status,
        'featured': bool(content.featured),
        'created_by': content.created_by,
        'created': _iso(content.created),
        'modified': _iso(content.modified),
    }
    result['media'] = _load_json(content.media, [])
    extras = _load_json(content.extras, {})
    if isinstance(extras, dict):
        for key, value in extras.items():
            result.setdefault(key, value)
    if not summary:
        # Full read: include the sanitized body verbatim.
        result['body'] = content.body
    return result


def _content_slug_taken(slug):
    return (
        Session.query(CsContent.id)
        .filter(CsContent.slug == slug)
        .first()
        is not None
    )


def unique_content_slug(base):
    """Return a content slug derived from ``base`` that is not already used.

    Mirrors :func:`unique_slug` for projects: appends ``-2``, ``-3``, ... on
    collision and falls back to a short random token when ``base`` slugifies to
    nothing. Slugs are permanent once assigned so URLs never break.
    """
    _ensure_mappers()
    slug = _slugify(base)
    if not slug:
        slug = make_uuid()[:8]
    candidate = slug
    suffix = 2
    while _content_slug_taken(candidate):
        candidate = '{0}-{1}'.format(slug, suffix)
        suffix += 1
    return candidate


def get_content(id_or_slug):
    """Fetch a ``CsContent`` by primary key OR slug (None if not found)."""
    _ensure_mappers()
    if not id_or_slug:
        return None
    return (
        Session.query(CsContent)
        .filter(sa.or_(CsContent.id == id_or_slug,
                       CsContent.slug == id_or_slug))
        .first()
    )


def list_content(content_type=None, project_id=None, status=None,
                 initiative_group=None, featured=None, summary=True,
                 limit=20, offset=0):
    """List content with server-side filtering + paging. Returns ``(total, rows)``.

    All filter values are bound query parameters (no SQL is built from strings).
    When ``summary`` is True the ``body`` column is deferred so it is never loaded
    for list rows -- keep the matching ``content_dictize(summary=True)`` on the
    action side so nothing accidentally reads it back.
    """
    _ensure_mappers()
    query = Session.query(CsContent)
    if summary:
        query = query.options(defer(CsContent.body))
    if content_type:
        query = query.filter(CsContent.content_type == content_type)
    if project_id:
        query = query.filter(CsContent.project_id == project_id)
    if status:
        query = query.filter(CsContent.status == status)
    if initiative_group:
        query = query.filter(CsContent.initiative_group == initiative_group)
    if featured is not None:
        query = query.filter(CsContent.featured == bool(featured))
    total = query.count()
    rows = (
        query.order_by(CsContent.publish_date.desc(),
                       CsContent.created.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return total, rows


# ---------------------------------------------------------------------------
# Admin / moderation helpers (Increment 5 -- approval panel)
# ---------------------------------------------------------------------------


def admin_project_ids(user_id):
    """Ids of every project where ``user_id`` is an ACTIVE ``admin`` member."""
    _ensure_mappers()
    if not user_id:
        return []
    rows = (
        Session.query(CsProjectMember.project_id)
        .filter(CsProjectMember.user_id == user_id)
        .filter(CsProjectMember.role == 'admin')
        .filter(CsProjectMember.status == 'active')
        .all()
    )
    return [pid for (pid,) in rows]


def pending_joins(project_ids=None, limit=20, offset=0):
    """Pending join-requests in scope. Returns ``(total, [dict, ...])``.

    ``project_ids=None`` means "every project" (sysadmin scope); a list restricts
    to those projects (project-admin scope) and an EMPTY list means "no scope" ->
    always zero. Each row is decorated with its project title/slug and the
    requesting user's display name, all resolved in bounded queries (no N+1).
    """
    _ensure_mappers()
    import ckan.model as model

    query = (
        Session.query(CsProjectMember, CsProject.title, CsProject.slug)
        .join(CsProject, CsProject.id == CsProjectMember.project_id)
        .filter(CsProjectMember.status == 'pending')
    )
    if project_ids is not None:
        if not project_ids:
            return 0, []
        query = query.filter(CsProjectMember.project_id.in_(project_ids))
    total = query.count()
    rows = (
        query.order_by(CsProjectMember.created.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    # Resolve display names for every requester in ONE query.
    user_ids = [member.user_id for (member, _t, _s) in rows]
    names = {}
    if user_ids:
        for user in (Session.query(model.User)
                     .filter(model.User.id.in_(user_ids)).all()):
            names[user.id] = user.display_name or user.name

    results = []
    for member, title, slug in rows:
        item = member_dictize(member)
        item['project_title'] = title
        item['project_slug'] = slug
        item['user_name'] = names.get(member.user_id, member.user_id)
        results.append(item)
    return total, results


def pending_content(project_ids=None, limit=20, offset=0):
    """Pending content in scope. Returns ``(total, [summary dict, ...])``.

    Same scoping rules as :func:`pending_joins`. Rows are summarized (``body``
    deferred) and decorated with their project title/slug for the review list.
    """
    _ensure_mappers()
    query = (
        Session.query(CsContent, CsProject.title, CsProject.slug)
        .outerjoin(CsProject, CsProject.id == CsContent.project_id)
        .options(defer(CsContent.body))
        .filter(CsContent.status == 'pending')
    )
    if project_ids is not None:
        if not project_ids:
            return 0, []
        query = query.filter(CsContent.project_id.in_(project_ids))
    total = query.count()
    rows = (
        query.order_by(CsContent.created.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    results = []
    for content, title, slug in rows:
        item = content_dictize(content, summary=True)
        item['project_title'] = title
        item['project_slug'] = slug
        results.append(item)
    return total, results


def _count_pending_projects():
    return (
        Session.query(CsProject.id)
        .filter(CsProject.status == 'pending')
        .count()
    )


def _count_pending_joins(project_ids=None):
    query = (
        Session.query(CsProjectMember.id)
        .filter(CsProjectMember.status == 'pending')
    )
    if project_ids is not None:
        if not project_ids:
            return 0
        query = query.filter(CsProjectMember.project_id.in_(project_ids))
    return query.count()


def _count_pending_content(project_ids=None):
    query = (
        Session.query(CsContent.id)
        .filter(CsContent.status == 'pending')
    )
    if project_ids is not None:
        if not project_ids:
            return 0
        query = query.filter(CsContent.project_id.in_(project_ids))
    return query.count()


# ---------------------------------------------------------------------------
# Data-source helpers (app-data pipeline)
# ---------------------------------------------------------------------------
#
# Same "commit is the caller's job" convention as the other helpers.


def data_source_dictize(data_source):
    """Flatten a ``CsDataSource`` to a plain dict (``extras`` merged in)."""
    if data_source is None:
        return None
    result = {
        'id': data_source.id,
        'project_id': data_source.project_id,
        'form_id': data_source.form_id,
        'title': data_source.title,
        'description': data_source.description,
        'status': data_source.status,
        'source': data_source.source,
        'created_by': data_source.created_by,
        'reviewed_by': data_source.reviewed_by,
        'reviewed_at': _iso(data_source.reviewed_at),
        'rejection_reason': data_source.rejection_reason,
        'ckan_package_id': data_source.ckan_package_id,
        'created': _iso(data_source.created),
        'modified': _iso(data_source.modified),
    }
    extras = _load_json(data_source.extras, {})
    if isinstance(extras, dict):
        for key, value in extras.items():
            result.setdefault(key, value)
    return result


def get_data_source(id):
    """Fetch a ``CsDataSource`` by primary key (None if not found)."""
    _ensure_mappers()
    if not id:
        return None
    return Session.query(CsDataSource).get(id)


def get_data_source_by_form(project_id, form_id):
    """Fetch the (unique) row for ``(project_id, form_id)`` (None if absent)."""
    _ensure_mappers()
    if not project_id or form_id is None:
        return None
    return (
        Session.query(CsDataSource)
        .filter(CsDataSource.project_id == project_id)
        .filter(CsDataSource.form_id == form_id)
        .first()
    )


def list_data_sources(project_id=None, status=None, limit=50, offset=0):
    """List data sources with filtering + paging. Returns ``(total, rows)``."""
    _ensure_mappers()
    query = Session.query(CsDataSource)
    if project_id:
        query = query.filter(CsDataSource.project_id == project_id)
    if status:
        query = query.filter(CsDataSource.status == status)
    total = query.count()
    rows = (
        query.order_by(CsDataSource.created.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return total, rows


def pending_data_sources(limit=20, offset=0):
    """Pending data sources (sysadmin scope only). ``(total, [dict, ...])``.

    Approval is sysadmin-only (like project requests), so unlike joins/content
    there is no project-admin scoping. Rows are decorated with their project
    title/slug for the review tab.
    """
    _ensure_mappers()
    query = (
        Session.query(CsDataSource, CsProject.title, CsProject.slug)
        .outerjoin(CsProject, CsProject.id == CsDataSource.project_id)
        .filter(CsDataSource.status == 'pending')
    )
    total = query.count()
    rows = (
        query.order_by(CsDataSource.created.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    results = []
    for data_source, title, slug in rows:
        item = data_source_dictize(data_source)
        item['project_title'] = title
        item['project_slug'] = slug
        results.append(item)
    return total, results


def _count_pending_data_sources():
    return (
        Session.query(CsDataSource.id)
        .filter(CsDataSource.status == 'pending')
        .count()
    )


def _resolve_user(context):
    """Resolve the acting ``User`` object from a CKAN action context.

    A flask-login ``AnonymousUser`` in ``auth_user_obj`` (anonymous API calls
    on portals with auth plugins) counts as "no user".
    """
    user_obj = context.get('auth_user_obj')
    if user_obj is not None and not getattr(user_obj, 'is_anonymous', False):
        return user_obj
    model = context.get('model')
    username = context.get('user')
    if not username or model is None:
        return None
    return model.User.get(username)


def pending_counts(context):
    """At-a-glance pending counts for the acting user (role-aware, cheap).

    Sysadmins see every pending project / join / content; a project-admin sees
    only pending joins + content for THEIR projects (and never project requests);
    everyone else sees zeros. All three counts are COUNT(*) queries -- this is the
    single source used by both the admin panel and the header-badge helper so the
    numbers are always identical.
    """
    _ensure_mappers()
    user_obj = _resolve_user(context)
    zero = {'project_requests': 0, 'join_requests': 0,
            'content_requests': 0, 'data_requests': 0, 'total': 0}
    if user_obj is None:
        return dict(zero)

    if getattr(user_obj, 'sysadmin', False):
        projects = _count_pending_projects()
        joins = _count_pending_joins(None)
        content = _count_pending_content(None)
        # Data-source approval is sysadmin-only, so only sysadmins ever see it.
        data_sources = _count_pending_data_sources()
    else:
        project_ids = admin_project_ids(user_obj.id)
        if not project_ids:
            return dict(zero)
        projects = 0
        joins = _count_pending_joins(project_ids)
        content = _count_pending_content(project_ids)
        data_sources = 0

    return {
        'project_requests': projects,
        'join_requests': joins,
        'content_requests': content,
        'data_requests': data_sources,
        'total': projects + joins + content + data_sources,
    }
