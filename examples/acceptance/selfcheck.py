"""Open-tier acceptance self-check: determinism + conformance.

Runs the full acceptance workflow TWICE into fresh trace dirs,
normalizes both bundles with the golden facility, and asserts:

  1. the two runs are structurally identical (diff_summary empty):
     the open tier's always-execute behavior is deterministic by
     construction;
  2. the producer conformance profile returns zero findings on one
     of the bundles: the evidence shapes satisfy the ordigovernance
     contract.

    python -m examples.acceptance.selfcheck
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from examples.acceptance.app import execute_acceptance_run
from ordigovernance.testing.conformance import run_producer_profile
from ordigovernance.testing.golden import (
    diff_summary,
    normalize_bundle,
    summarize,
)


async def _run_once() -> Path:
    # trace_dir must be a SUBDIRECTORY of the mkdtemp path:
    # build_run_context cleans trace_dir.parent. A bare mkdtemp path
    # would make the parent /tmp, so the second run's cleanup would
    # silently delete the first run's bundle before the diff.
    trace_dir = Path(tempfile.mkdtemp(
        prefix="ordigovernance-acceptance-")) / "trace"
    record = await execute_acceptance_run(trace_dir)
    if record["status"] != "succeeded":
        raise RuntimeError(f"acceptance run failed: {record['status']}")
    return trace_dir

def main() -> int:
    dir_a = asyncio.run(_run_once())
    dir_b = asyncio.run(_run_once())
    try:
        golden = normalize_bundle(dir_a)
        current = normalize_bundle(dir_b)
        diffs = diff_summary(summarize(golden), summarize(current),
                             tolerances={"audit_total": 10})
        if diffs:
            print(f"FAIL  {len(diffs)} summary diffs between two runs:")
            for d in diffs[:20]:
                print(f"  {d}")
            return 1

        findings = run_producer_profile(golden)
        if findings:
            print(f"FAIL  {len(findings)} conformance findings:")
            for f in findings[:20]:
                print(f"  {f}")
            return 1

        print(f"PASS  two runs are structurally identical and "
              f"conformant ({dir_a.name} == {dir_b.name})")
        return 0
    finally:
        shutil.rmtree(dir_a, ignore_errors=True)
        shutil.rmtree(dir_b, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())