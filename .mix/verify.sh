#!/usr/bin/env bash
#
# ckanext-csunesco -- increment 1 verification (SYNTAX-LEVEL ONLY).
#
# HARD CONSTRAINT: CKAN is NOT installed in this environment, so this script
# MUST NOT import ckan or run pytest-ckan. Verification is limited to:
#   * bash syntax (implicitly, via `set -e` + running under bash),
#   * python byte-compilation (py_compile / compileall),
#   * AST assertions on setup.py and plugin.py,
#   * structural checks that required files/dirs exist.
#
# Usage (from the repo root):
#   bash .mix/verify.sh
#
set -euo pipefail

# Resolve the repo root as the parent of this script's directory, then cd there
# so the script works regardless of the caller's current directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PY="${PYTHON:-python3}"
if ! command -v "${PY}" >/dev/null 2>&1; then
  PY=python
fi

echo "== ckanext-csunesco verify (syntax-level, no CKAN) =="
echo "-- repo root: ${REPO_ROOT}"

# (c) Byte-compile everything -- fails on any SyntaxError.
echo "-- python -m compileall"
"${PY}" -m compileall -q setup.py ckanext

# (d) AST checks: entry point in setup.py + CsunescoPlugin class in plugin.py.
echo "-- AST checks (entry point + plugin class)"
"${PY}" - <<'PYEOF'
import ast
import sys

ENTRY_POINT = 'csunesco=ckanext.csunesco.plugin:CsunescoPlugin'

# setup.py must contain the entry-point string.
with open('setup.py', 'r') as fh:
    setup_src = fh.read()
# Parse to confirm it is valid Python (raises on syntax error).
ast.parse(setup_src, filename='setup.py')
if ENTRY_POINT not in setup_src:
    sys.exit('FAIL: entry point %r not found in setup.py' % ENTRY_POINT)

# plugin.py must define a ClassDef named CsunescoPlugin.
plugin_path = 'ckanext/csunesco/plugin.py'
with open(plugin_path, 'r') as fh:
    tree = ast.parse(fh.read(), filename=plugin_path)
classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
if 'CsunescoPlugin' not in classes:
    sys.exit('FAIL: class CsunescoPlugin not defined in %s' % plugin_path)

print('   AST OK: entry point present, CsunescoPlugin defined')
PYEOF

# (d2) Increment 2 AST checks: registration view + seed-initiatives command.
echo "-- AST checks (increment 2: register_citizen + seed-initiatives)"
"${PY}" - <<'PYEOF'
import ast
import sys

# blueprint.py must define a `register_citizen` view function.
bp_path = 'ckanext/csunesco/blueprint.py'
with open(bp_path, 'r') as fh:
    bp_tree = ast.parse(fh.read(), filename=bp_path)
bp_funcs = {n.name for n in ast.walk(bp_tree) if isinstance(n, ast.FunctionDef)}
if 'register_citizen' not in bp_funcs:
    sys.exit('FAIL: function register_citizen not defined in %s' % bp_path)

# cli.py must define the `seed-initiatives` click command (function
# seed_initiatives decorated with @csunesco.command('seed-initiatives')).
cli_path = 'ckanext/csunesco/cli.py'
with open(cli_path, 'r') as fh:
    cli_src = fh.read()
cli_tree = ast.parse(cli_src, filename=cli_path)
cli_funcs = {n.name for n in ast.walk(cli_tree) if isinstance(n, ast.FunctionDef)}
if 'seed_initiatives' not in cli_funcs:
    sys.exit('FAIL: function seed_initiatives not defined in %s' % cli_path)
if 'seed-initiatives' not in cli_src:
    sys.exit("FAIL: 'seed-initiatives' command name missing in %s" % cli_path)

print('   AST OK: register_citizen view + seed-initiatives command present')
PYEOF

# (d3) Increment 3 AST checks: action package + auth + validators + IValidators.
echo "-- AST checks (increment 3: actions/auth/validators)"
"${PY}" - <<'PYEOF'
import ast
import sys

ACTION_FILES = [
    'ckanext/csunesco/logic/action/__init__.py',
    'ckanext/csunesco/logic/action/projects.py',
    'ckanext/csunesco/logic/action/members.py',
]
OTHER_FILES = [
    'ckanext/csunesco/logic/actions.py',
    'ckanext/csunesco/logic/auth.py',
    'ckanext/csunesco/logic/validators.py',
]

# AST-parse every module (raises on syntax error) and gather the action source.
action_src = ''
for path in ACTION_FILES:
    with open(path, 'r') as fh:
        src = fh.read()
    ast.parse(src, filename=path)
    action_src += src
for path in OTHER_FILES:
    with open(path, 'r') as fh:
        ast.parse(fh.read(), filename=path)

# Every csunesco_* action-name string literal must appear across the modules.
ACTION_NAMES = [
    'csunesco_project_request_create',
    'csunesco_project_approve',
    'csunesco_project_reject',
    'csunesco_project_list',
    'csunesco_project_show',
    'csunesco_project_stats_show',
    'csunesco_join_request_create',
    'csunesco_join_approve',
    'csunesco_join_reject',
]
for name in ACTION_NAMES:
    if name not in action_src:
        sys.exit('FAIL: action name %r not found in action modules' % name)

# validators.py must define get_validators.
with open('ckanext/csunesco/logic/validators.py', 'r') as fh:
    vtree = ast.parse(fh.read())
vfuncs = {n.name for n in ast.walk(vtree) if isinstance(n, ast.FunctionDef)}
if 'get_validators' not in vfuncs:
    sys.exit('FAIL: get_validators not defined in validators.py')

# auth.py must define get_auth_functions.
with open('ckanext/csunesco/logic/auth.py', 'r') as fh:
    atree = ast.parse(fh.read())
afuncs = {n.name for n in ast.walk(atree) if isinstance(n, ast.FunctionDef)}
if 'get_auth_functions' not in afuncs:
    sys.exit('FAIL: get_auth_functions not defined in auth.py')

# plugin.py source must reference IValidators.
with open('ckanext/csunesco/plugin.py', 'r') as fh:
    plugin_src = fh.read()
if 'IValidators' not in plugin_src:
    sys.exit("FAIL: 'IValidators' not referenced in plugin.py")

print('   AST OK: action modules, csunesco_* names, validators/auth, IValidators')
PYEOF

# (d4) Increment 4 AST checks: public presentation layer (views + blueprint
# endpoints + aggregate action + helpers wiring).
echo "-- AST checks (increment 4: views/blueprint/helpers/aggregate action)"
"${PY}" - <<'PYEOF'
import ast
import sys

# blueprint.py must define every public view endpoint function.
bp_path = 'ckanext/csunesco/blueprint.py'
with open(bp_path, 'r') as fh:
    bp_tree = ast.parse(fh.read(), filename=bp_path)
bp_funcs = {n.name for n in ast.walk(bp_tree) if isinstance(n, ast.FunctionDef)}
if not ({'hub', 'index'} & bp_funcs):
    sys.exit("FAIL: blueprint.py defines neither 'hub' nor 'index'")
for name in ('initiative_index', 'project_list', 'project_landing',
             'project_geojson', 'project_new', 'join_project'):
    if name not in bp_funcs:
        sys.exit('FAIL: function %r not defined in %s' % (name, bp_path))

# logic/views.py must AST-parse and define the orchestration functions.
views_path = 'ckanext/csunesco/logic/views.py'
with open(views_path, 'r') as fh:
    views_tree = ast.parse(fh.read(), filename=views_path)
views_funcs = {n.name for n in ast.walk(views_tree)
               if isinstance(n, ast.FunctionDef)}
for name in ('hub', 'initiative_index', 'project_list', 'project_landing',
             'project_geojson', 'project_new', 'join_project'):
    if name not in views_funcs:
        sys.exit('FAIL: function %r not defined in %s' % (name, views_path))

# logic/helpers.py must AST-parse and define the presentation helpers.
helpers_path = 'ckanext/csunesco/logic/helpers.py'
with open(helpers_path, 'r') as fh:
    helpers_tree = ast.parse(fh.read(), filename=helpers_path)
helpers_funcs = {n.name for n in ast.walk(helpers_tree)
                 if isinstance(n, ast.FunctionDef)}
for name in ('csunesco_initiatives', 'csunesco_aggregate_stats',
             'csunesco_project_url', 'csunesco_join_url',
             'csunesco_qr_data_uri', 'csunesco_member_state_title'):
    if name not in helpers_funcs:
        sys.exit('FAIL: helper %r not defined in %s' % (name, helpers_path))

# The aggregate action literal must appear in the action layer.
proj_path = 'ckanext/csunesco/logic/action/projects.py'
with open(proj_path, 'r') as fh:
    proj_src = fh.read()
ast.parse(proj_src, filename=proj_path)
if 'csunesco_aggregate_stats' not in proj_src:
    sys.exit("FAIL: 'csunesco_aggregate_stats' not found in %s" % proj_path)

# db.py must define the single-query aggregate_stats helper.
db_path = 'ckanext/csunesco/db.py'
with open(db_path, 'r') as fh:
    db_tree = ast.parse(fh.read(), filename=db_path)
db_funcs = {n.name for n in ast.walk(db_tree) if isinstance(n, ast.FunctionDef)}
if 'aggregate_stats' not in db_funcs:
    sys.exit('FAIL: aggregate_stats not defined in %s' % db_path)

# plugin.py get_helpers must reference the helpers module.
with open('ckanext/csunesco/plugin.py', 'r') as fh:
    plugin_src = fh.read()
if 'import helpers' not in plugin_src or 'helpers.' not in plugin_src:
    sys.exit("FAIL: plugin.py get_helpers does not reference the helpers module")

print('   AST OK: views + blueprint endpoints + aggregate action + helpers')
PYEOF

# (d5) Increment 5 AST checks: admin approval panel + news/events content.
echo "-- AST checks (increment 5: admin panel + content actions/views)"
"${PY}" - <<'PYEOF'
import ast
import sys

# New action + view modules must AST-parse; gather the action source for the
# action-name literal assertions below.
NEW_ACTION_FILES = [
    'ckanext/csunesco/logic/action/admin.py',
    'ckanext/csunesco/logic/action/content.py',
]
OTHER_NEW_FILES = [
    'ckanext/csunesco/logic/views_admin.py',
    'ckanext/csunesco/logic/views_content.py',
    'ckanext/csunesco/logic/sanitize.py',
]
action_src = ''
for path in NEW_ACTION_FILES:
    with open(path, 'r') as fh:
        src = fh.read()
    ast.parse(src, filename=path)
    action_src += src
for path in OTHER_NEW_FILES:
    with open(path, 'r') as fh:
        ast.parse(fh.read(), filename=path)

# The increment-5 action-name literals must appear across the action modules.
for name in ('csunesco_admin_pending_list', 'csunesco_content_create',
             'csunesco_content_list', 'csunesco_content_approve'):
    if name not in action_src:
        sys.exit('FAIL: action name %r not found in action modules' % name)

# blueprint.py must define the new endpoint functions.
bp_path = 'ckanext/csunesco/blueprint.py'
with open(bp_path, 'r') as fh:
    bp_tree = ast.parse(fh.read(), filename=bp_path)
bp_funcs = {n.name for n in ast.walk(bp_tree) if isinstance(n, ast.FunctionDef)}
for name in ('admin_dashboard', 'cs_news_index', 'cs_events_index',
             'content_new', 'content_edit'):
    if name not in bp_funcs:
        sys.exit('FAIL: function %r not defined in %s' % (name, bp_path))

# The content schema builder must exist.
with open('ckanext/csunesco/logic/schema.py', 'r') as fh:
    sch_tree = ast.parse(fh.read())
sch_funcs = {n.name for n in ast.walk(sch_tree) if isinstance(n, ast.FunctionDef)}
if 'content_schema' not in sch_funcs:
    sys.exit('FAIL: content_schema not defined in logic/schema.py')

# db.py must gain the content + admin helpers.
with open('ckanext/csunesco/db.py', 'r') as fh:
    db_tree = ast.parse(fh.read())
db_funcs = {n.name for n in ast.walk(db_tree) if isinstance(n, ast.FunctionDef)}
for name in ('content_dictize', 'get_content', 'unique_content_slug',
             'list_content', 'admin_project_ids', 'pending_counts'):
    if name not in db_funcs:
        sys.exit('FAIL: %r not defined in db.py' % name)

print('   AST OK: admin panel + content actions/views/schema/db helpers')
PYEOF

# (d6) Increment 9 AST checks: server-to-server CS registration action.
echo "-- AST checks (increment 9: register_citizen_scientist action + registry)"
"${PY}" - <<'PYEOF'
import ast
import sys

# The new action module must AST-parse and define the action + get_actions.
reg_path = 'ckanext/csunesco/logic/action/registration.py'
with open(reg_path, 'r') as fh:
    reg_src = fh.read()
reg_tree = ast.parse(reg_src, filename=reg_path)
reg_funcs = {n.name for n in ast.walk(reg_tree) if isinstance(n, ast.FunctionDef)}
if 'csunesco_register_citizen_scientist' not in reg_funcs:
    sys.exit('FAIL: csunesco_register_citizen_scientist not defined in %s' % reg_path)
if 'get_actions' not in reg_funcs:
    sys.exit('FAIL: get_actions not defined in %s' % reg_path)
# The action-name literal must be present in this module's registry dict.
if "'csunesco_register_citizen_scientist'" not in reg_src:
    sys.exit("FAIL: action name literal missing from get_actions in %s" % reg_path)

# logic/registration.py must expose the reusable create_citizen_scientist core.
core_path = 'ckanext/csunesco/logic/registration.py'
with open(core_path, 'r') as fh:
    core_tree = ast.parse(fh.read(), filename=core_path)
core_funcs = {n.name for n in ast.walk(core_tree) if isinstance(n, ast.FunctionDef)}
if 'create_citizen_scientist' not in core_funcs:
    sys.exit('FAIL: create_citizen_scientist not defined in %s' % core_path)

# The aggregator must merge the registration module into the actions registry.
actions_path = 'ckanext/csunesco/logic/actions.py'
with open(actions_path, 'r') as fh:
    actions_src = fh.read()
ast.parse(actions_src, filename=actions_path)
if 'registration' not in actions_src or 'registration.get_actions()' not in actions_src:
    sys.exit("FAIL: registration.get_actions() not merged in %s" % actions_path)

# The sysadmin-only auth function must gate the action.
auth_path = 'ckanext/csunesco/logic/auth.py'
with open(auth_path, 'r') as fh:
    auth_src = fh.read()
ast.parse(auth_src, filename=auth_path)
if "'csunesco_register_citizen_scientist'" not in auth_src:
    sys.exit("FAIL: csunesco_register_citizen_scientist auth missing in %s" % auth_path)

print('   AST OK: register_citizen_scientist action defined + in registry + auth')
PYEOF

# (d7) Increment 11 checks: behavioral test files parse + nav header wiring.
echo "-- checks (increment 11: behavioral tests + header nav wiring)"
"${PY}" - <<'PYEOF'
import ast
import sys

# Both new behavioral test modules must AST-parse (they run inside the
# ckan-dev container; here we only assert they are syntactically valid).
for path in ('ckanext/csunesco/tests/test_db_behavior.py',
             'ckanext/csunesco/tests/test_pure_logic.py'):
    with open(path, 'r') as fh:
        ast.parse(fh.read(), filename=path)

# header.html must extend CKAN's header and override the main-nav tabs block
# with a Citizen Science entry point.
header_path = 'ckanext/csunesco/templates/header.html'
with open(header_path, 'r') as fh:
    header_src = fh.read()
for needle in ('{% ckan_extends %}',
               'header_site_navigation_tabs',
               "h.url_for('csunesco.index')"):
    if needle not in header_src:
        sys.exit('FAIL: %r missing from %s' % (needle, header_path))

print('   OK: behavioral test files parse + header nav wiring present')
PYEOF

# (d8) Project-page blocks: the registry + the aggregation must stay CKAN-FREE.
# That is what lets their unit tests run here, outside the container.
echo "-- AST checks (project pages: blocks/aggregate stay CKAN-free)"
"${PY}" - <<'PYEOF'
import ast
import sys

PURE = {
    'ckanext/csunesco/logic/blocks.py': (
        'normalize_block', 'normalize_blocks', 'blocks_from_form', 'apply_op',
        'page_initial_status', 'blocks_requiring_review', 'default_blocks',
        'blocks_from_json', 'blocks_to_json', 'parse_video', 'ensure_builtins'),
    'ckanext/csunesco/logic/aggregate.py': (
        'to_number', 'category_value', 'robust_range', 'detect_site_field',
        'numeric_fields_with_data', 'categorical_field_options', 'value_counts',
        'parse_iso_day', 'choose_bucket', 'bucket_key', 'bucket_labels',
        'filter_rows_by_date', 'aggregate_numeric', 'aggregate_counts',
        'aggregate_categories', 'aggregate_scalar', 'round_series',
        'preset_start'),
    'ckanext/csunesco/logic/chat.py': (
        'parse_unit', 'build_profile', 'numeric_names', 'groupable_names',
        'has_data', 'validate_tool_call', 'build_messages', 'clamp_question',
        'clamp_history',
        'suggestions_from_profile', 'answer_card', 'result_is_empty'),
}

for path, required in PURE.items():
    with open(path, 'r') as fh:
        tree = ast.parse(fh.read(), filename=path)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in required:
        if name not in funcs:
            sys.exit('FAIL: %r not defined in %s' % (name, path))
    # No CKAN import, direct or deferred: these modules are the ones a
    # contributor can unit-test without a container, and that only holds while
    # nothing in their import graph reaches ckan.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or '']
        else:
            continue
        for name in names:
            if name == 'ckan' or name.startswith('ckan.'):
                sys.exit('FAIL: %s imports CKAN (%s) -- it must stay pure'
                         % (path, name))

# sanitize.py must expose BOTH allowlists as separate functions (a single
# parameterised sanitizer invites passing the wide list to the news path).
san_path = 'ckanext/csunesco/logic/sanitize.py'
with open(san_path, 'r') as fh:
    san_tree = ast.parse(fh.read(), filename=san_path)
san_funcs = {n.name for n in ast.walk(san_tree) if isinstance(n, ast.FunctionDef)}
for name in ('sanitize_html', 'sanitize_page_html'):
    if name not in san_funcs:
        sys.exit('FAIL: %r not defined in %s' % (name, san_path))

print('   AST OK: blocks/aggregate present and CKAN-free; two sanitizers')
PYEOF

# (d8b) Every block type must have BOTH a render snippet and (unless it is a
# built-in section) an editor snippet. A registry entry with no template is a
# 500 on a public page; a template with no entry is dead code.
echo "-- checks (project pages: registry <-> templates)"
"${PY}" - <<'PYEOF'
import ast
import os
import re
import sys

# Read the registry with AST, not a regex: the keyword arguments wrap across
# lines and the descriptions contain parentheses, so any "match up to the
# closing paren" pattern silently mis-reads which entries are built-ins.
tree = ast.parse(open('ckanext/csunesco/logic/blocks.py').read())
keys = set()
builtin = set()
for node in ast.walk(tree):
    if not (isinstance(node, ast.Call)
            and getattr(node.func, 'id', None) == 'BlockType'):
        continue
    if not node.args or not isinstance(node.args[0], ast.Constant):
        sys.exit('FAIL: a BlockType() call has no literal key')
    key = node.args[0].value
    keys.add(key)
    for keyword in node.keywords:
        if keyword.arg == 'builtin' and getattr(keyword.value, 'value', False):
            builtin.add(key)
if not keys:
    sys.exit('FAIL: no BlockType entries found in blocks.py')

render_dir = 'ckanext/csunesco/templates/csunesco/blocks'
edit_dir = os.path.join(render_dir, 'edit')
rendered = {f[:-5] for f in os.listdir(render_dir) if f.endswith('.html')}
edited = {f[:-5] for f in os.listdir(edit_dir)
          if f.endswith('.html') and not f.startswith('_')}

missing_render = sorted(keys - rendered)
if missing_render:
    sys.exit('FAIL: block types with no render snippet: %s' % missing_render)
missing_edit = sorted((keys - builtin) - edited)
if missing_edit:
    sys.exit('FAIL: block types with no editor snippet: %s' % missing_edit)
orphan_edit = sorted(edited - keys)
if orphan_edit:
    sys.exit('FAIL: editor snippets with no block type: %s' % orphan_edit)

# The five queues must line up across db, the action and the view fallback.
for path, needle in (
        ('ckanext/csunesco/db.py', "'page_requests'"),
        ('ckanext/csunesco/logic/action/admin.py', "'page_requests'"),
        ('ckanext/csunesco/logic/views_admin.py', "'page_requests'"),
        ('ckanext/csunesco/templates/csunesco/cs-admin-dashboard.html',
         'cs-tabbtn-pages')):
    if needle not in open(path).read():
        sys.exit('FAIL: %r missing from %s (pending-count wiring)' %
                 (needle, path))

# Newstyle gettext binds its variables as KEYWORDS. A later "% {...}" reaches
# a Markup object whose __mod__ never sees the mapping, so it raises KeyError
# at render time -- invisible until that exact template runs. The repo has been
# bitten by this before (commit 43fa6d3); keep it from coming back.
#
# The same trap has a second face: newstyle gettext applies `% variables` to
# what _() returns ALWAYS, even with no variables at all. So a % INSIDE the
# literal is a runtime error on its own -- _("%(n)s items") raises KeyError and
# _("100% done") raises ValueError, both only when that template renders. A
# literal percent has to be written %%, and a placeholder either gets kwargs on
# the same call or is written {braces} and substituted downstream.
bad = []
percent = []
for root, _dirs, files in os.walk('ckanext/csunesco/templates'):
    for name in files:
        if not name.endswith('.html'):
            continue
        path = os.path.join(root, name)
        for number, line in enumerate(open(path), 1):
            if re.search(r'_\(".*?"\)\s*%', line):
                bad.append('%s:%d' % (path, number))
            for literal in re.findall(r'_\((".*?")\s*(\)|,)', line):
                text, closer = literal
                if '%' not in text.replace('%%', ''):
                    continue
                # A `,` means kwargs follow, which is the supported form.
                if closer == ',':
                    continue
                percent.append('%s:%d' % (path, number))
if bad:
    sys.exit('FAIL: newstyle gettext needs _("...", var=x), not a later %%: %s'
             % ', '.join(bad))
if percent:
    sys.exit('FAIL: a %% inside _("...") with no kwargs raises at render time; '
             'escape it as %%%% or use {braces}: %s' % ', '.join(percent))

# The op carriers must stay on SEPARATE names. Sharing one made the server's
# answer depend on DOM order -- and it was wrong: a hidden `op` before the
# buttons made MultiDict.get return "save", so with JavaScript disabled every
# button in the editor became "Draft saved", publish included.
editor = open('ckanext/csunesco/templates/csunesco/project_page_form.html').read()
if re.search(r'<input[^>]*\bname="op"', editor):
    sys.exit('FAIL: `op` must be carried ONLY by submit buttons; a hidden input '
             'of the same name makes MultiDict.get return it instead')
if editor.count('name="op_js"') != 1:
    sys.exit('FAIL: expected exactly one op_js carrier in the editor')

print('   OK: %d block types, all with templates; page queue wired everywhere'
      % len(keys))
PYEOF

# (d8b2) Every route must be REACHABLE. A view nobody links to is a URL you
# have to remember, which is how /citizen-science/admin ended up invisible the
# moment a reviewer's queue hit zero (the link was gated on the count, not on
# the role). Any endpoint not referenced from a template or a view has to be
# listed below with the reason it is reached another way.
echo "-- checks (every blueprint endpoint is linked from somewhere)"
"${PY}" - <<'PYEOF'
import collections
import os
import re
import sys

# Reached without a literal 'csunesco.<endpoint>' anywhere:
REACHED_OTHERWISE = {
    # builtin_region_map.html builds it by concatenation:
    # h.csunesco_project_url(slug) + "/geojson".
    'project_geojson',
}

blueprint = open('ckanext/csunesco/blueprint.py').read()
# Quote-agnostic on purpose: a rule written with double quotes must not slip
# past the audit just because the rest of the file happens to use single ones.
endpoints = set(re.findall(
    r"""add_url_rule\(\s*['"][^'"]+['"],\s*['"]([^'"]+)['"]""", blueprint))
if not endpoints:
    sys.exit('FAIL: no endpoints found in blueprint.py')

referenced = collections.defaultdict(list)
for base in ('ckanext/csunesco/templates', 'ckanext/csunesco/logic'):
    for directory, subdirs, files in os.walk(base):
        # Byte-compiled caches are not source and are not UTF-8.
        subdirs[:] = [d for d in subdirs if d != '__pycache__']
        for name in files:
            if not name.endswith(('.html', '.py')):
                continue
            path = os.path.join(directory, name)
            with open(path, 'r') as handle:
                text = handle.read()
            for endpoint in endpoints:
                if re.search(r"""csunesco\.%s['"]""" % re.escape(endpoint),
                             text):
                    referenced[endpoint].append(path)

orphans = sorted(endpoints - set(referenced) - REACHED_OTHERWISE)
if orphans:
    sys.exit('FAIL: blueprint endpoints nothing links to: %s\n'
             '      Link them, or add them to REACHED_OTHERWISE with a reason.'
             % ', '.join(orphans))

stale = sorted(REACHED_OTHERWISE - endpoints)
if stale:
    sys.exit('FAIL: REACHED_OTHERWISE names endpoints that no longer exist: %s'
             % ', '.join(stale))

# The approval panel is the one page with no other entry point, so its link
# must be gated on the ROLE, never on "is anything pending".
header = open('ckanext/csunesco/templates/header.html').read()
if 'admin_dashboard' not in header:
    sys.exit('FAIL: header.html no longer links the approval dashboard')
if not re.search(r"check_access\('csunesco_admin_pending_list'\)", header):
    sys.exit("FAIL: the Review link must be gated on "
             "h.check_access('csunesco_admin_pending_list'), not on the pending "
             "count -- otherwise the panel disappears when the queue is empty")

print('   OK: %d endpoints, all linked (%d reached another way)'
      % (len(endpoints), len(REACHED_OTHERWISE)))
PYEOF

# (d8c) Every template must PARSE. CKAN's own tags are stubbed so this needs
# nothing but jinja2 -- and it catches the class of error the other checks
# cannot see, e.g. a {# comment #} inside a {% set %} expression, which is a
# 500 on a public page rather than a failing import.
if "${PY}" -c "import jinja2" >/dev/null 2>&1; then
  echo "-- jinja parse (every template, CKAN tags stubbed)"
  "${PY}" - <<'PYEOF'
import os
import sys

import jinja2
from jinja2 import nodes
from jinja2.ext import Extension


class _StubTag(Extension):
    """Parse-and-discard stand-in for a CKAN custom tag."""

    tags = set()

    def parse(self, parser):
        token = next(parser.stream)
        while parser.stream.current.type != 'block_end':
            next(parser.stream)
        return nodes.Output([nodes.Const('')]).set_lineno(token.lineno)


class _Snippet(_StubTag):
    tags = {'snippet'}


class _Asset(_StubTag):
    tags = {'asset'}


class _CkanExtends(_StubTag):
    tags = {'ckan_extends'}


class _Url(_StubTag):
    tags = {'url'}


ROOT = 'ckanext/csunesco/templates'
env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(ROOT),
    extensions=[_Snippet, _Asset, _CkanExtends, _Url, 'jinja2.ext.i18n'],
    autoescape=True)
env.install_null_translations(newstyle=True)
# Provided by Flask at runtime, not by bare jinja2.
env.filters['tojson'] = lambda value, **kwargs: '{}'

failures = []
count = 0
for directory, _subdirs, files in os.walk(ROOT):
    for name in files:
        if not name.endswith('.html'):
            continue
        path = os.path.join(directory, name)
        count += 1
        with open(path, 'r') as handle:
            source = handle.read()
        try:
            env.parse(source, filename=os.path.relpath(path, ROOT))
        except jinja2.TemplateSyntaxError as error:
            failures.append('%s:%s %s' % (path, error.lineno, error.message))

if failures:
    sys.exit('FAIL: template syntax errors:\n  ' + '\n  '.join(failures))
print('   OK: %d templates parse' % count)
PYEOF
else
  echo "-- jinja2 not installed: skipping the template parse check"
fi

# (d9) Run the CKAN-free unit tests here when pytest is available. These are
# the only tests that do NOT need the container, so running them in the fast
# loop is free signal.
if "${PY}" -c "import pytest" >/dev/null 2>&1; then
  echo "-- pytest (CKAN-free: test_blocks.py + test_aggregate.py + test_chat.py)"
  "${PY}" -m pytest -q -p no:cacheprovider \
    ckanext/csunesco/tests/test_blocks.py \
    ckanext/csunesco/tests/test_aggregate.py \
    ckanext/csunesco/tests/test_chat.py
else
  echo "-- pytest not installed: skipping the CKAN-free unit tests"
fi

# (e) Structural checks: required files/dirs must exist.
echo "-- structural checks (required files)"
REQUIRED_FILES=(
  "ckanext/csunesco/plugin.py"
  "ckanext/csunesco/db.py"
  "ckanext/csunesco/blueprint.py"
  "ckanext/csunesco/cli.py"
  "ckanext/csunesco/constants.py"
  "ckanext/csunesco/logic/registration.py"
  "ckanext/csunesco/logic/views.py"
  "ckanext/csunesco/logic/helpers.py"
  "ckanext/csunesco/logic/actions.py"
  "ckanext/csunesco/logic/auth.py"
  "ckanext/csunesco/logic/validators.py"
  "ckanext/csunesco/logic/schema.py"
  "ckanext/csunesco/logic/action/__init__.py"
  "ckanext/csunesco/logic/action/projects.py"
  "ckanext/csunesco/logic/action/members.py"
  "ckanext/csunesco/logic/action/admin.py"
  "ckanext/csunesco/logic/action/content.py"
  "ckanext/csunesco/logic/action/registration.py"
  "ckanext/csunesco/logic/views_admin.py"
  "ckanext/csunesco/logic/views_content.py"
  "ckanext/csunesco/logic/sanitize.py"
  "ckanext/csunesco/logic/blocks.py"
  "ckanext/csunesco/logic/page_render.py"
  "ckanext/csunesco/logic/views_page.py"
  "ckanext/csunesco/logic/action/page.py"
  "ckanext/csunesco/templates/csunesco/project_page_form.html"
  "ckanext/csunesco/templates/csunesco/snippets/block_render.html"
  "ckanext/csunesco/assets/js/cs-charts.js"
  "ckanext/csunesco/assets/js/cs-page-editor.js"
  "ckanext/csunesco/assets/js/cs-page-view.js"
  "ckanext/csunesco/assets/vendor/chart.umd.min.js"
  "ckanext/csunesco/assets/vendor/LICENSE-chartjs.txt"
  "ckanext/csunesco/logic/aggregate.py"
  "ckanext/csunesco/logic/chat.py"
  "ckanext/csunesco/logic/llm.py"
  "ckanext/csunesco/logic/action/chat.py"
  "ckanext/csunesco/templates/csunesco/blocks/data_chat.html"
  "ckanext/csunesco/templates/csunesco/blocks/edit/data_chat.html"
  "ckanext/csunesco/assets/js/cs-data-chat.js"
  "ckanext/csunesco/tests/test_blocks.py"
  "ckanext/csunesco/tests/test_aggregate.py"
  "ckanext/csunesco/tests/test_chat.py"
  "ckanext/csunesco/tests/test_data_chat.py"
  "ckanext/csunesco/tests/fixtures/ofform_form3.json"
  "ckanext/csunesco/templates/csunesco/citizen-science.html"
  "ckanext/csunesco/templates/csunesco/register_citizen.html"
  "ckanext/csunesco/templates/csunesco/initiative.html"
  "ckanext/csunesco/templates/csunesco/project_list.html"
  "ckanext/csunesco/templates/csunesco/project_landing.html"
  "ckanext/csunesco/templates/csunesco/project_request.html"
  "ckanext/csunesco/templates/csunesco/cs-admin-dashboard.html"
  "ckanext/csunesco/templates/csunesco/cs-news_list.html"
  "ckanext/csunesco/templates/csunesco/cs-news.html"
  "ckanext/csunesco/templates/csunesco/cs-events_list.html"
  "ckanext/csunesco/templates/csunesco/cs-events.html"
  "ckanext/csunesco/templates/csunesco/content_form.html"
  "ckanext/csunesco/templates/header.html"
  "ckanext/csunesco/tests/test_db_behavior.py"
  "ckanext/csunesco/tests/test_pure_logic.py"
  "ckanext/csunesco/assets/js/cs-map.js"
  "ckanext/csunesco/assets/webassets.yml"
  "Dockerfile.test"
  "docker-compose.dev.yml"
  "INSTALL.md"
  "docs/OFFORM_INTEGRATION.md"
  "scripts/run-ckan-tests.sh"
)
for f in "${REQUIRED_FILES[@]}"; do
  if [[ ! -f "${f}" ]]; then
    echo "FAIL: required file missing: ${f}" >&2
    exit 1
  fi
done

# (f) Increment 12 checks: the deployment/verification handoff files are wired.
echo "-- checks (increment 12: deploy/verify handoff)"
"${PY}" - <<'PYEOF'
import sys

# Dockerfile.test must be the ckan-dev harness that COPYs + editable-installs.
with open('Dockerfile.test', 'r') as fh:
    docker_src = fh.read()
for needle in ('FROM ckan/ckan-dev:2.10', 'USER root',
               'COPY . /plugin', 'pip install -e /plugin'):
    if needle not in docker_src:
        sys.exit('FAIL: %r missing from Dockerfile.test' % needle)

# The test driver must build the image and run BOTH the smoke check and pytest.
with open('scripts/run-ckan-tests.sh', 'r') as fh:
    runner_src = fh.read()
for needle in ('docker build -f Dockerfile.test', 'PLUGIN OK',
               'test_db_behavior.py', 'test_pure_logic.py', '-p no:ckan'):
    if needle not in runner_src:
        sys.exit('FAIL: %r missing from scripts/run-ckan-tests.sh' % needle)

# The dev stack must enable csunesco and wire the four CKAN dev services.
with open('docker-compose.dev.yml', 'r') as fh:
    compose_src = fh.read()
for needle in ('ckan/ckan-dev:2.10', 'ckan/ckan-postgres-dev:2.10',
               'ckan/ckan-solr:2.10-solr9', 'redis:7',
               'csunesco', 'CKAN_SITE_URL'):
    if needle not in compose_src:
        sys.exit('FAIL: %r missing from docker-compose.dev.yml' % needle)

print('   OK: Dockerfile.test + run-ckan-tests.sh + docker-compose.dev.yml wired')
PYEOF

echo "VERIFY OK"
