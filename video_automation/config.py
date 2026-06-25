from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _project_root() -> Path:
    override = os.environ.get("VIDEO_AUTOMATION_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
_ENV_FILE_CACHE: dict[Path, tuple[tuple[int, int, str], dict[str, str]]] = {}

DEFAULT_WHISPER_INITIAL_PROMPT = ""
DEFAULT_PROFANITY_WORDS = (
    "\u64cd,\u8279,\u5367\u69fd,\u6211\u64cd,\u9000,\u50bb\u903c,"
    "\u725b\u903c,\u88c5\u903c,\u5988\u7684,\u4ed6\u5988\u7684,\u8349\u6ce5\u9a6c"
)
DEFAULT_SUBTITLE_CENSOR_REPLACEMENT = "[\u54d4]"


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _cached_env_file(path: Path) -> dict[str, str]:
    try:
        stat = path.stat()
    except OSError:
        _ENV_FILE_CACHE.pop(path, None)
        return {}
    try:
        content = path.read_bytes()
    except OSError:
        _ENV_FILE_CACHE.pop(path, None)
        return {}
    digest = hashlib.sha1(content).hexdigest()
    cache_key = (stat.st_mtime_ns, stat.st_size, digest)
    cached = _ENV_FILE_CACHE.get(path)
    if cached and cached[0] == cache_key:
        return dict(cached[1])
    values = _parse_env_text(content.decode("utf-8"))
    _ENV_FILE_CACHE[path] = (cache_key, values)
    return dict(values)


def _parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env(name: str, default: str = "") -> str:
    env_path = PROJECT_ROOT / ".env"
    example_path = PROJECT_ROOT / ".env.example"
    file_values = {**_cached_env_file(example_path), **_cached_env_file(env_path)}
    env_value = os.environ.get(name)
    if env_value is not None:
        return env_value
    return file_values.get(name) or default


def _portable_tool_path(root: Path, env_name: str, portable_name: str, fallback_name: str) -> Path:
    configured = _env(env_name, "")
    if configured:
        return Path(configured)
    portable = root / "tools" / "bin" / portable_name
    if portable.exists():
        return portable
    return Path(fallback_name)


def _portable_optional_tool_path(root: Path, env_name: str, portable_name: str) -> Path:
    configured = _env(env_name, "")
    if configured:
        return Path(configured)
    portable = root / "tools" / "bin" / portable_name
    if portable.exists():
        return portable
    return Path("")


def _float_env(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _int_env(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    root: Path
    input_recordings_dir: Path
    jobs_dir: Path
    logs_dir: Path
    ffmpeg_path: Path
    ffprobe_path: Path
    audiowaveform_path: Path
    native_waveform_enabled: bool
    native_cuts_enabled: bool
    high_quality_audio_enabled: bool
    whisper_bin: Path
    whisper_backend: str
    whisper_model: str
    whisper_model_fallbacks: tuple[str, ...]
    whisper_language: str
    whisper_initial_prompt: str
    whisper_timeout_min_seconds: int
    whisper_timeout_multiplier: float
    whisper_word_timestamps: bool
    whisper_vad_filter: bool
    faster_whisper_device: str
    faster_whisper_compute_type: str
    faster_whisper_batch_size: int
    funasr_model: str
    funasr_vad_model: str
    funasr_punc_model: str
    funasr_device: str
    funasr_hotwords: str
    funasr_batch_size_s: int
    funasr_max_segment_ms: int
    funasr_persistent_worker: bool
    transcribe_audio_filter: str
    profanity_words: tuple[str, ...]
    subtitle_replacements: tuple[tuple[str, str], ...]
    subtitle_censor_replacement: str
    subtitle_min_duration_seconds: float
    subtitle_music_vocal_filter_enabled: bool
    subtitle_music_vocal_min_duration_seconds: float
    subtitle_music_vocal_min_chars_per_second: float
    subtitle_music_vocal_min_avg_probability: float
    subtitle_music_vocal_patterns: tuple[str, ...]
    file_stable_seconds: float
    poll_interval_seconds: float
    silence_min_length_seconds: float
    silence_min_gap_seconds: float
    cut_min_clip_seconds: float
    cut_merge_gap_seconds: float
    silence_threshold_db: float
    freeze_noise_db: float
    freeze_min_duration_seconds: float
    scene_threshold: float
    source_integrity_scan_enabled: bool
    source_integrity_scan_timeout_multiplier: float
    source_integrity_scan_max_errors: int
    visual_detect_keyframes_only: bool
    visual_detect_fps: float
    visual_detect_width: int
    ass_font_name: str
    ass_preset: str
    ass_font_size: int
    ass_primary_color: str
    ass_outline_color: str
    ass_back_color: str
    ass_alignment: int
    ass_margin_v: int
    ass_outline: float
    ass_shadow: float
    ass_max_lines: int
    ass_vertical_font_size: int
    api_host: str
    api_port: int
    api_parallel_jobs: int
    api_batch_limit: int
    api_allowed_origins: tuple[str, ...]
    recording_upload_max_bytes: int
    llm_provider: str
    llm_model: str
    llm_translation_batch_size: int
    llm_translation_batch_chars: int
    google_api_key: str
    google_base_url: str
    publish_enabled: bool
    publish_providers: tuple[str, ...]
    export_platforms: tuple[str, ...]
    render_video_encoder: str
    render_output_fps: int
    render_x264_preset: str
    render_x264_crf: int
    render_nvenc_preset: str
    render_nvenc_cq: int
    render_nvenc_preview_preset: str
    render_nvenc_preview_cq: int
    web_preview_enabled: bool
    web_preview_max_width: int
    web_preview_max_height: int
    web_preview_fps: int
    web_preview_video_bitrate: str
    bgm_path: Path | None
    bgm_volume: float
    source_audio_volume: float
    vertical_mode: str
    crop_anchor_x: float
    crop_anchor_y: float
    webhook_url: str
    cover_provider: str
    cover_model: str
    cover_count: int
    cover_aspects: tuple[str, ...]
    cover_quality: str
    cover_output_format: str
    cover_title_font: str
    cover_base_url: str
    cover_api_key: str
    cover_http_referer: str
    cover_app_title: str
    cover_modalities: tuple[str, ...]
    openai_api_key: str
    audio_separation_engine: str
    demucs_path: Path
    demucs_model: str
    demucs_device: str
    audio_separation_timeout_seconds: int
    uvr_path: Path | None

    def cover_api_key_for_provider(self) -> str:
        if self.cover_api_key.strip():
            return self.cover_api_key.strip()
        if self.cover_provider.strip().lower() == "google":
            return self.google_api_key.strip()
        return self.openai_api_key.strip()

    @classmethod
    def load(cls) -> "Settings":
        root = Path(_env("VIDEO_AUTOMATION_ROOT", str(PROJECT_ROOT))).expanduser()
        return cls(
            root=root,
            input_recordings_dir=Path(_env("INPUT_RECORDINGS_DIR", str(root / "input" / "recordings"))),
            jobs_dir=Path(_env("JOBS_DIR", str(root / "processing" / "jobs"))),
            logs_dir=Path(_env("LOGS_DIR", str(root / "logs"))),
            ffmpeg_path=_portable_tool_path(root, "FFMPEG_PATH", "ffmpeg.exe", "ffmpeg"),
            ffprobe_path=_portable_tool_path(root, "FFPROBE_PATH", "ffprobe.exe", "ffprobe"),
            audiowaveform_path=_portable_tool_path(root, "AUDIOWAVEFORM_PATH", "audiowaveform.exe", "audiowaveform"),
            native_waveform_enabled=_bool_env("NATIVE_WAVEFORM_ENABLED", True),
            native_cuts_enabled=_bool_env("NATIVE_CUTS_ENABLED", True),
            high_quality_audio_enabled=_bool_env("HIGH_QUALITY_AUDIO_ENABLED", True),
            whisper_bin=Path(_env("WHISPER_BIN", "whisper")),
            whisper_backend=_env("WHISPER_BACKEND", "faster-whisper"),
            whisper_model=_env("WHISPER_MODEL", "large-v3"),
            whisper_model_fallbacks=_words_env("WHISPER_MODEL_FALLBACKS", "large-v3-turbo,medium"),
            whisper_language=_env("WHISPER_LANGUAGE", "auto"),
            whisper_initial_prompt=_env("WHISPER_INITIAL_PROMPT", DEFAULT_WHISPER_INITIAL_PROMPT),
            whisper_timeout_min_seconds=_int_env("WHISPER_TIMEOUT_MIN_SECONDS", 300),
            whisper_timeout_multiplier=_float_env("WHISPER_TIMEOUT_MULTIPLIER", 10),
            whisper_word_timestamps=_bool_env("WHISPER_WORD_TIMESTAMPS", True),
            whisper_vad_filter=_bool_env("WHISPER_VAD_FILTER", True),
            faster_whisper_device=_env("FASTER_WHISPER_DEVICE", "cuda"),
            faster_whisper_compute_type=_env("FASTER_WHISPER_COMPUTE_TYPE", "int8_float16"),
            faster_whisper_batch_size=max(1, _int_env("FASTER_WHISPER_BATCH_SIZE", 8)),
            funasr_model=_env("FUNASR_MODEL", "paraformer-zh"),
            funasr_vad_model=_env("FUNASR_VAD_MODEL", "fsmn-vad"),
            funasr_punc_model=_env("FUNASR_PUNC_MODEL", "ct-punc"),
            funasr_device=_env("FUNASR_DEVICE", "cuda:0"),
            funasr_hotwords=_env("FUNASR_HOTWORDS", ""),
            funasr_batch_size_s=_int_env("FUNASR_BATCH_SIZE_S", 300),
            funasr_max_segment_ms=_int_env("FUNASR_MAX_SEGMENT_MS", 60000),
            funasr_persistent_worker=_bool_env("FUNASR_PERSISTENT_WORKER", True),
            transcribe_audio_filter=_env("TRANSCRIBE_AUDIO_FILTER", ""),
            profanity_words=_words_env("PROFANITY_WORDS", DEFAULT_PROFANITY_WORDS),
            subtitle_replacements=_replacements_env("SUBTITLE_REPLACEMENTS"),
            subtitle_censor_replacement=_env("SUBTITLE_CENSOR_REPLACEMENT", DEFAULT_SUBTITLE_CENSOR_REPLACEMENT),
            subtitle_min_duration_seconds=_float_env("SUBTITLE_MIN_DURATION_SECONDS", 0.3),
            subtitle_music_vocal_filter_enabled=_bool_env("SUBTITLE_MUSIC_VOCAL_FILTER_ENABLED", True),
            subtitle_music_vocal_min_duration_seconds=_float_env("SUBTITLE_MUSIC_VOCAL_MIN_DURATION_SECONDS", 1.5),
            subtitle_music_vocal_min_chars_per_second=_float_env("SUBTITLE_MUSIC_VOCAL_MIN_CHARS_PER_SECOND", 1.8),
            subtitle_music_vocal_min_avg_probability=_float_env("SUBTITLE_MUSIC_VOCAL_MIN_AVG_PROBABILITY", 0.0),
            subtitle_music_vocal_patterns=_words_env("SUBTITLE_MUSIC_VOCAL_PATTERNS", ""),
            file_stable_seconds=_float_env("FILE_STABLE_SECONDS", 8),
            poll_interval_seconds=_float_env("POLL_INTERVAL_SECONDS", 5),
            silence_min_length_seconds=_float_env("SILENCE_MIN_LENGTH_SECONDS", 0.8),
            silence_min_gap_seconds=_float_env("SILENCE_MIN_GAP_SECONDS", 0.35),
            cut_min_clip_seconds=_float_env("CUT_MIN_CLIP_SECONDS", 2.0),
            cut_merge_gap_seconds=_float_env("CUT_MERGE_GAP_SECONDS", 1.5),
            silence_threshold_db=_float_env("SILENCE_THRESHOLD_DB", -35),
            freeze_noise_db=_float_env("FREEZE_NOISE_DB", -60),
            freeze_min_duration_seconds=_float_env("FREEZE_MIN_DURATION_SECONDS", 2),
            scene_threshold=_float_env("SCENE_THRESHOLD", 0.3),
            source_integrity_scan_enabled=_bool_env("SOURCE_INTEGRITY_SCAN_ENABLED", True),
            source_integrity_scan_timeout_multiplier=_float_env("SOURCE_INTEGRITY_SCAN_TIMEOUT_MULTIPLIER", 3.0),
            source_integrity_scan_max_errors=max(1, _int_env("SOURCE_INTEGRITY_SCAN_MAX_ERRORS", 40)),
            visual_detect_keyframes_only=_bool_env("VISUAL_DETECT_KEYFRAMES_ONLY", True),
            visual_detect_fps=_float_env("VISUAL_DETECT_FPS", 2.0),
            visual_detect_width=_int_env("VISUAL_DETECT_WIDTH", 480),
            ass_font_name=_env("ASS_FONT_NAME", "Microsoft YaHei"),
            ass_preset=_env("ASS_PRESET", "classic"),
            ass_font_size=_int_env("ASS_FONT_SIZE", 56),
            ass_primary_color=_env("ASS_PRIMARY_COLOR", "&H00FFFFFF"),
            ass_outline_color=_env("ASS_OUTLINE_COLOR", "&H00000000"),
            ass_back_color=_env("ASS_BACK_COLOR", "&H64000000"),
            ass_alignment=_int_env("ASS_ALIGNMENT", 2),
            ass_margin_v=_int_env("ASS_MARGIN_V", 90),
            ass_outline=_float_env("ASS_OUTLINE", 3),
            ass_shadow=_float_env("ASS_SHADOW", 1),
            ass_max_lines=max(1, _int_env("ASS_MAX_LINES", 2)),
            ass_vertical_font_size=max(0, _int_env("ASS_VERTICAL_FONT_SIZE", 44)),
            api_host=_env("API_HOST", "127.0.0.1"),
            api_port=_int_env("API_PORT", 8765),
            api_parallel_jobs=max(1, _int_env("API_PARALLEL_JOBS", 2)),
            api_batch_limit=max(1, _int_env("API_BATCH_LIMIT", 30)),
            api_allowed_origins=_words_env("API_ALLOWED_ORIGINS", ""),
            recording_upload_max_bytes=max(0, _int_env("RECORDING_UPLOAD_MAX_BYTES", 20 * 1024 * 1024 * 1024)),
            llm_provider=_env("LLM_PROVIDER", "openai"),
            llm_model=_env("LLM_MODEL", ""),
            llm_translation_batch_size=max(1, _int_env("LLM_TRANSLATION_BATCH_SIZE", 24)),
            llm_translation_batch_chars=max(500, _int_env("LLM_TRANSLATION_BATCH_CHARS", 6000)),
            google_api_key=_env("GOOGLE_API_KEY", ""),
            google_base_url=_env("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
            publish_enabled=_bool_env("PUBLISH_ENABLED", False),
            publish_providers=_words_env("PUBLISH_PROVIDERS", ""),
            export_platforms=_words_env("EXPORT_PLATFORMS", "douyin,bilibili,youtube_shorts"),
            render_video_encoder=_env("RENDER_VIDEO_ENCODER", "libx264"),
            render_output_fps=max(0, _int_env("RENDER_OUTPUT_FPS", 30)),
            render_x264_preset=_env("RENDER_X264_PRESET", "medium"),
            render_x264_crf=max(0, _int_env("RENDER_X264_CRF", 0)),
            render_nvenc_preset=_env("RENDER_NVENC_PRESET", "p5"),
            render_nvenc_cq=max(1, _int_env("RENDER_NVENC_CQ", 21)),
            render_nvenc_preview_preset=_env("RENDER_NVENC_PREVIEW_PRESET", "p4"),
            render_nvenc_preview_cq=max(1, _int_env("RENDER_NVENC_PREVIEW_CQ", 25)),
            web_preview_enabled=_bool_env("WEB_PREVIEW_ENABLED", True),
            web_preview_max_width=max(320, _int_env("WEB_PREVIEW_MAX_WIDTH", 960)),
            web_preview_max_height=max(320, _int_env("WEB_PREVIEW_MAX_HEIGHT", 960)),
            web_preview_fps=max(1, _int_env("WEB_PREVIEW_FPS", 24)),
            web_preview_video_bitrate=_env("WEB_PREVIEW_VIDEO_BITRATE", "1200k"),
            bgm_path=_optional_path("BGM_PATH"),
            bgm_volume=_float_env("BGM_VOLUME", 0.16),
            source_audio_volume=_float_env("SOURCE_AUDIO_VOLUME", 1.0),
            vertical_mode=_env("VERTICAL_MODE", "crop"),
            crop_anchor_x=_float_env("CROP_ANCHOR_X", 0.5),
            crop_anchor_y=_float_env("CROP_ANCHOR_Y", 0.5),
            webhook_url=_env("WEBHOOK_URL", ""),
            cover_provider=_env("COVER_PROVIDER", "openai"),
            cover_model=_env("COVER_MODEL", "gpt-image-2"),
            cover_count=_int_env("COVER_COUNT", 3),
            cover_aspects=_words_env("COVER_ASPECTS", "9:16,16:9"),
            cover_quality=_env("COVER_QUALITY", "medium"),
            cover_output_format=_env("COVER_OUTPUT_FORMAT", "jpeg"),
            cover_title_font=_env("COVER_TITLE_FONT", "Microsoft YaHei"),
            cover_base_url=_env("COVER_BASE_URL", "https://api.openai.com/v1"),
            cover_api_key=_env("COVER_API_KEY", ""),
            cover_http_referer=_env("COVER_HTTP_REFERER", ""),
            cover_app_title=_env("COVER_APP_TITLE", "Video Automation"),
            cover_modalities=_words_env("COVER_MODALITIES", "image,text"),
            openai_api_key=_env("OPENAI_API_KEY", ""),
            audio_separation_engine=_env("AUDIO_SEPARATION_ENGINE", "plan"),
            demucs_path=_portable_tool_path(root, "DEMUCS_PATH", "demucs.exe", "demucs"),
            demucs_model=_env("DEMUCS_MODEL", "htdemucs"),
            demucs_device=_env("DEMUCS_DEVICE", "auto"),
            audio_separation_timeout_seconds=max(60, _int_env("AUDIO_SEPARATION_TIMEOUT_SECONDS", 7200)),
            uvr_path=_optional_path("UVR_PATH"),
        )


def _optional_path(name: str) -> Path | None:
    raw = _env(name, "")
    return Path(raw) if raw else None


def _words_env(name: str, default: str) -> tuple[str, ...]:
    raw = _env(name, default)
    words = []
    for item in raw.replace("\n", ",").replace("\uff0c", ",").split(","):
        value = item.strip()
        if value:
            words.append(value)
    return tuple(dict.fromkeys(words))


def _replacements_env(name: str) -> tuple[tuple[str, str], ...]:
    raw = _env(name, "")
    pairs: list[tuple[str, str]] = []
    for item in raw.replace("\n", ",").replace("\uff0c", ",").split(","):
        value = item.strip()
        if not value or "=>" not in value:
            continue
        source, target = value.split("=>", 1)
        source = source.strip()
        target = target.strip()
        if source:
            pairs.append((source, target))
    return tuple(dict.fromkeys(pairs))
