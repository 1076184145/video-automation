from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote


@dataclass(frozen=True)
class RouteMatch:
    endpoint: str
    params: dict[str, str]


@dataclass(frozen=True)
class Route:
    method: str
    template: str
    endpoint: str

    def match(self, method: str, path: str) -> RouteMatch | None:
        if method.upper() != self.method:
            return None
        template_parts = _parts(self.template)
        path_parts = _parts(path)
        params: dict[str, str] = {}
        index = 0
        for template_part in template_parts:
            if template_part.startswith("{") and template_part.endswith(":path}"):
                name = template_part[1:-6]
                if index >= len(path_parts):
                    return None
                params[name] = unquote("/".join(path_parts[index:]))
                index = len(path_parts)
                break
            if index >= len(path_parts):
                return None
            if template_part.startswith("{") and template_part.endswith("}"):
                params[template_part[1:-1]] = unquote(path_parts[index])
            elif template_part != path_parts[index]:
                return None
            index += 1
        if index != len(path_parts):
            return None
        return RouteMatch(self.endpoint, params)


class Router:
    def __init__(self, routes: tuple[Route, ...]):
        self.routes = routes

    def resolve(self, method: str, path: str) -> RouteMatch | None:
        for route in self.routes:
            matched = route.match(method, path)
            if matched is not None:
                return matched
        return None


def _parts(path: str) -> list[str]:
    return [part for part in str(path).strip("/").split("/") if part]


CORE_ROUTER = Router((
    Route("GET", "/", "root"),
    Route("GET", "/static/{asset_path:path}", "static_file"),
    Route("GET", "/health", "health"),
    Route("GET", "/recordings", "recordings"),
    Route("GET", "/publish/packages", "publish_packages"),
    Route("GET", "/events", "events"),
    Route("GET", "/jobs", "jobs"),
    Route("GET", "/jobs/{job_name}/files/{filename:path}", "job_file"),
    Route("GET", "/jobs/{job_name}", "job"),
    Route("POST", "/health/install-tools", "install_tools"),
    Route("POST", "/settings", "update_settings"),
    Route("POST", "/recordings/upload", "upload_recording"),
    Route("POST", "/jobs/{job_name}/approve", "approve_job"),
    Route("POST", "/jobs/{job_name}/cancel", "cancel_job"),
    Route("POST", "/jobs/{job_name}/cuts", "update_job_cuts"),
    Route("POST", "/jobs/{job_name}/transcript", "update_job_transcript"),
    Route("POST", "/jobs/{job_name}/clip-feedback", "save_clip_feedback"),
    Route("POST", "/jobs/{job_name}/rerun", "rerun_job_stage"),
    Route("POST", "/jobs/{job_name}/covers/generate", "generate_job_covers"),
    Route("POST", "/jobs/{job_name}/covers/select", "select_job_cover"),
    Route("POST", "/jobs/{job_name}/segments/generate", "generate_job_segments"),
    Route("POST", "/jobs/{job_name}/metadata/generate", "generate_job_metadata"),
    Route("POST", "/jobs/{job_name}/metadata", "save_job_metadata"),
    Route("POST", "/jobs/{job_name}/highlights/generate", "generate_job_highlights"),
    Route("POST", "/jobs/{job_name}/highlights/cut", "generate_job_highlight_cut"),
    Route("POST", "/jobs/{job_name}/highlights/render", "render_job_highlight_cut"),
    Route("POST", "/jobs/{job_name}/publish/package", "generate_job_publish_package"),
    Route("POST", "/jobs/{job_name}/project-export/generate", "generate_job_project_export"),
    Route("POST", "/jobs/{job_name}/subtitles/translate", "translate_job_subtitles"),
    Route("POST", "/jobs/{job_name}/subtitles/render-translated", "render_translated_subtitles"),
    Route("POST", "/process/batch", "process_batch"),
    Route("POST", "/process", "process_one"),
    Route("DELETE", "/jobs/{job_name}", "delete_job"),
))
