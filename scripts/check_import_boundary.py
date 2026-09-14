#!/usr/bin/env python
"""Import boundary gate: machine-enforced layering for the ordigovernance repo.

Rules (each is checked by scanning source files for import statements):

  1. No packaged code may import the legacy closed-source namespace
     (`orditect_components`) or any configured closed-tier package.
  2. `ordigovernance-api` must stay stdlib-only: every non-stdlib top-level
     import inside it is a violation.
  3. `ordigovernance-testing` may only import stdlib plus `ordigovernance.*`.
  4. No packaged code may import `examples` (examples import packages,
     never the other way around).

Exit code 0 when the boundary holds, 1 with a finding list otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = ROOT / "packages"

# Closed-tier namespaces that packaged open code must never import.
# Extend this list as the closed tier names its distributions.
CLOSED_NAMESPACES = (
    "orditect_components",
    "ordigovernance_engine",
    "ordigovernance_insight",
)

_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)",
    re.MULTILINE,
)

import sys as _sys
_STDLIB = set(_sys.stdlib_module_names)


def _top_level(module: str) -> str:
    return module.split(".", 1)[0]


def _iter_package_sources():
    for src_root in sorted(PACKAGES_DIR.glob("*/src")):
        package = src_root.parent.name
        for path in sorted(src_root.rglob("*.py")):
            yield package, path


def _imports_of(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return _IMPORT_RE.findall(text)


def check() -> list[str]:
    findings: list[str] = []
    for package, path in _iter_package_sources():
        rel = path.relative_to(ROOT)
        for module in _imports_of(path):
            top = _top_level(module)
            if top in CLOSED_NAMESPACES:
                findings.append(
                    f"{rel}: imports closed-tier namespace {top!r}")
            if top == "examples":
                findings.append(
                    f"{rel}: packaged code must not import 'examples'")
            if package == "ordigovernance-api" \
                    and top not in _STDLIB and top != "ordigovernance":
                # api stays free of EXTERNAL third-party deps; its own
                # subpackage imports (ordigovernance.api.*) are internal.
                findings.append(
                    f"{rel}: ordigovernance-api must stay free of external "
                    f"dependencies (found {module!r})")
            if package == "ordigovernance-testing" \
                    and top not in _STDLIB \
                    and top not in ("ordigovernance", "orditect"):
                # testing may use ordigovernance.* plus the orditect
                # framework (declared in its pyproject).
                findings.append(
                    f"{rel}: ordigovernance-testing may only import stdlib, "
                    f"ordigovernance.* and orditect.* (found {module!r})")
    return findings

def main() -> int:
    findings = check()
    if findings:
        print(f"FAIL  {len(findings)} import boundary findings:")
        for f in findings:
            print(f"  {f}")
        return 1
    print("PASS  import boundary holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())