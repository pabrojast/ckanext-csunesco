# encoding: utf-8
"""API actions aggregator for ckanext-csunesco.

The domain is split by aggregate under ``logic/action/`` -- ``projects`` (request
/ approve / reject / list / show / stats), ``members`` (join request / approve /
reject), ``content`` (news/events create / update / approve / reject / list /
show) and ``admin`` (the aggregated approval-panel query). This module stays a
thin aggregator: it merges every ``get_actions`` dict so the plugin's
``IActions`` hook has a single entry point (see .mix/plan.md).
"""
from ckanext.csunesco.logic.action import (
    projects, members, content, admin, registration)


def get_actions():
    actions = {}
    actions.update(projects.get_actions())
    actions.update(members.get_actions())
    actions.update(content.get_actions())
    actions.update(admin.get_actions())
    actions.update(registration.get_actions())
    return actions
