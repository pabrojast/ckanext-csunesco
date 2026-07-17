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
