from __future__ import annotations

from .clip_state import (
    ClipQualityReport,
    ClipRefinementState,
    RefinementAction,
    RefinementActionKind,
)


class DeterministicClipPolicy:
    """Bounded policy for safe clip-boundary repair.

    The policy can only select from validated, structured actions. It never
    executes commands, reads files, or changes retry budgets.
    """

    def choose_action(
        self,
        state: ClipRefinementState,
        report: ClipQualityReport,
    ) -> RefinementAction:
        if report.passed:
            return RefinementAction(
                RefinementActionKind.ACCEPT,
                "All deterministic clip checks passed.",
            )

        blocking = next((issue for issue in report.issues if issue.blocking), None)
        if blocking is not None:
            return RefinementAction(
                RefinementActionKind.REQUIRE_REVIEW,
                f"Blocking issue requires manual review: {blocking.code}",
                clip_index=blocking.clip_index,
            )

        repairable = next((issue for issue in report.issues if issue.repairable), None)
        if repairable is not None:
            return RefinementAction(
                RefinementActionKind.ADJUST_BOUNDARY,
                f"Repair deterministic boundary issue: {repairable.code}",
                clip_index=repairable.clip_index,
                start=repairable.suggested_start,
                end=repairable.suggested_end,
            )

        return RefinementAction(
            RefinementActionKind.REQUIRE_REVIEW,
            "No safe deterministic repair is available.",
        )
