"""Import boundary gate: machine-enforced layering for the ordigovernance repo.

Rules (each is checked by scanning source files for import statements):

  1. No packaged code may import the legacy closed-source namespace
     (`orditect_components`) or any configured closed-tier package.
  2. `ordigovernance-api` must stay stdlib-only: every non-stdlib top-level
     import inside it is a violation.
  3. `ordigovernance-testing` may only import stdlib, `ordigovernance.*`
     and `orditect.*` (its hot-path fixtures wrap the framework).
  4. No packaged code may import `examples` (examples import packages,
     never the other way around).
  5. No packaged code may import underscore-prefixed (private) names
     across the top-level ordigovernance package boundary
     (api/runtime/testing/viewer/gateway/bridges-*) or from the
     orditect framework: cross-package contracts are public names.
     Private imports inside the same top-level package are allowed.
Exit code 0 when the boundary holds, 1 with a finding list otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = ROOT / "packages"

# Closed-tier namespaces that packaged open code must never import.
# `orditect_components` is the archived legacy monorepo; `ordienterprise`
# is the closed commercial tier built on top of this stack.
CLOSED_NAMESPACES = (
    "orditect_components",
    "ordienterprise",
)

_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)",
    re.MULTILINE,
)
_FROM_IMPORT_RE = re.compile(
    r"^\s*from\s+([A-Za-z_][A-Za-z0-9_.]*)\s+import\s+(\([^)]*\)|[^\n]+)",
    re.MULTILINE,
)

# Namespaces whose private names are contract-protected.
_PRIVATE_NAMESPACES = ("ordigovernance", "orditect")


def _from_imports_of(path: Path) -> list[tuple[str, list[str]]]:
    """(module, imported names) pairs of one file's from-imports."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[tuple[str, list[str]]] = []
    for module, names_blob in _FROM_IMPORT_RE.findall(text):
        names = []
        for raw in names_blob.strip("()").split(","):
            tokens = raw.split()
            if tokens:
                names.append(tokens[0])
        out.append((module, names))
    return out


def _subpackage_of(path: Path, packages_dir: Path) -> str | None:
    """The top-level ordigovernance package one source file belongs to.

    packages/<dist>/src/ordigovernance/<subpackage>/... -> <subpackage>
    """
    try:
        rel = path.relative_to(packages_dir)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 4 and parts[1] == "src" and parts[2] == "ordigovernance":
        return parts[3]
    return None


_STDLIB = set(sys.stdlib_module_names)


def _top_level(module: str) -> str:
    return module.split(".", 1)[0]


def _iter_package_sources(packages_dir: Path | None = None):
    root = packages_dir or PACKAGES_DIR
    for src_root in sorted(root.glob("*/src")):
        package = src_root.parent.name
        for path in sorted(src_root.rglob("*.py")):
            yield package, path


def _imports_of(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return _IMPORT_RE.findall(text)


def _is_internal_api_import(module: str) -> bool:
    """Internal self-imports inside ordigovernance-api.

    The api package may import its own submodules only; anything else
    under the ordigovernance namespace (runtime, testing, ...) is a
    reverse dependency and must fail the gate.
    """
    return module == "ordigovernance.api" \
        or module.startswith("ordigovernance.api.")


def check(packages_dir: Path | None = None) -> list[str]:
    findings: list[str] = []
    root = packages_dir or PACKAGES_DIR
    for package, path in _iter_package_sources(root):
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            rel = path
        for module in _imports_of(path):
            top = _top_level(module)
            if top in CLOSED_NAMESPACES:
                findings.append(
                    f"{rel}: imports closed-tier namespace {top!r}")
            if top == "examples":
                findings.append(
                    f"{rel}: packaged code must not import 'examples'")
            if package == "ordigovernance-api" \
                    and top not in _STDLIB \
                    and not _is_internal_api_import(module):
                # api stays free of EXTERNAL third-party deps; only its
                # own subpackage imports are internal.
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
        # Rule 5: private names are package-internal; cross-package
        # contracts are public names only.
        source_sub = _subpackage_of(path, root)
        if source_sub is not None:
            for module, names in _from_imports_of(path):
                top = _top_level(module)
                if top not in _PRIVATE_NAMESPACES:
                    continue
                if top == "ordigovernance":
                    parts = module.split(".")
                    target_sub = parts[1] if len(parts) > 1 else None
                    if target_sub is None or target_sub == source_sub:
                        continue
                private = [n for n in names if n.startswith("_")]
                if private:
                    findings.append(
                        f"{rel}: imports private name(s) {private} from "
                        f"{module!r} across the package boundary")
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