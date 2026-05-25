# FunASR 集成 Plan

## 背景

当前 video-automation 项目使用 faster-whisper 做中文语音转写，准确率偏低。
Whisper 是英文为主训练的模型，中文是"顺带支持"，微调天花板有限。
FunASR Paraformer-zh 专为中文优化，开箱准确率 90%+，自带标点恢复和热词支持。

## 目标

在项目中新增 `funasr` backend，与现有 `faster-whisper` / `cli` 并列，
用户通过 `.env` 切换，输出格式完全兼容，下游字幕/剪辑逻辑零改动。

## 影响范围

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| video_automation/config.py | 修改 | 新增 FunASR 配置项 |
| video_automation/transcribe.py | 修改 | 新增 funasr backend 分支 |
| video_automation/transcribe_runner.py | 修改 | 支持 funasr 子进程模式 |
| video_automation/worker.py | 修改 | health check / settings payload 补充 funasr |
| .env.example | 修改 | 补充 FunASR 配置示例 |
| requirements-optional.txt | 修改 | 新增 funasr 依赖 |

下游文件（subtitles.py, cuts.py, render.py 等）**不改动**，
因为它们只消费 transcript.json 的标准 segments 格式。

## 详细改动

### 1. config.py — 新增配置项

在 Settings dataclass 中新增：

```python
# FunASR 配置
funasr_model: str              # 默认 "paraformer-zh"
funasr_device: str             # 默认 "cpu"，可选 "cuda"
funasr_quantize: bool           # 默认 False，是否 Int8 量化加速
funasr_hotwords: str            # 默认 ""，热词（逗号分隔）
```

在 Settings.load() 中新增对应环境变量读取：

```
FUNASR_MODEL       → funasr_model       默认 "paraformer-zh"
FUNASR_DEVICE      → funasr_device       默认 "cpu"
FUNASR_QUANTIZE    → funasr_quantize     默认 False
FUNASR_HOTWORDS    → funasr_hotwords     默认 ""
```

### 2. transcribe.py — 新增 funasr backend

在 `transcribe_audio()` 入口函数中增加分支：

```python
if settings.whisper_backend == "funasr":
    if os.environ.get("VIDEO_AUTOMATION_TRANSCRIBE_CHILD") == "1":
        transcribe_audio_funasr(settings, audio_path, txt_path, srt_path, json_path)
    else:
        _run_funasr_subprocess(settings, audio_path, job_dir)
    return
```

新增核心函数 `transcribe_audio_funasr()`：

```python
def transcribe_audio_funasr(settings, audio_path, txt_path, srt_path, json_path):
    from funasr import AutoModel

    model = AutoModel(
        model=settings.funasr_model,
        device=settings.funasr_device,
        # quantize 只在非 CPU 时生效
        quantize=settings.funasr_quantize if settings.funasr_device != "cpu" else False,
    )

    # 热词支持
    hotword_str = settings.funasr_hotwords.strip() or None

    result = model.generate(
        input=str(audio_path),
        hotword=hotword_str,
    )

    # 将 FunASR 输出转换为标准 segments 格式
    segments = []
    text_parts = []
    for item in result:
        # FunASR 返回带时间戳的 segment
        # 每个 item 包含: text, timestamp ([[start_ms, end_ms], ...])
        text = _postprocess_text(item["text"].strip(), settings)
        text_parts.append(text)
        timestamps = item.get("timestamp", [])
        if timestamps:
            for idx, (start_ms, end_ms) in enumerate(timestamps):
                seg_text = ...  # 按 timestamp 对齐拆分
                segments.append({
                    "id": idx,
                    "start": round(start_ms / 1000, 3),
                    "end": round(end_ms / 1000, 3),
                    "text": seg_text,
                })
        else:
            # 无时间戳时作为单 segment
            segments.append({
                "id": 0,
                "start": 0.0,
                "end": 0.0,  # 未知时长
                "text": text,
            })

    # 写出标准格式（与 faster-whisper 完全一致）
    write_text_atomic(txt_path, "\n".join(text_parts))
    write_text_atomic(srt_path, _segments_to_srt(segments))
    write_json_atomic(json_path, {
        "text": "\n".join(text_parts),
        "segments": segments,
        "language": "zh",
        "duration": None,
        "backend": "funasr",
        "model": settings.funasr_model,
        "device": settings.funasr_device,
        "hotwords": settings.funasr_hotwords,
    })
```

关键点：
- FunASR 的 Paraformer-zh 返回的 timestamp 是毫秒级 [[start, end], ...]
- 需要把 timestamp 对应到 text 子串（FunASR 返回的 text 和 timestamp 是对齐的）
- 无 timestamp 的情况下降级为单 segment（某些小模型可能不返回时间戳）
- 长音频需分段处理：FunASR 内置 VAD，AutoModel.generate() 自动处理，
  但对于超长音频（>30min），考虑手动切片后逐段转写再合并

新增 `_run_funasr_subprocess()` 函数：

```python
def _run_funasr_subprocess(settings, audio_path, job_dir):
    # 与 _run_faster_whisper_subprocess 结构一致
    # 调用 transcribe_runner.py，传入 --backend funasr
    python_executable = _project_python(settings)
    command = [
        str(python_executable),
        "-m", "video_automation.transcribe_runner",
        "--audio", str(audio_path),
        "--job-dir", str(job_dir),
        "--backend", "funasr",
    ]
    env = os.environ.copy()
    env["VIDEO_AUTOMATION_TRANSCRIBE_CHILD"] = "1"
    result = subprocess.run(command, ...)
    ...
```

### 3. transcribe_runner.py — 支持 funasr 模式

当前只支持 faster-whisper，需要扩展：

```python
parser.add_argument("--backend", default="faster-whisper",
                    choices=["faster-whisper", "funasr"])

# main() 中增加分支
if args.backend == "funasr":
    from .transcribe import transcribe_audio_funasr
    transcribe_audio_funasr(settings, args.audio, ...)
else:
    # 原有 faster-whisper 逻辑
    transcribe_audio_faster_whisper(settings, args.audio, ...)
```

### 4. worker.py — health check 和 settings 补充

在 `_transcription_runtime_checks()` 中增加 funasr 分支：

```python
if settings.whisper_backend == "funasr":
    funasr_exists = importlib.util.find_spec("funasr") is not None
    checks.append({...})  # 类似 faster_whisper 的检查
    return checks
```

在 `_settings_payload()` 的 whisper 区块中补充 funasr 字段。

### 5. .env.example — 新增配置示例

```
# FunASR backend (alternative to faster-whisper, better Chinese accuracy)
# WHISPER_BACKEND=funasr
# FUNASR_MODEL=paraformer-zh
# FUNASR_DEVICE=cpu
# FUNASR_QUANTIZE=false
# FUNASR_HOTWORDS=酒馆战棋,炉石传说
```

### 6. requirements-optional.txt — 新增依赖

```
funasr>=1.0.0
```

注意：funasr 依赖较重（含 torch、modelscope 等），放在 optional 而非核心依赖。
用户选择 funasr backend 时才需要安装。

## 输出格式兼容性

transcript.json 的 segments 格式完全一致：

```json
{
  "text": "完整文本",
  "segments": [
    {"id": 0, "start": 0.0, "end": 1.5, "text": "片段文本"},
    ...
  ],
  "language": "zh",
  "backend": "funasr",
  "model": "paraformer-zh"
}
```

下游 subtitles.py 的 `_segments_from_transcript()` 只读 `start`/`end`/`text`，
所以完全兼容，无需改动。

## FunASR 特有优势

1. **热词支持** — 通过 FUNASR_HOTWORDS 配置，不用微调就能提升专业词汇识别率
   （比如你现在 .env 里有 `酒馆占棋=>酒馆战棋` 的替换规则，
   用热词 `酒馆战棋` 直接从源头纠正，比事后替换更可靠）

2. **标点恢复** — Paraformer-zh 自带标点模型，输出带逗号句号，
   对字幕断句有帮助

3. **模型小** — paraformer-zh 约 220M 参数，CPU 模式下比 whisper-medium 快

## 潜在风险和注意事项

1. **首次启动慢** — FunASR 会从 ModelScope 自动下载模型（~1GB），
   需要网络。后续会缓存到 `~/.cache/modelscope/`

2. **torch 依赖冲突** — funasr 依赖 torch，如果项目已有 torch 版本需注意兼容。
   当前项目 requirements.txt 里没有 torch，问题不大。

3. **长音频** — FunASR AutoModel 内置 VAD 分段，实测 30min 以内没问题。
   超长音频如遇 OOM，可考虑手动 ffmpeg 切片后逐段转写。

4. **子进程隔离** — faster-whisper 用子进程是因为 CTranslate2 CUDA 崩溃。
   FunASR 没有这个问题，但保持一致的子进程模式更安全
   （内存隔离、崩溃不影响主进程）。

5. **Windows 兼容** — 项目在 Windows 上运行，funasr 在 Windows 上可用，
   但 torch 的 CUDA 支持需要对应版本的 CUDA toolkit 已安装。

## 切换方式

用户只需改一行 .env：

```bash
# 原来
WHISPER_BACKEND=faster-whisper

# 切换到 FunASR
WHISPER_BACKEND=funasr
```

## 实施步骤

1. config.py 加 FunASR 配置项和 env 读取
2. transcribe.py 加 transcribe_audio_funasr() 和子进程入口
3. transcribe_runner.py 扩展 --backend 参数
4. worker.py 补充 health check 和 settings payload
5. .env.example 和 requirements-optional.txt 更新
6. 用一段实际音频测试，验证输出格式兼容

预计改动量：~150 行新增代码，0 行下游改动。
