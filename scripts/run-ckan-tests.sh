#!/usr/bin/env bash
#
# ckanext-csunesco -- reproducible CKAN 2.10 verification driver.
#
# Builds the Dockerfile.test image (real CKAN 2.10 with the plugin installed)
# and runs BOTH verification layers inside it:
#   1. a plugin-LOAD smoke check -- instantiate CsunescoPlugin and assert its
#      IActions / ITemplateHelpers / IValidators registries are non-empty,
#   2. the BEHAVIORAL pytest files (test_db_behavior.py + test_pure_logic.py).
#
# Prints a clear PASS/FAIL summary and exits non-zero on the first failure.
#
# Usage (from the repo root, requires Docker):
#   bash scripts/run-ckan-tests.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

IMAGE="csunesco-test"

echo "== ckanext-csunesco: CKAN 2.10 verification harness =="

# --------------------------------------------------------------------------- #
# 1. Build the test image (bakes the plugin into ckan/ckan-dev:2.10).         #
# --------------------------------------------------------------------------- #
echo "-- docker build -f Dockerfile.test -t ${IMAGE} ."
if ! docker build -f Dockerfile.test -t "${IMAGE}" .; then
  echo "FAIL: docker build failed"
  exit 1
fi

# --------------------------------------------------------------------------- #
# 2. Plugin-load smoke check: CsunescoPlugin instantiates + hooks non-empty.  #
# --------------------------------------------------------------------------- #
echo "-- plugin-load smoke check (CsunescoPlugin hooks)"
if ! docker run --rm -i "${IMAGE}" python - <<'PY'
import sys

from ckanext.csunesco.plugin import CsunescoPlugin

plugin = CsunescoPlugin()

actions = plugin.get_actions()
helpers = plugin.get_helpers()
validators = plugin.get_validators()
auth = plugin.get_auth_functions()

assert actions, "get_actions() returned empty"
assert helpers, "get_helpers() returned empty"
assert validators, "get_validators() returned empty"
assert auth, "get_auth_functions() returned empty"

# The server-to-server + landing surface must be wired.
for name in ("csunesco_project_request_create", "csunesco_join_approve",
             "csunesco_content_create", "csunesco_register_citizen_scientist",
             "csunesco_project_show", "csunesco_content_list"):
    assert name in actions, "missing action %r" % name

print("actions=%d helpers=%d validators=%d auth=%d"
      % (len(actions), len(helpers), len(validators), len(auth)))
print("PLUGIN OK")
sys.exit(0)
PY
then
  echo "FAIL: plugin-load smoke check failed"
  exit 1
fi

# --------------------------------------------------------------------------- #
# 3. Behavioral pytest files (real CKAN code, plugin DB helpers + pure logic). #
#    -p no:ckan keeps the CKAN pytest plugin from demanding a configured site. #
# --------------------------------------------------------------------------- #
echo "-- behavioral pytest (test_db_behavior.py + test_pure_logic.py + test_initiative_admin.py)"
if ! docker run --rm "${IMAGE}" bash -lc \
  'cd /plugin && python -m pytest ckanext/csunesco/tests/test_db_behavior.py ckanext/csunesco/tests/test_pure_logic.py ckanext/csunesco/tests/test_initiative_admin.py -q -p no:ckan'
then
  echo "FAIL: behavioral pytest failed"
  exit 1
fi

echo
echo "== SUMMARY: PASS (build + PLUGIN OK + behavioral pytest) =="
