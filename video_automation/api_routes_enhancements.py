from __future__ import annotations

from .api_job_utils import job_is_terminal, publish_job_dir_event
from .covers import (
    mark_cover_generation_started,
    normalize_cover_options,
    select_cover,
)
from .highlight_cut import generate_highlight_cut
from .llm_tools import save_metadata
from .routing import RouteMatch
from .subtitle_translation import (
    translated_clipped_ass_name,
    translated_final_video_name,
)


class EnhancementRoutes:
    """Optional cover, metadata, highlight, export, and translation routes."""

    def _route_generate_job_covers(self, matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None:
            return
        if not job_is_terminal(job):
            self._json(
                {
                    "error": (
                        f"job is already {job.status}; wait for it to finish "
                        "before generating covers"
                    )
                },
                status=409,
            )
            return
        if (
            settings.cover_provider.strip().lower()
            in {"openai", "openai-compatible", "openrouter", "google"}
            and not settings.cover_api_key_for_provider()
        ):
            self._json(
                {
                    "error": (
                        "COVER_API_KEY, OPENAI_API_KEY, or GOOGLE_API_KEY is not configured"
                    )
                },
                status=400,
            )
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            options = normalize_cover_options(settings, payload)
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)
            return
        manifest = mark_cover_generation_started(settings, job.job_dir, options)
        publish_job_dir_event(job.job_dir)
        queue_item = self._enqueue_job_command(job, "generate_covers", options)
        if queue_item is None:
            return
        self._json(
            {"job": self._job_payload(job), "cover": manifest, "queue": queue_item},
            status=202,
        )

    def _route_select_job_cover(self, matched: RouteMatch, _query: str) -> None:
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None:
            return
        if not job_is_terminal(job):
            self._json(
                {
                    "error": (
                        f"job is already {job.status}; wait for it to finish "
                        "before selecting covers"
                    )
                },
                status=409,
            )
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            manifest = select_cover(
                job.job_dir,
                aspect=str(payload.get("aspect") or "").strip(),
                candidate=str(payload.get("candidate") or "").strip(),
            )
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)
            return
        publish_job_dir_event(job.job_dir)
        self._json({"job": self._job_payload(job), "cover": manifest})

    def _route_generate_job_segments(self, matched: RouteMatch, _query: str) -> None:
        self._queue_job_enhancement(
            matched.params.get("job_name", ""),
            "generate_segments",
        )

    def _route_generate_job_metadata(self, matched: RouteMatch, _query: str) -> None:
        self._queue_job_enhancement(
            matched.params.get("job_name", ""),
            "generate_metadata",
        )

    def _route_save_job_metadata(self, matched: RouteMatch, _query: str) -> None:
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None or not self._allow_quick_mutation(job):
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            metadata = save_metadata(job.job_dir, payload)
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)
            return
        self._json({"job": self._job_payload(job), "metadata": metadata})

    def _route_generate_job_highlights(self, matched: RouteMatch, _query: str) -> None:
        self._queue_job_enhancement(
            matched.params.get("job_name", ""),
            "generate_highlights",
        )

    def _route_generate_job_highlight_cut(self, matched: RouteMatch, _query: str) -> None:
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None or not self._allow_quick_mutation(job):
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            highlight_cut = generate_highlight_cut(
                job.job_dir,
                target_seconds=float(payload.get("target_seconds") or 60),
                force=bool(payload.get("force", False)),
            )
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)
            return
        self._json({"job": self._job_payload(job), "highlight_cut": highlight_cut})

    def _route_render_job_highlight_cut(self, matched: RouteMatch, _query: str) -> None:
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None:
            return
        payload = self._read_json()
        if payload is None:
            return
        target_seconds = float(payload.get("target_seconds") or 60)
        try:
            highlight_cut = generate_highlight_cut(
                job.job_dir,
                target_seconds=target_seconds,
                force=bool(payload.get("force_cut", False)),
            )
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)
            return
        queue_item = self._enqueue_job_command(
            job,
            "render_highlight",
            {"highlight_cut": highlight_cut},
        )
        if queue_item is None:
            return
        self._json(
            {
                "job": self._job_payload(job),
                "status": "queued",
                "highlight_cut": highlight_cut,
                "output": "highlight.mp4",
                "queue": queue_item,
            },
            status=202,
        )

    def _route_generate_job_publish_package(self, matched: RouteMatch, _query: str) -> None:
        self._queue_job_enhancement(
            matched.params.get("job_name", ""),
            "generate_publish_package",
        )

    def _route_generate_job_project_export(self, matched: RouteMatch, _query: str) -> None:
        self._queue_job_enhancement(
            matched.params.get("job_name", ""),
            "generate_project_export",
        )

    def _route_translate_job_subtitles(self, matched: RouteMatch, _query: str) -> None:
        self._queue_job_enhancement(
            matched.params.get("job_name", ""),
            "translate_subtitles",
        )

    def _route_render_translated_subtitles(self, matched: RouteMatch, _query: str) -> None:
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None:
            return
        payload = self._read_json()
        if payload is None:
            return
        target_language = str(payload.get("target_language") or "zh").strip() or "zh"
        try:
            subtitle_name = translated_clipped_ass_name(target_language)
            output_filename = translated_final_video_name(target_language)
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)
            return
        subtitle_file = job.job_dir / subtitle_name
        if not subtitle_file.exists() or subtitle_file.stat().st_size < 1:
            self._json(
                {"error": f"translated subtitles are not ready for {target_language}"},
                status=400,
            )
            return
        queue_item = self._enqueue_job_command(
            job,
            "render_translated_subtitles",
            {
                "target_language": target_language,
                "output_filename": output_filename,
            },
        )
        if queue_item is None:
            return
        self._json(
            {
                "job": self._job_payload(job),
                "status": "queued",
                "target_language": target_language,
                "output": output_filename,
                "queue": queue_item,
            },
            status=202,
        )
