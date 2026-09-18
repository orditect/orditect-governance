#!/usr/bin/env bash
# Run the full test suite, package by package, with a per-suite summary.
# Usage:
#   ./run_tests.sh              # all suites
#   ./run_tests.sh runtime      # substring match on suite names

set -u
cd "$(dirname "$0")"

PATTERN="${1:-}"
FAILED=()
PASSED=()

SUITES=(
    "packages/ordigovernance-api/tests"
    "packages/ordigovernance-runtime/tests"
    "packages/ordigovernance-viewer/tests"
    "packages/ordigovernance-testing/tests"
    "packages/ordigovernance-bridges-langgraph/tests"
    "packages/ordigovernance-bridges-deepagents/tests"
    "packages/ordigovernance-bridges-direct/tests"
    "packages/ordigovernance-gateway/tests"
)

for suite in "${SUITES[@]}"; do
    name="$(basename "$(dirname "$suite")")"
    if [[ -n "$PATTERN" && "$name" != *"$PATTERN"* ]]; then
        continue
    fi
    if [[ ! -d "$suite" ]]; then
        continue
    fi
    echo "================================================================"
    echo ">>> $name"
    echo "================================================================"
    if python -m pytest "$suite" -q; then
        PASSED+=("$name")
    else
        FAILED+=("$name")
    fi
    echo
done

echo "================================================================"
echo ">>> acceptance self-check"
echo "================================================================"
if python -m examples.acceptance.selfcheck; then
    PASSED+=("acceptance-selfcheck")
else
    FAILED+=("acceptance-selfcheck")
fi

echo
echo "================================================================"
echo ">>> import boundary"
echo "================================================================"
if python scripts/check_import_boundary.py; then
    PASSED+=("import-boundary")
else
    FAILED+=("import-boundary")
fi

echo
echo "================================================================"
echo "SUMMARY"
echo "================================================================"
echo "passed suites: ${#PASSED[@]}"
for n in "${PASSED[@]:-}"; do [[ -n "$n" ]] && echo "  OK    $n"; done
if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo "failed suites: ${#FAILED[@]}"
    for n in "${FAILED[@]}"; do echo "  FAIL  $n"; done
    exit 1
fi
echo "all green"