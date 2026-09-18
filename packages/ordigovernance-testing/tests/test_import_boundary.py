"""Import boundary gate: the private-import rule (script self-tests).

The script is loaded by path (it lives outside the packages tree) and
driven against synthetic package layouts under tmp_path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "check_import_boundary",
        ROOT / "scripts" / "check_import_boundary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_pkg(root: Path, dist: str, subpkg: str, name: str,
               body: str) -> None:
    src = root / "packages" / dist / "src" / "ordigovernance" / subpkg
    src.mkdir(parents=True, exist_ok=True)
    (src / f"{name}.py").write_text(body)


def test_cross_package_private_import_flagged(tmp_path):
    gate = _load_gate()
    _write_pkg(tmp_path, "pkg-a", "aa", "mod",
               "from ordigovernance.bb.mod import _private\n")
    findings = gate.check(tmp_path / "packages")
    assert findings and any("_private" in f for f in findings)


def test_same_package_private_import_allowed(tmp_path):
    gate = _load_gate()
    _write_pkg(tmp_path, "pkg-a", "aa", "mod",
               "from ordigovernance.aa.helpers import _private\n")
    assert gate.check(tmp_path / "packages") == []


def test_public_cross_package_import_allowed(tmp_path):
    gate = _load_gate()
    _write_pkg(tmp_path, "pkg-a", "aa", "mod",
               "from ordigovernance.bb.mod import public_name\n")
    assert gate.check(tmp_path / "packages") == []


def test_parenthesized_private_import_flagged(tmp_path):
    gate = _load_gate()
    body = ("from ordigovernance.bb.mod import (\n"
            "    public_name,\n"
            "    _private,\n"
            ")\n")
    _write_pkg(tmp_path, "pkg-a", "aa", "mod", body)
    findings = gate.check(tmp_path / "packages")
    assert findings and any("_private" in f for f in findings)


def test_aliased_private_import_flagged(tmp_path):
    gate = _load_gate()
    _write_pkg(tmp_path, "pkg-a", "aa", "mod",
               "from ordigovernance.bb.mod import _private as alias\n")
    findings = gate.check(tmp_path / "packages")
    assert findings and any("_private" in f for f in findings)


def test_orditect_private_import_flagged(tmp_path):
    gate = _load_gate()
    _write_pkg(tmp_path, "pkg-a", "aa", "mod",
               "from orditect.flow.storage import _internal\n")
    findings = gate.check(tmp_path / "packages")
    assert findings and any("_internal" in f for f in findings)