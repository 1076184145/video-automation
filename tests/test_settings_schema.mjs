import assert from "node:assert/strict";
import test from "node:test";

globalThis.localStorage = {
  getItem() {
    return "zh";
  },
  setItem() {},
};

Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { language: "zh-CN" },
});

globalThis.window = {
  addEventListener() {},
  dispatchEvent() {},
};

const {
  settingDisplayValue,
  settingEnvLabel,
  settingKeyLabel,
  settingOptionLabel,
  settingRecommendation,
} = await import("../web/js/settings-schema.js");
const { t } = await import("../web/js/i18n.js");
const {
  normalizeSettingsMessage,
  firstInvalidChangedSettingsControl,
  recommendedSettingsUpdates,
  renderEditableGroup,
  renderSettingsSnapshot,
} = await import("../web/js/settings.js");

test("editable settings use creator-facing Chinese labels", () => {
  assert.equal(settingEnvLabel("WHISPER_BACKEND"), "转写后端");
  assert.equal(settingEnvLabel("FASTER_WHISPER_COMPUTE_TYPE"), "Faster-Whisper 计算精度");
  assert.equal(settingEnvLabel("COVER_API_KEY"), "封面 API Key");
  assert.equal(
    t("settings.edit_ai_note"),
    "这里配置字幕翻译、语义高光、标题简介等文本 AI 的 LLM_MODEL，也配置封面生成。所有 AI 功能仅使用你自行配置的第三方 API Key。",
  );
});

test("settings route match arrays are not rendered as save messages", () => {
  assert.equal(normalizeSettingsMessage(["/settings"]), "");
  assert.equal(normalizeSettingsMessage("配置已保存"), "配置已保存");
});

test("settings groups stay collapsed until the user opens one", () => {
  const group = {
    title: "settings.edit_whisper",
    fields: [{ env: "WHISPER_MODEL", path: ["whisper", "model"] }],
  };
  const settings = { whisper: { model: "medium" } };
  const first = renderEditableGroup(group, settings, [], 0);
  const later = renderEditableGroup(group, settings, [], 1);

  assert.doesNotMatch(first, / open>/);
  assert.match(first, /<summary class="settings-edit-summary">/);
  assert.doesNotMatch(later, / open>/);
});

test("AI settings expose LLM_MODEL before cover-specific fields", () => {
  const group = {
    title: "settings.edit_ai",
    note: "settings.edit_ai_note",
    fields: [
      { env: "LLM_PROVIDER", path: ["optional_modules", "llm_provider"], type: "select", options: ["openai", "openai-compatible", "google"] },
      { env: "LLM_MODEL", path: ["optional_modules", "llm_model"], placeholder: "settings.placeholder.llm_model" },
      { env: "COVER_MODEL", path: ["covers", "model"] },
    ],
  };
  const html = renderEditableGroup(group, {
    optional_modules: { llm_provider: "google", llm_model: "" },
    covers: { model: "imagen-4.0-generate-preview-06-06" },
  }, [], 0);

  assert.match(html, /文本 AI 模型/);
  assert.match(html, /例如 gemini-2\.5-flash/);
  assert.ok(html.indexOf("LLM_MODEL") < html.indexOf("COVER_MODEL"));
});

test("recommended settings updates include only editable values that differ", () => {
  const editable = [{
    title: "settings.edit_whisper",
    fields: [
      { env: "WHISPER_MODEL", path: ["whisper", "model"] },
      { env: "WHISPER_LANGUAGE", path: ["whisper", "language"] },
      { env: "WHISPER_INITIAL_PROMPT", path: ["whisper", "initial_prompt"] },
    ],
  }];
  const settings = {
    whisper: {
      model: "small",
      language: "zh",
      initial_prompt: "保留这个用户提示",
    },
  };
  const checks = [{ name: "ctranslate2_cuda", exists: true }];

  assert.deepEqual(recommendedSettingsUpdates(editable, settings, checks), {
    WHISPER_MODEL: "medium",
  });
});

test("complete settings snapshot is collapsed and reports its item count", () => {
  const html = renderSettingsSnapshot({
    directories: { project_root: "D:\\video-automation" },
    api: { host: "127.0.0.1", port: 8765 },
  }, []);

  assert.match(html, /<details class="settings-snapshot">/);
  assert.doesNotMatch(html, /settings-snapshot" open/);
  assert.match(html, /高级配置快照/);
  assert.match(html, /3 项/);
});

test("settings validation ignores unchanged hidden values but catches changed invalid values", () => {
  const unchangedInvalid = {
    type: "number",
    value: "0",
    checked: false,
    dataset: { env: "VALUE", original: "0" },
    checkValidity: () => false,
  };
  const changedInvalid = {
    type: "number",
    value: "-1",
    checked: false,
    dataset: { env: "VALUE", original: "0" },
    checkValidity: () => false,
  };

  assert.equal(firstInvalidChangedSettingsControl([unchangedInvalid]), null);
  assert.equal(firstInvalidChangedSettingsControl([changedInvalid]), changedInvalid);
});

test("select options are localized without changing their stored values", () => {
  assert.equal(settingOptionLabel("WHISPER_BACKEND", "funasr-whisper"), "FunASR 优先，Whisper 回退");
  assert.equal(settingOptionLabel("WHISPER_LANGUAGE", "auto"), "自动检测");
  assert.equal(settingOptionLabel("COVER_PROVIDER", "openrouter"), "OpenRouter");
  assert.equal(settingOptionLabel("COVER_PROVIDER", "google"), "Google Gemini");
  assert.equal(settingOptionLabel("LLM_PROVIDER", "google"), "Google Gemini");
  assert.equal(settingOptionLabel("RENDER_VIDEO_ENCODER", "h264_nvenc"), "NVIDIA NVENC 硬件编码");
});

test("read-only settings keys and boolean values are localized", () => {
  assert.equal(settingKeyLabel("directories", "project_root"), "项目根目录");
  assert.equal(settingKeyLabel("detection", "source_integrity_scan_enabled"), "源文件完整性扫描");
  assert.equal(settingKeyLabel("covers", "cover_api_key_configured"), "封面 API Key 已配置");
  assert.equal(settingDisplayValue("api", "batch_limit", 30), "30");
  assert.equal(
    settingDisplayValue("subtitles", "replacements", [{ source: "错词", target: "正词" }]),
    '[{"source":"错词","target":"正词"}]',
  );
});

test("every current health settings key has a Chinese display label", () => {
  const keys = {
    directories: ["project_root", "input_recordings", "job_outputs", "logs"],
    paths: ["ffmpeg", "ffprobe", "whisper", "audiowaveform", "demucs"],
    whisper: [
      "backend", "model", "model_fallbacks", "language", "initial_prompt",
      "faster_whisper_device", "faster_whisper_compute_type", "faster_whisper_batch_size",
      "funasr_model", "funasr_vad_model", "funasr_punc_model", "funasr_device",
      "funasr_hotwords", "funasr_batch_size_s", "funasr_max_segment_ms",
      "word_timestamps", "vad_filter", "transcribe_audio_filter",
    ],
    detection: [
      "silence_threshold_db", "silence_min_length", "silence_min_gap",
      "cut_min_clip_seconds", "cut_merge_gap_seconds", "freeze_noise_db",
      "freeze_min_duration", "scene_threshold", "source_integrity_scan_enabled",
      "source_integrity_scan_timeout_multiplier", "source_integrity_scan_max_errors",
      "visual_detect_keyframes_only", "visual_detect_fps", "visual_detect_width",
    ],
    subtitles: [
      "preset", "font_name", "font_size", "primary_color", "outline_color",
      "back_color", "outline", "shadow", "alignment", "margin_v", "max_lines",
      "vertical_font_size", "censor_replacement", "replacements", "min_duration_seconds",
    ],
    api: ["host", "port", "parallel_jobs", "batch_limit", "recording_upload_max_bytes", "allowed_origins"],
    exports: [
      "platforms", "render_video_encoder", "render_output_fps", "render_nvenc_preset",
      "render_nvenc_cq", "render_nvenc_preview_preset", "render_nvenc_preview_cq",
      "web_preview_enabled", "web_preview_max_width", "web_preview_max_height",
      "web_preview_fps", "web_preview_video_bitrate", "bgm_path", "bgm_volume",
      "source_audio_volume", "webhook_url",
    ],
    optional_modules: [
      "llm_provider", "llm_model",
      "google_base_url", "google_api_key_configured",
      "native_waveform_enabled", "native_cuts_enabled", "high_quality_audio_enabled",
      "llm_translation_batch_size", "llm_translation_batch_chars",
      "audio_separation_engine", "demucs_model", "demucs_device",
      "audio_separation_timeout_seconds", "publish_enabled", "publish_providers",
    ],
    crop: ["vertical_mode", "anchor_x", "anchor_y"],
    covers: [
      "provider", "base_url", "model", "count", "aspects", "quality", "output_format",
      "title_font", "cover_api_key_configured", "openai_api_key_configured",
      "http_referer", "app_title", "modalities",
    ],
  };

  for (const [group, names] of Object.entries(keys)) {
    for (const key of names) {
      assert.match(settingKeyLabel(group, key), /[\u3400-\u9fff]/, `${group}.${key}`);
    }
  }
});

test("dynamic recommendations prefer detected CUDA and NVENC capabilities", () => {
  const checks = [
    { name: "torch_cuda", exists: true },
    { name: "ctranslate2_cuda", exists: true },
    { name: "h264_nvenc", exists: true },
    { name: "funasr", exists: true },
  ];

  const device = settingRecommendation({
    env: "FASTER_WHISPER_DEVICE",
    group: "whisper",
    key: "faster_whisper_device",
    value: "cpu",
    checks,
  });
  assert.equal(device.recommended, "cuda");
  assert.equal(device.matches, false);
  assert.match(device.text, /CUDA|显卡/);

  const encoder = settingRecommendation({
    env: "RENDER_VIDEO_ENCODER",
    group: "exports",
    key: "render_video_encoder",
    value: "libx264",
    checks,
  });
  assert.equal(encoder.recommended, "h264_nvenc");
  assert.match(encoder.text, /NVENC/);

  const model = settingRecommendation({
    env: "WHISPER_MODEL",
    group: "whisper",
    key: "model",
    value: "small",
    checks,
  });
  assert.match(model.text, /Medium 模型/);
});

test("dynamic recommendations fall back to CPU-safe values", () => {
  const checks = [
    { name: "torch_cuda", exists: false },
    { name: "ctranslate2_cuda", exists: false },
    { name: "h264_nvenc", exists: false },
    { name: "funasr", exists: false },
  ];

  assert.equal(settingRecommendation({
    group: "whisper",
    key: "faster_whisper_compute_type",
    value: "float16",
    checks,
  }).recommended, "int8");
  assert.equal(settingRecommendation({
    group: "exports",
    key: "render_video_encoder",
    value: "h264_nvenc",
    checks,
  }).recommended, "libx264");
  assert.equal(settingRecommendation({
    group: "whisper",
    key: "backend",
    value: "funasr",
    checks,
  }).recommended, "faster-whisper");
});

test("missing optional tools produce install-or-disable guidance", () => {
  const recommendation = settingRecommendation({
    group: "optional_modules",
    key: "audio_separation_engine",
    value: "demucs",
    checks: [{ name: "demucs", exists: false }],
  });
  assert.equal(recommendation.recommended, "plan");
  assert.match(recommendation.text, /健康|安装|未检测/);
});

test("recommendations preserve content-specific user dictionaries and prompts", () => {
  const prompt = "忽略背景音乐，只转写主要说话人";
  const promptRecommendation = settingRecommendation({
    group: "whisper",
    key: "initial_prompt",
    value: prompt,
    checks: [],
  });
  assert.equal(promptRecommendation.recommended, prompt);
  assert.equal(promptRecommendation.matches, true);

  const replacements = [{ source: "酒馆占棋", target: "酒馆战棋" }];
  const replacementRecommendation = settingRecommendation({
    group: "subtitles",
    key: "replacements",
    value: replacements,
    checks: [],
  });
  assert.deepEqual(replacementRecommendation.recommended, replacements);
  assert.equal(replacementRecommendation.matches, true);
});

test("resolver keeps automatic fallback and missing Demucs avoids a CUDA recommendation", () => {
  const checks = [
    { name: "demucs", exists: false },
    { name: "torch_cuda", exists: true },
  ];
  assert.equal(settingRecommendation({
    group: "optional_modules",
    key: "demucs_device",
    value: "auto",
    checks,
  }).recommended, "auto");
});

test("every current health setting receives a recommendation", () => {
  const keys = {
    directories: ["project_root", "input_recordings", "job_outputs", "logs"],
    paths: ["ffmpeg", "ffprobe", "whisper", "audiowaveform", "demucs"],
    whisper: [
      "backend", "model", "model_fallbacks", "language", "initial_prompt",
      "faster_whisper_device", "faster_whisper_compute_type", "faster_whisper_batch_size",
      "funasr_model", "funasr_vad_model", "funasr_punc_model", "funasr_device",
      "funasr_hotwords", "funasr_batch_size_s", "funasr_max_segment_ms",
      "word_timestamps", "vad_filter", "transcribe_audio_filter",
    ],
    detection: [
      "silence_threshold_db", "silence_min_length", "silence_min_gap",
      "cut_min_clip_seconds", "cut_merge_gap_seconds", "freeze_noise_db",
      "freeze_min_duration", "scene_threshold", "source_integrity_scan_enabled",
      "source_integrity_scan_timeout_multiplier", "source_integrity_scan_max_errors",
      "visual_detect_keyframes_only", "visual_detect_fps", "visual_detect_width",
    ],
    subtitles: [
      "preset", "font_name", "font_size", "primary_color", "outline_color",
      "back_color", "outline", "shadow", "alignment", "margin_v", "max_lines",
      "vertical_font_size", "censor_replacement", "replacements", "min_duration_seconds",
    ],
    api: ["host", "port", "parallel_jobs", "batch_limit", "recording_upload_max_bytes", "allowed_origins"],
    exports: [
      "platforms", "render_video_encoder", "render_output_fps", "render_nvenc_preset",
      "render_nvenc_cq", "render_nvenc_preview_preset", "render_nvenc_preview_cq",
      "web_preview_enabled", "web_preview_max_width", "web_preview_max_height",
      "web_preview_fps", "web_preview_video_bitrate", "bgm_path", "bgm_volume",
      "source_audio_volume", "webhook_url",
    ],
    optional_modules: [
      "llm_provider", "llm_model",
      "google_base_url", "google_api_key_configured",
      "llm_translation_batch_size", "llm_translation_batch_chars",
      "audio_separation_engine", "demucs_model", "demucs_device",
      "audio_separation_timeout_seconds", "publish_enabled", "publish_providers",
    ],
    crop: ["vertical_mode", "anchor_x", "anchor_y"],
    covers: [
      "provider", "base_url", "model", "count", "aspects", "quality", "output_format",
      "title_font", "cover_api_key_configured", "openai_api_key_configured",
      "http_referer", "app_title", "modalities",
    ],
  };

  for (const [group, names] of Object.entries(keys)) {
    for (const key of names) {
      const recommendation = settingRecommendation({
        group,
        key,
        value: "",
        checks: [],
      });
      assert.ok(recommendation.text, `${group}.${key}`);
      assert.match(recommendation.text, /[\u3400-\u9fff]/, `${group}.${key}`);
    }
  }
});
