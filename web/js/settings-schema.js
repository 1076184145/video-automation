import { t } from "./i18n.js";

const ENV_KEY_MAP = {
  WHISPER_BACKEND: "backend",
  WHISPER_MODEL: "whisper_model",
  WHISPER_MODEL_FALLBACKS: "model_fallbacks",
  WHISPER_LANGUAGE: "language",
  FASTER_WHISPER_DEVICE: "faster_whisper_device",
  FASTER_WHISPER_COMPUTE_TYPE: "faster_whisper_compute_type",
  FASTER_WHISPER_BATCH_SIZE: "faster_whisper_batch_size",
  WHISPER_WORD_TIMESTAMPS: "word_timestamps",
  WHISPER_VAD_FILTER: "vad_filter",
  WHISPER_INITIAL_PROMPT: "initial_prompt",
  TRANSCRIBE_AUDIO_FILTER: "transcribe_audio_filter",
  SILENCE_THRESHOLD_DB: "silence_threshold_db",
  SILENCE_MIN_LENGTH_SECONDS: "silence_min_length",
  SILENCE_MIN_GAP_SECONDS: "silence_min_gap",
  CUT_MIN_CLIP_SECONDS: "cut_min_clip_seconds",
  CUT_MERGE_GAP_SECONDS: "cut_merge_gap_seconds",
  SCENE_THRESHOLD: "scene_threshold",
  SOURCE_INTEGRITY_SCAN_ENABLED: "source_integrity_scan_enabled",
  ASS_PRESET: "preset",
  ASS_FONT_NAME: "font_name",
  ASS_FONT_SIZE: "font_size",
  ASS_VERTICAL_FONT_SIZE: "vertical_font_size",
  ASS_MAX_LINES: "max_lines",
  ASS_MARGIN_V: "margin_v",
  ASS_OUTLINE: "outline",
  ASS_SHADOW: "shadow",
  SUBTITLE_MIN_DURATION_SECONDS: "min_duration_seconds",
  SUBTITLE_CENSOR_REPLACEMENT: "censor_replacement",
  RENDER_VIDEO_ENCODER: "render_video_encoder",
  RENDER_OUTPUT_FPS: "render_output_fps",
  RENDER_X264_PRESET: "render_x264_preset",
  RENDER_X264_CRF: "render_x264_crf",
  RENDER_NVENC_CQ: "render_nvenc_cq",
  RENDER_NVENC_PREVIEW_CQ: "render_nvenc_preview_cq",
  WEB_PREVIEW_ENABLED: "web_preview_enabled",
  WEB_PREVIEW_MAX_WIDTH: "web_preview_max_width",
  WEB_PREVIEW_MAX_HEIGHT: "web_preview_max_height",
  WEB_PREVIEW_FPS: "web_preview_fps",
  WEB_PREVIEW_VIDEO_BITRATE: "web_preview_video_bitrate",
  BGM_VOLUME: "bgm_volume",
  SOURCE_AUDIO_VOLUME: "source_audio_volume",
  COVER_PROVIDER: "provider",
  COVER_BASE_URL: "base_url",
  COVER_MODEL: "cover_model",
  COVER_COUNT: "count",
  COVER_QUALITY: "quality",
  COVER_OUTPUT_FORMAT: "output_format",
  COVER_API_KEY: "cover_api_key",
  OPENAI_API_KEY: "openai_api_key",
  GOOGLE_API_KEY: "google_api_key",
  GOOGLE_BASE_URL: "google_base_url",
  COVER_HTTP_REFERER: "http_referer",
  COVER_APP_TITLE: "app_title",
  LLM_PROVIDER: "llm_provider",
  LLM_MODEL: "llm_model",
  API_BATCH_LIMIT: "batch_limit",
  RECORDING_UPLOAD_MAX_BYTES: "recording_upload_max_bytes",
  NATIVE_WAVEFORM_ENABLED: "native_waveform_enabled",
  NATIVE_CUTS_ENABLED: "native_cuts_enabled",
  HIGH_QUALITY_AUDIO_ENABLED: "high_quality_audio_enabled",
};

const STATIC_RECOMMENDATIONS = {
  "whisper.model_fallbacks": "small",
  "whisper.language": "zh",
  "whisper.initial_prompt": "",
  "whisper.funasr_model": "paraformer-zh",
  "whisper.funasr_vad_model": "fsmn-vad",
  "whisper.funasr_punc_model": "ct-punc",
  "whisper.funasr_hotwords": "",
  "whisper.funasr_max_segment_ms": 60000,
  "whisper.word_timestamps": true,
  "whisper.vad_filter": true,
  "whisper.transcribe_audio_filter": "",
  "detection.silence_threshold_db": -35,
  "detection.silence_min_length": 0.8,
  "detection.silence_min_gap": 0.35,
  "detection.cut_min_clip_seconds": 2,
  "detection.cut_merge_gap_seconds": 1.5,
  "detection.freeze_noise_db": -60,
  "detection.freeze_min_duration": 2,
  "detection.scene_threshold": 0.3,
  "detection.source_integrity_scan_enabled": true,
  "detection.source_integrity_scan_timeout_multiplier": 3,
  "detection.source_integrity_scan_max_errors": 40,
  "detection.visual_detect_keyframes_only": true,
  "detection.visual_detect_fps": 2,
  "detection.visual_detect_width": 480,
  "subtitles.preset": "classic",
  "subtitles.font_name": "Microsoft YaHei",
  "subtitles.font_size": 56,
  "subtitles.primary_color": "&H00FFFFFF",
  "subtitles.outline_color": "&H00000000",
  "subtitles.back_color": "&H64000000",
  "subtitles.outline": 3,
  "subtitles.shadow": 1,
  "subtitles.alignment": 2,
  "subtitles.margin_v": 90,
  "subtitles.max_lines": 2,
  "subtitles.vertical_font_size": 44,
  "subtitles.censor_replacement": "[哔]",
  "subtitles.replacements": "",
  "subtitles.min_duration_seconds": 0.3,
  "api.host": "127.0.0.1",
  "api.port": 8765,
  "api.batch_limit": 30,
  "api.recording_upload_max_bytes": 21474836480,
  "api.allowed_origins": "",
  "exports.platforms": "douyin, bilibili, youtube_shorts",
  "exports.render_output_fps": 30,
  "exports.render_x264_preset": "medium",
  "exports.render_x264_crf": 0,
  "exports.render_nvenc_preset": "p5",
  "exports.render_nvenc_cq": 21,
  "exports.render_nvenc_preview_preset": "p4",
  "exports.render_nvenc_preview_cq": 25,
  "exports.web_preview_enabled": true,
  "exports.web_preview_max_width": 960,
  "exports.web_preview_max_height": 960,
  "exports.web_preview_fps": 24,
  "exports.web_preview_video_bitrate": "1200k",
  "exports.bgm_path": "",
  "exports.bgm_volume": 0.16,
  "exports.source_audio_volume": 1,
  "exports.webhook_url": "",
  "optional_modules.llm_provider": "openai-compatible",
  "optional_modules.llm_model": "",
  "optional_modules.native_waveform_enabled": true,
  "optional_modules.native_cuts_enabled": true,
  "optional_modules.high_quality_audio_enabled": true,
  "optional_modules.llm_translation_batch_size": 24,
  "optional_modules.llm_translation_batch_chars": 6000,
  "optional_modules.demucs_model": "htdemucs",
  "optional_modules.audio_separation_timeout_seconds": 7200,
  "optional_modules.publish_enabled": false,
  "optional_modules.publish_providers": "",
  "crop.vertical_mode": "blur",
  "crop.anchor_x": 0.5,
  "crop.anchor_y": 0.5,
  "covers.count": 3,
  "covers.aspects": "9:16, 16:9",
  "covers.quality": "medium",
  "covers.output_format": "jpeg",
  "covers.title_font": "Microsoft YaHei",
  "covers.http_referer": "http://127.0.0.1:8765",
  "covers.app_title": "Video Automation",
  "covers.modalities": "image",
};

const GROUP_REASON_KEYS = {
  directories: "settings.recommendation.reason.directory",
  paths: "settings.recommendation.reason.tool_path",
  whisper: "settings.recommendation.reason.transcription",
  detection: "settings.recommendation.reason.detection",
  subtitles: "settings.recommendation.reason.subtitle",
  api: "settings.recommendation.reason.local_api",
  exports: "settings.recommendation.reason.export",
  optional_modules: "settings.recommendation.reason.optional",
  crop: "settings.recommendation.reason.crop",
  covers: "settings.recommendation.reason.cover",
};

export function settingEnvLabel(env) {
  return translated(`settings.key.${ENV_KEY_MAP[env] || env}`, env);
}

export function settingKeyLabel(group, key) {
  const groupLabel = translated(`settings.key.${group}.${key}`, "");
  return groupLabel || translated(`settings.key.${key}`, humanize(key));
}

export function settingOptionLabel(env, value) {
  const raw = String(value ?? "");
  const contextual = translated(`settings.option.${env}.${raw}`, "");
  if (contextual) return contextual;
  return translated(`settings.option.${raw}`, raw);
}

export function settingDisplayValue(group, key, value) {
  if (typeof value === "boolean") {
    if (key.endsWith("_configured")) {
      return t(value ? "settings.value.configured" : "settings.value.not_configured");
    }
    return t(value ? "settings.value.enabled" : "settings.value.disabled");
  }
  if (Array.isArray(value)) {
    return value.some((item) => item && typeof item === "object")
      ? JSON.stringify(value)
      : value.join(", ");
  }
  if (value == null) return "";
  const raw = String(value);
  const option = settingOptionLabel(`${group}.${key}`, raw);
  return option || raw;
}

export function settingRecommendation({
  env = "",
  group = "",
  key = "",
  value = "",
  checks = [],
} = {}) {
  const id = `${group}.${key}`;
  const capabilities = capabilityMap(checks);
  const rule = dynamicRecommendation(id, value, capabilities)
    || staticRecommendation(id)
    || fallbackRecommendation(group, value);
  const recommended = rule.recommended;
  const reason = t(rule.reasonKey || GROUP_REASON_KEYS[group] || "settings.recommendation.reason.general");
  const displayValue = recommendationDisplayValue(env, group, key, recommended, rule.valueKey);
  const matches = recommended == null || valuesMatch(value, recommended);
  const templateKey = recommended == null
    ? "settings.recommendation.advice_template"
    : "settings.recommendation.template";
  let text = interpolate(t(templateKey), {
    value: displayValue,
    reason,
  });
  if (!matches) {
    text += ` ${t("settings.recommendation.differs")}`;
  }
  return { recommended, reason, text, matches };
}

function dynamicRecommendation(id, currentValue, capabilities) {
  const hasTranscriptionCuda = capabilities.ctranslate2_cuda;
  const hasTorchCuda = capabilities.torch_cuda;
  const hasAnyCuda = hasTranscriptionCuda || hasTorchCuda;
  if (id === "whisper.initial_prompt" || id === "whisper.funasr_hotwords") {
    return {
      recommended: currentValue || "",
      reasonKey: "settings.recommendation.reason.content_specific",
    };
  }
  if (id === "subtitles.replacements") {
    return {
      recommended: currentValue || [],
      reasonKey: "settings.recommendation.reason.dictionary",
    };
  }
  if (id === "optional_modules.llm_provider") {
    return {
      recommended: currentValue || "openai-compatible",
      reasonKey: "settings.recommendation.reason.byok_provider",
    };
  }
  if (id === "optional_modules.llm_model") {
    return {
      recommended: currentValue || null,
      reasonKey: "settings.recommendation.reason.provider_specific",
    };
  }
  const rules = {
    "whisper.backend": "faster-whisper",
    "whisper.model": hasTranscriptionCuda ? "medium" : "small",
    "whisper.faster_whisper_device": hasTranscriptionCuda ? "cuda" : "cpu",
    "whisper.faster_whisper_compute_type": hasTranscriptionCuda ? "int8_float16" : "int8",
    "whisper.faster_whisper_batch_size": hasTranscriptionCuda ? 8 : 1,
    "whisper.funasr_device": hasTorchCuda ? "cuda:0" : "cpu",
    "whisper.funasr_batch_size_s": hasTorchCuda ? 300 : 60,
    "api.parallel_jobs": hasAnyCuda ? 1 : 2,
    "exports.render_video_encoder": capabilities.h264_nvenc ? "h264_nvenc" : "libx264",
    "optional_modules.audio_separation_engine": capabilities.demucs ? "demucs" : "plan",
    "optional_modules.demucs_device": capabilities.demucs ? (hasTorchCuda ? "cuda" : "cpu") : "auto",
  };
  if (Object.hasOwn(rules, id)) {
    return {
      recommended: rules[id],
      reasonKey: dynamicReasonKey(id, capabilities),
    };
  }
  if (id === "covers.provider") {
    return capabilities.cover_api_key
      ? {
          recommended: currentValue && currentValue !== "disabled" ? currentValue : "openai-compatible",
          reasonKey: "settings.recommendation.reason.byok_ready",
        }
      : {
          recommended: "disabled",
          reasonKey: "settings.recommendation.reason.byok_missing",
        };
  }
  if (id === "covers.base_url" || id === "covers.model") {
    return {
      recommended: currentValue || null,
      reasonKey: "settings.recommendation.reason.provider_specific",
      valueKey: currentValue ? "" : "settings.recommendation.value.provider_specific",
    };
  }
  if (id === "covers.cover_api_key_configured" || id === "covers.openai_api_key_configured" || id === "covers.google_api_key_configured") {
    return {
      recommended: null,
      reasonKey: "settings.recommendation.reason.byok",
    };
  }
  if (id === "paths.demucs" && !capabilities.demucs) {
    return missingToolRecommendation("demucs");
  }
  if (id === "paths.audiowaveform" && !capabilities.audiowaveform_path) {
    return missingToolRecommendation("audiowaveform");
  }
  return null;
}

function staticRecommendation(id) {
  if (!Object.hasOwn(STATIC_RECOMMENDATIONS, id)) return null;
  return {
    recommended: STATIC_RECOMMENDATIONS[id],
    reasonKey: GROUP_REASON_KEYS[id.split(".")[0]] || "settings.recommendation.reason.general",
  };
}

function fallbackRecommendation(group, currentValue) {
  if (group === "directories" || group === "paths") {
    return {
      recommended: currentValue || null,
      reasonKey: GROUP_REASON_KEYS[group],
      valueKey: currentValue ? "" : "settings.recommendation.value.auto_path",
    };
  }
  return {
    recommended: currentValue === "" ? null : currentValue,
    reasonKey: GROUP_REASON_KEYS[group] || "settings.recommendation.reason.general",
    valueKey: currentValue === "" ? "settings.recommendation.value.as_needed" : "",
  };
}

function missingToolRecommendation(tool) {
  return {
    recommended: null,
    reasonKey: "settings.recommendation.reason.tool_missing",
    valueKey: `settings.recommendation.value.${tool}`,
  };
}

function dynamicReasonKey(id, capabilities) {
  if (id === "exports.render_video_encoder") {
    return capabilities.h264_nvenc
      ? "settings.recommendation.reason.nvenc_available"
      : "settings.recommendation.reason.nvenc_missing";
  }
  if (id === "whisper.backend") {
    return "settings.recommendation.reason.funasr_missing";
  }
  if (id === "optional_modules.audio_separation_engine") {
    return capabilities.demucs
      ? "settings.recommendation.reason.demucs_available"
      : "settings.recommendation.reason.tool_missing";
  }
  if (id === "api.parallel_jobs") {
    return capabilities.ctranslate2_cuda || capabilities.torch_cuda
      ? "settings.recommendation.reason.gpu_parallel"
      : "settings.recommendation.reason.cpu_parallel";
  }
  return capabilities.ctranslate2_cuda || capabilities.torch_cuda
    ? "settings.recommendation.reason.cuda_available"
    : "settings.recommendation.reason.cuda_missing";
}

function capabilityMap(checks) {
  const result = {};
  for (const check of Array.isArray(checks) ? checks : []) {
    if (!check || !check.name) continue;
    result[String(check.name)] = Boolean(check.exists);
  }
  return result;
}

function recommendationDisplayValue(env, group, key, value, valueKey = "") {
  if (valueKey) return t(valueKey);
  if (value == null) return "";
  if (value === "") return t("settings.recommendation.value.leave_empty");
  if (Array.isArray(value)) return settingDisplayValue(group, key, value);
  if (typeof value === "boolean") return settingDisplayValue(group, key, value);
  return settingOptionLabel(env || `${group}.${key}`, value);
}

function valuesMatch(current, recommended) {
  if (Array.isArray(current)) {
    return JSON.stringify(current) === JSON.stringify(recommended);
  }
  if (typeof recommended === "boolean") {
    return Boolean(current) === recommended;
  }
  return String(current ?? "").trim() === String(recommended ?? "").trim();
}

function interpolate(template, values) {
  return Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value ?? "")),
    template,
  );
}

function translated(key, fallback) {
  const value = t(key);
  return value === key ? fallback : value;
}

function humanize(value) {
  return String(value || "")
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
