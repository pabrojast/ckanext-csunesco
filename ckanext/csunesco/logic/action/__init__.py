# encoding: utf-8
"""CS domain action package.

The action layer is split by aggregate -- ``projects`` (request / approve /
reject / list / show / stats) and ``members`` (join request / approve / reject).
``logic/actions.py`` stays a thin aggregator that merges both ``get_actions``
dicts. Small helpers shared by both submodules live here so neither has to reach
into the other.
"""
import ckan.model as model


def current_user_id(context):
    """Resolve the id of the user in ``context`` (None when anonymous).

    Prefers the already-loaded ``auth_user_obj`` and only falls back to a lookup
    by username, mirroring how core CKAN actions resolve the acting user.
    """
    # Anonymous API calls may carry a flask-login AnonymousUser here (no
    # ``id`` attribute) on portals like IHP-WINS -- treat it as "no user".
    user_obj = context.get('auth_user_obj')
    if user_obj is not None and not getattr(user_obj, 'is_anonymous', False):
        return getattr(user_obj, 'id', None)
    username = context.get('user')
    if not username:
        return None
    user = model.User.get(username)
    return user.id if user else None
