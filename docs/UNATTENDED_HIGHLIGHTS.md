# Unattended highlights

This opt-in mode produces independent 30–75 second, 1080×1920 MP4s. It does not
replace the default reviewed-cut workflow, alter existing `cuts.json`/`final.mp4`,
publish videos, or upload source media. **Enabling it sends transcript text to
the configured LLM**; use a local endpoint if the text must remain local.

```powershell
python run_worker.py --once "D:\path\video.mp4" --unattended-highlights --progress
```

For the existing local API, submit `POST /api/process` with a JSON body such as:

```json
{"path": "D:/path/video.mp4", "unattended_highlights": true}
```

`UNATTENDED_HIGHLIGHTS_ENABLED=false` is the default. Setting it to true enables
the mode for subsequent tasks; API `unattended_highlights: false` overrides that
default. `skip_transcribe` is incompatible. The new mode always transcribes,
detects silence, selects highlights and renders them; legacy render flags do not
disable these stages. Do not mix legacy recipe stages with the automatic stages.
Use `--force` to explicitly regenerate a previously completed job.

## Provider configuration

Keep configuration and credentials in your ignored local `.env`/OS keyring.
No model is downloaded and no provider is probed automatically by this feature.

| Provider | Settings |
| --- | --- |
| Existing OpenAI Responses | `LLM_PROVIDER=openai`, `LLM_MODEL=<available model>`, `OPENAI_API_KEY`; leave `LLM_OPENAI_BASE_URL` empty |
| DeepSeek-compatible | `LLM_PROVIDER=openai`, `LLM_OPENAI_BASE_URL=https://api.deepseek.com/v1`, `LLM_RESPONSE_FORMAT=json_object`, model ID and `OPENAI_API_KEY` for your provider |
| Ollama | `LLM_PROVIDER=openai`, `LLM_OPENAI_BASE_URL=http://127.0.0.1:11434/v1`, locally installed `LLM_MODEL`, `LLM_RESPONSE_FORMAT=json_schema`; loopback requires no key |
| Native Gemini | Existing `LLM_PROVIDER=google`, `LLM_MODEL`, `GOOGLE_API_KEY` |
| Existing managed llama-server | Existing `LLM_PROVIDER=local` and `LOCAL_LLM_*` settings |

`LLM_OPENAI_BASE_URL` is explicit, HTTPS-only except loopback; it changes all
OpenAI structured calls, not only highlight selection. Supported compatibility
formats are `json_schema` and `json_object`. Consult your provider for a currently
available model and supported format. The latter is still locally schema-validated.
Configured `LLM_FALLBACK_PROVIDER` still applies; leave it empty to prevent fallback.
`LLM_REQUEST_TIMEOUT_SECONDS` (default 90) bounds each compatibility HTTP request.
Network/429/5xx failures get at most two additional attempts with cooperative
backoff. Existing JSON repair retries remain controlled by `LLM_MAX_REPAIR_RETRIES`.
Authentication/configuration errors are not repeatedly retried by the evaluator.
Cancel/pause is checked between calls; an in-flight HTTP request can take its timeout.

## Optional semantic review

Set `HIGHLIGHT_LLM_CHECKER_ENABLED=true` in your ignored local `.env` before
starting a new unattended task (restart the CLI/API process to ensure it loads the
change). The default is **false**, so existing tasks add no review calls. This
switch only affects unattended highlights, not the legacy `highlights.json` action.
Queued tasks retain their captured configuration. It does not enable unattended
mode by itself or add a Web UI switch.

Review works with either the single-route evaluator or the optional multi-angle
graph below. Distinct overlapping candidates remain available until AFTER review.
Before review, only exact sentence-range + title duplicates are combined. A bounded
pool (`HIGHLIGHT_REVIEW_MAX_CANDIDATES`, default 36, max 150, effective minimum
`HIGHLIGHT_MAX_CLIPS`) controls cost; excess candidates are ranked by generator
score and omitted with a recorded count. After review, IoU > 0.5 suppresses
overlapping approved candidates and `HIGHLIGHT_MAX_CLIPS` limits final outputs.
`candidates.json.selection` explains review rejection, overlap, count-limit and
selection decisions. No interval is expanded/unioned during deduplication.

- The reviewer receives every selected sentence in full, the title, and one
  adjacent sentence on each side when available. Generator scores/reasons are
  withheld to reduce anchoring. Outside context cannot rescue a missing opening
  or ending. Calm, informative explanations can qualify; conflict is not required.
- Exact IDs, 30–75 second source duration, aligned timestamps and basic fields
  are checked in code. There is no sampled-transcript coverage threshold, keyword
  blacklist, or character-set similarity filter. Sentence IDs/titles/scores are
  not rewritten by the reviewer; it only passes/rejects candidates with reasons
  and supporting sentence IDs. Unknown, duplicate or missing verdict IDs, invalid
  statuses and unsupported evidence fail the review instead of silently passing.
- Requests are serial, with at most four candidates per batch. Complete prompts
  (system + user) respect `HIGHLIGHT_REQUEST_CHARS` and the existing conservative
  local-context cap. Oversized candidates/context fail before any review request,
  rather than truncating the conclusion. Review calls are additional paid/local
  inference work; batch count depends on candidate text size. Existing JSON
  repair/provider fallback and bounded transient retries apply. Invalid verdict
  identity/evidence fails the run and requires a retry, not an unbounded repair loop.
- `auto_clips/review.json` persists full reviewed text/context, per-candidate
  `pending`/`pass`/`reject` status, reasons, evidence and aggregate
  `running`/`complete`/`failed`/`paused`/`canceled` status. Provider error codes are
  saved without raw provider error bodies. Keep this report private like transcripts.
- Review errors stop evaluation and block downstream planning/rendering. A retry
  with unchanged inputs/config reuses generated candidates and finished review
  batches. `--force` regenerates both. Changed inputs/config/version invalidate
  checkpoints. A completed review rejecting every candidate is distinct from a
  failed review: it yields `no_qualifying_clips`, leaving the job needing review.

This is text-only editorial assessment, not audio/visual emotion recognition or a
guarantee of engagement. Filtering can reduce output count; the bounded pool is
not expanded automatically to replace every rejected candidate. No cloud calls run merely by adding the feature;
when enabled, the configured provider/fallback receives the full text described above.

## Optional multi-angle generation

Set `HIGHLIGHT_GRAPH_ENABLED=true` to replace only the unattended candidate generator
with three fixed perspectives: hook/insight, text-supported emotion, and
conflict/reveal. All perspectives retain the same sentence-ID contract, complete
subject, hook/details/payoff and 30–75 second duration requirements. The existing
`LLM_PROVIDER`, `LLM_MODEL` and explicit fallback are reused; three prompts are not
three independent models and do not guarantee better results. The graph does not
implicitly enable review; set `HIGHLIGHT_LLM_CHECKER_ENABLED=true` for the full flow.

```text
Full sentence IDs + overlapping windows
  -> hook / text emotion / conflict candidates
  -> exact-duplicate merge + bounded candidate pool
  -> optional full-text reviewer
  -> final temporal NMS + output limit
  -> existing RMS / silence compression / ASS / rendering
```

- Each perspective sees EVERY window, not a transcript sample or first 80 entries.
  Windows reserve space for graph instructions. At most 128 windows are allowed;
  oversized inputs fail before sending requests. Each node returns at most eight
  candidates. The graph runs up to three nodes per window, plus review batches;
  JSON repair, transient retries and configured fallback can add more calls.
- `HIGHLIGHT_GRAPH_CONCURRENCY` defaults to 2 and is clamped to 1–3. Managed local
  inference, loopback compatible endpoints and any possible managed-local fallback
  use concurrency 1. This preserves existing local locks/GPU constraints. Cloud
  mode submits only as many nodes as there are free slots, not hundreds of futures.
- `generation.json` persists per-window/perspective status, safe error codes,
  duration, rejected-candidate count and source provenance. Empty successful output
  is different from failure. All nodes failing fails the task. Some nodes failing
  yields `partial`; successfully selected clips may render, but even if those MP4s
  all succeed, the aggregate result stays `partial` and the job needs review.
- Re-evaluating unchanged inputs without forcing reuses completed nodes and retries
  failed ones. Failed queue tasks can use the existing retry flow; completed or
  needs-review jobs retain existing rerun semantics (`--force` regenerates them).
  Changing input/config/version invalidates the graph cache. Cancel/pause stops
  scheduling new nodes and propagates, while in-flight provider/repair/fallback
  calls still need to finish within their configured timeouts. Workers never write
  checkpoint files or poll the queue callback directly.

## Compare runs without more inference

For the same source and transcript, compare three configurations: both switches
off; reviewer only; graph + reviewer. Keep model/render settings unchanged and
preserve each run's `auto_clips/` reports separately. Use fresh runs for timing:
cached/resumed timings only describe the last attempt.

```powershell
python tools/compare_highlight_runs.py --run baseline="D:/comparison/baseline/auto_clips" --run reviewed="D:/comparison/reviewed/auto_clips" --run graph="D:/comparison/graph/auto_clips"
```

This read-only helper reads only the named report files, prints counts, selected
source duration, saved generation/review timings and status, and checks whether
source signatures match. Stale/missing render reports are not counted as successes.
It never runs inference, renders, scans your job library or uploads media/text.
Node counts are NOT exact HTTP/token/billing counts. Cost, token usage and editorial
quality are reported as unknown, not fabricated from model scores. Record actual
provider usage and blind human ratings of completeness/usefulness/manual edits
separately; automated tests cannot prove an increase in audience engagement.

## Pipeline and outputs

1. ASR and amplitude-based silence detection run on the original timeline.
2. Word-timed punctuation creates stable sentence IDs; untimed ASR segments stay
   atomic. LLM requests include IDs/text/source times, but only accept ID ranges.
   The wire schema is `{"clips": [...]}`; `extract_highlights()` returns a list.
3. Recordings longer than 25 minutes use 20-minute windows with **90 seconds**
   overlap (60 seconds cannot cover every 75-second clip). Dense text is also
   bounded by `HIGHLIGHT_REQUEST_CHARS` (default 24000); if too dense, overlap may
   be shorter. This is a character budget, not a tokenizer guarantee. Reduce it
   for small local contexts. Managed llama-server also applies a conservative
   context-size-derived cap, reserving room for instructions and output.
   More than 128 windows fails before any requests.
4. IDs, chronological order, source duration, integer score and nonempty text are
   validated. Obvious dependent openings are extended backward or rejected.
   IoU > 0.5 suppresses the lower-scoring candidate; at most `HIGHLIGHT_MAX_CLIPS`
   (default 12, max 50) survive. Hook/details/payoff is a semantic model instruction,
   **not a provable guarantee of editorial quality**.
5. Directional ±300ms searches use local 10ms PCM RMS minima plus speech protection.
   Loud minima, unsupported audio and unsafe shifts retain the raw boundary.
   Long detected quiet intervals (>0.6s) are shortened toward 0.2s, retaining breath
   margins. Word intervals are never removed; without word times, the entire ASR
   sentence is protected. Output boundaries share a 30fps grid (pause length can
   differ by up to two frames); unsafe neighboring-speech boundaries are rejected.
   The final edited duration must still be 30–75s; short candidates are not padded.
6. One EDL maps audio, video and ASS onto the same output timeline. Audio gets
   15ms internal `afade` edges, not overlapping `acrossfade`: no accumulated loss
   of duration. Captions use real word times and ASS karaoke; absent word times
   fall back to ordinary sentence subtitles. Frames are center-cropped to 9:16;
   this mode does **not** claim face tracking. Existing source black-border
   detection is reused. BGM/UVR and manual subtitle overrides are not applied.
7. NVENC is probed and used if available, with libx264 fallback. Each clip is rendered
   separately; no unbounded parallel GPU jobs. MP4s replace prior completed files
   only after successful validation. Native frame/codec quantization still applies.

Outputs live under the ignored job directory:

```text
auto_clips/
  candidates.json       # IDs, validated selections, input/config fingerprint
                        # raw pool, final selection decisions, stage statuses/timings
  generation.json       # optional graph nodes, provenance, failures, retry checkpoint
  review.json           # optional full-text review, verdicts, evidence, retry checkpoint
  edits.json            # source spans and original→output mapping; rejected plans
  index.json            # aggregate result and per-clip status/download paths
  01-<revision>/
    edit.json           # title, score, reason and exact source/output spans
    subtitles.ass       # word karaoke or sentence fallback
    crop_plan.json
    render_plan.json
    result.json
    final.mp4
```

The existing job files API exposes nested outputs. No new Web UI toggle/editor is
introduced here: use CLI/API/env to opt in and inspect `auto_clips/index.json`.
Caches fingerprint transcript/silence/manifest, source/audio file stat and runtime
configuration. Completed clips are reused when those inputs match; forced runs
regenerate them. A changed revision gets its own directory; older outputs are not
deleted. Stat fingerprints do not detect adversarial same-size/same-mtime edits.
Keep source recordings immutable during processing.

Canonical stages are `evaluate_highlights`, `plan_highlights`, `render_highlights`.
Failures in one clip do not stop the remaining clips. Aggregate status is `done`,
`partial`, `failed`, or `no_qualifying_clips`; no result/partial success leaves the
job needing review, and all render failures fail the job for retry. Cancellation
propagates instead of being recorded as an ordinary failed clip. This is automated
editing, not automatic publishing or a promise that every input has a good clip.

## 中文说明

默认关闭，现有人工审核流程不变。通过 `--unattended-highlights`、API 的
`unattended_highlights: true` 或 `.env` 的 `UNATTENDED_HIGHLIGHTS_ENABLED=true`
开启；不能同时跳过转写。开启后会把转录文本发给你配置的 LLM，需完全离线时请
配置本地 Ollama/llama-server，并保持 `LLM_FALLBACK_PROVIDER` 为空。

录音、句子 ID、声学吸附始终使用原始时间；长静音压缩成剪辑区间，字幕和音视频
使用同一映射。25 分钟以上按约 20 分钟窗口处理，默认重叠 90 秒，并有字符预算。
输出时再次检查 30–75 秒时长，不会硬凑不足时长的片段或伪造词级时间。

输出位于任务目录 `auto_clips/`，每个候选独立 MP4、ASS、剪辑计划和结果记录。
使用居中 9:16 裁切、15ms 音频淡入淡出、NVENC 优先/软件回退；没有词时间时使用
普通字幕，没有承诺人脸跟踪。没有合格候选或部分失败会明确标记，不假装全部成功。
这次没有新增网页开关、不自动发布、不修改旧 `cuts.json`/`final.mp4`。

可选全文语义审核：在本地 `.env` 设置
`HIGHLIGHT_LLM_CHECKER_ENABLED=true`，重新启动服务后创建无人值守任务。默认关闭，
不开启就不会增加审核调用；该开关不影响旧版高光按钮，也不能代替无人值守开关。
已入队任务沿用入队时的配置。审核读取候选全文、标题及前后各一句，不截断结尾，
不根据抽样字幕覆盖率或关键词黑名单硬删除片段。它只判断通过/拒绝，保留原始
句子 ID、时间、标题和评分；只看文本，不能代替表情、语调或画面的判断。

审核串行分批，每批最多 4 个候选，并受字符预算限制；候选全文及上下文超限会
明确失败，不会偷偷截断。完整记录保存在 `auto_clips/review.json`，包含通过、
拒绝的理由和证据句子 ID，以及失败/暂停/取消状态。审核失败会阻止剪辑及渲染，
不能当作审核通过；全部拒绝则明确显示没有合格候选。输入与配置不变时，重试复用
已生成候选和已完成的审核；`--force` 才重新生成两者。报告含转录正文，请勿上传 Git。
审核前仅合并相同句子范围且标题一致的重复项，保留有重叠但不同的候选；审核通过后
再做 IoU > 0.5 去重，并按 `HIGHLIGHT_MAX_CLIPS` 限制成片数量。候选池由
`HIGHLIGHT_REVIEW_MAX_CANDIDATES` 控制，默认 36，最多 150，实际不低于成片上限；
超出池容量的候选按生成评分截取，并记录数量。不会为了凑数量无限追加模型请求。

完整多角度模式另设 `HIGHLIGHT_GRAPH_ENABLED=true`：钩子/观点、文本情绪、冲突/揭秘
分别分析每个完整滑窗。`HIGHLIGHT_GRAPH_CONCURRENCY` 默认 2，云端最多 3；本地端点
或可能回退到本地时固定串行。该开关不自动开启审核，两项一起开启才走完整链路。
每路每窗口最多 8 个候选，最多 128 个窗口；调用量随窗口和审核批次数增加，不能把
并行当成省钱或效果保证。来源、状态、耗时记录在 `generation.json`，失败路可单独重试。
全部分析失败则任务失败；部分失败即使剩余视频全部渲染成功，也保留 `partial` 状态，
任务需要审核。取消会停止提交新节点，但已进行的接口调用仍受原有超时约束。

上面的离线对比命令可比较同一素材的“单路 / 单路+审核 / 多路+审核”记录，不会调用
模型或上传文件。它汇总数量、状态和耗时，不会把模型评分冒充实际内容质量或账单。
效果仍需用相同素材盲评完整性、实用性和人工修改量；真实费用以供应商用量为准。

DeepSeek/Ollama 等兼容接口使用 `LLM_PROVIDER=openai` 与 `LLM_OPENAI_BASE_URL`；
DeepSeek JSON 模式配置 `LLM_RESPONSE_FORMAT=json_object`，支持结构化输出的端点
可使用 `json_schema`。接口、模型 ID 需按供应商实际支持情况配置。详细约束及其余
配置项见上文；新增处理逻辑仅使用标准库和项目已有 FFmpeg，不增加第三方依赖。
