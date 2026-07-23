from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StageSpec:
    name: str
    status: str
    dependencies: frozenset[str] = frozenset()
    rerun_dependencies: frozenset[str] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "dependencies": sorted(self.dependencies),
            "rerun_dependencies": sorted(self.rerun_dependencies),
        }


PIPELINE_STAGE_SPECS: dict[str, StageSpec] = {
    spec.name: spec
    for spec in (
        StageSpec("probe", "probing"),
        StageSpec("detect_corruption", "detecting_corruption", frozenset({"probe"}), frozenset({"probe"})),
        StageSpec("extract_audio", "extracting_audio", frozenset({"probe", "detect_corruption"}), frozenset({"probe"})),
        StageSpec("transcribe", "transcribing", frozenset({"extract_audio"}), frozenset({"probe", "extract_audio"})),
        StageSpec("detect_silence", "detecting_silence", frozenset({"extract_audio"}), frozenset({"probe", "extract_audio"})),
        StageSpec("detect_freeze", "detecting_freeze", frozenset({"probe"}), frozenset({"probe"})),
        StageSpec("detect_scenes", "detecting_scenes", frozenset({"probe", "detect_freeze"}), frozenset({"probe"})),
        StageSpec("plan_cuts", "planning_cuts", frozenset({"transcribe", "detect_silence", "detect_freeze", "detect_scenes"}), frozenset({"probe", "extract_audio", "transcribe"})),
        StageSpec(
            "refine_cuts",
            "refining_cuts",
            frozenset({"plan_cuts"}),
            frozenset({"probe", "extract_audio", "transcribe", "plan_cuts"}),
        ),
        StageSpec("plan_crop", "planning_crop", frozenset({"probe"}), frozenset({"probe"})),
        StageSpec("style_subtitles", "styling_subtitles", frozenset({"transcribe", "refine_cuts"}), frozenset({"probe", "extract_audio", "transcribe", "plan_cuts", "refine_cuts"})),
        StageSpec("plan_uvr", "planning_uvr", frozenset({"extract_audio"}), frozenset({"probe", "extract_audio"})),
        StageSpec("plan_render", "planning_render", frozenset({"refine_cuts", "style_subtitles", "plan_uvr"}), frozenset({"probe", "extract_audio", "transcribe", "plan_cuts", "refine_cuts", "style_subtitles"})),
        StageSpec("render_review", "rendering_review", frozenset({"plan_render"}), frozenset({"probe", "extract_audio", "transcribe", "plan_cuts", "style_subtitles", "plan_render"})),
        StageSpec("render_final", "rendering_final", frozenset({"plan_render", "plan_crop", "render_review"}), frozenset({"probe", "extract_audio", "transcribe", "plan_cuts", "plan_crop", "style_subtitles", "plan_render"})),
        StageSpec("render_web_preview", "rendering_web_preview", frozenset({"render_final"}), frozenset({"probe", "extract_audio", "transcribe", "plan_cuts", "style_subtitles", "plan_render"})),
    )
}

PIPELINE_STAGE_DEPENDENCIES = {
    name: set(spec.dependencies) for name, spec in PIPELINE_STAGE_SPECS.items()
}
PIPELINE_STAGE_SELECTION_DEPENDENCIES = {
    name: set(spec.rerun_dependencies) for name, spec in PIPELINE_STAGE_SPECS.items()
}


def pipeline_stage_contract() -> list[dict[str, Any]]:
    return [spec.to_dict() for spec in PIPELINE_STAGE_SPECS.values()]
