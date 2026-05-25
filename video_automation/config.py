from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_WHISPER_INITIAL_PROMPT = (
    "\u4ee5\u4e0b\u662f\u4e2d\u6587\u76f4\u64ad\u5f55\u64ad\uff0c"
    "\u53ef\u80fd\u5305\u542b\u4e3b\u64ad\u540d\u3001\u6e38\u620f\u672f\u8bed\u3001"
    "\u5f39\u5e55\u53e3\u8bed\u548c\u7f51\u7edc\u7528\u8bed\u3002"
)
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


def _env(name: str, default: str = "") -> str:
    env_path = PROJECT_ROOT / ".env"
    example_path = PROJECT_ROOT / ".env.example"
    file_values = {**_load_env_file(example_path), **_load_env_file(env_path)}
    env_value = os.environ.get(name)
    if env_value is not None:
        return env_value
    return file_values.get(name) or default


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
    input_downloads_dir: Path
    jobs_dir: Path
    logs_dir: Path
    ffmpeg_path: Path
    ffprobe_path: Path
    audiowaveform_path: Path
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
    transcribe_audio_filter: str
    profanity_words: tuple[str, ...]
    subtitle_replacements: tuple[tuple[str, str], ...]
    subtitle_censor_replacement: str
    subtitle_min_duration_seconds: float
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
    api_allowed_origins: tuple[str, ...]
    download_enabled: bool
    llm_provider: str
    llm_model: str
    publish_enabled: bool
    publish_providers: tuple[str, ...]
    ytdlp_path: Path
    export_platforms: tuple[str, ...]
    render_video_encoder: str
    render_nvenc_preset: str
    render_nvenc_cq: int
    render_nvenc_preview_preset: str
    render_nvenc_preview_cq: int
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
    openai_api_key: str
    uvr_path: Path | None
    face_swap_path: Path | None
    aifacecover_root: Path | None

    @classmethod
    def load(cls) -> "Settings":
        root = Path(_env("VIDEO_AUTOMATION_ROOT", str(PROJECT_ROOT))).expanduser()
        return cls(
            root=root,
            input_recordings_dir=Path(_env("INPUT_RECORDINGS_DIR", str(root / "input" / "recordings"))),
            input_downloads_dir=Path(_env("INPUT_DOWNLOADS_DIR", str(root / "input" / "downloads"))),
            jobs_dir=Path(_env("JOBS_DIR", str(root / "processing" / "jobs"))),
            logs_dir=Path(_env("LOGS_DIR", str(root / "logs"))),
            ffmpeg_path=Path(_env("FFMPEG_PATH", "ffmpeg")),
            ffprobe_path=Path(_env("FFPROBE_PATH", "ffprobe")),
            audiowaveform_path=Path(_env("AUDIOWAVEFORM_PATH", "audiowaveform")),
            whisper_bin=Path(_env("WHISPER_BIN", "whisper")),
            whisper_backend=_env("WHISPER_BACKEND", "faster-whisper"),
            whisper_model=_env("WHISPER_MODEL", "large-v3"),
            whisper_model_fallbacks=_words_env("WHISPER_MODEL_FALLBACKS", "large-v3-turbo,medium"),
            whisper_language=_env("WHISPER_LANGUAGE", "zh"),
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
            transcribe_audio_filter=_env("TRANSCRIBE_AUDIO_FILTER", ""),
            profanity_words=_words_env("PROFANITY_WORDS", DEFAULT_PROFANITY_WORDS),
            subtitle_replacements=_replacements_env("SUBTITLE_REPLACEMENTS"),
            subtitle_censor_replacement=_env("SUBTITLE_CENSOR_REPLACEMENT", DEFAULT_SUBTITLE_CENSOR_REPLACEMENT),
            subtitle_min_duration_seconds=_float_env("SUBTITLE_MIN_DURATION_SECONDS", 0.3),
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
            api_allowed_origins=_words_env("API_ALLOWED_ORIGINS", ""),
            download_enabled=_bool_env("DOWNLOAD_ENABLED", False),
            llm_provider=_env("LLM_PROVIDER", "openai"),
            llm_model=_env("LLM_MODEL", ""),
            publish_enabled=_bool_env("PUBLISH_ENABLED", False),
            publish_providers=_words_env("PUBLISH_PROVIDERS", ""),
            ytdlp_path=Path(_env("YTDLP_PATH", "yt-dlp")),
            export_platforms=_words_env("EXPORT_PLATFORMS", "douyin,bilibili,youtube_shorts"),
            render_video_encoder=_env("RENDER_VIDEO_ENCODER", "libx264"),
            render_nvenc_preset=_env("RENDER_NVENC_PRESET", "p5"),
            render_nvenc_cq=max(1, _int_env("RENDER_NVENC_CQ", 21)),
            render_nvenc_preview_preset=_env("RENDER_NVENC_PREVIEW_PRESET", "p4"),
            render_nvenc_preview_cq=max(1, _int_env("RENDER_NVENC_PREVIEW_CQ", 25)),
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
            openai_api_key=_env("OPENAI_API_KEY", ""),
            uvr_path=_optional_path("UVR_PATH"),
            face_swap_path=_optional_path("FACE_SWAP_PATH"),
            aifacecover_root=_optional_path("AIFACECOVER_ROOT"),
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
