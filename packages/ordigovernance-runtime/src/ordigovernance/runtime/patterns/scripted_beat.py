"""ScriptedBeat: deterministic per-run narrative beats.

Generalized from the reference application's DemoScript: run-scoped
deterministic decisions so a scripted narrative always lands. All
randomness-free decisions live here; the object is constructed PER RUN
so flags never leak into later runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScriptedBeat:
    """Run-scoped scripted beats (failure injection + score sequence)."""

    failing_position_index: int = 0
    score_sequence: tuple = ()
    pass_threshold: float | None = None
    _fired_once: set = field(default_factory=set)

    def failing_position(self, total: int) -> int:
        """1-based position scripted to fail; -1 disables.

        Clamped to the actual fan-out size so a small item set still
        lands the failure beat.
        """
        if self.failing_position_index <= 0 or total <= 0:
            return -1
        return min(self.failing_position_index, total)

    def should_fail_once(self, fail_candidate: bool, key: str) -> bool:
        """True exactly once per run for the scripted candidate.

        A reopened generation asks again and gets False, so the rerun
        succeeds: the failure beat is a first-generation event by design.
        """
        if not fail_candidate or key in self._fired_once:
            return False
        self._fired_once.add(key)
        return True

    def score(self, generation_index: int, default=None):
        """Scripted verdict for the Nth generation (1-based).

        The last configured value repeats for extra iterations so the
        gate always converges before the hard cap.
        """
        if not self.score_sequence:
            return default
        idx = min(generation_index - 1, len(self.score_sequence) - 1)
        return self.score_sequence[idx]

    def is_pass(self, verdict) -> bool:
        """Default pass predicate when the verdict is numeric."""
        if self.pass_threshold is None:
            return bool(verdict)
        return verdict >= self.pass_threshold