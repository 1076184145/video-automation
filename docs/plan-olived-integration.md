# OlivedPro 直播录制能力接入计划

## 1. 背景

当前 video-automation 项目的视频下载方案：

| 模块 | 工具 | 问题 |
|------|------|------|
| 普通视频下载 | yt-dlp（子进程） | 够用，无大问题 |
| 直播流录制 | ffmpeg（子进程） | 断流即死、无平台解析、无自动重连、无弹幕 |

OlivedPro（github.com/olivedapp/olived）是一个 Go 编写的直播录制工具，内置 80+ 平台解析器、自研 FLV/M3U8/WebSocket 流处理器、弹幕录制。其核心解析库 `olivetv` 是独立模块，可单独提取使用。

## 2. 目标

将 OlivedPro 的**平台解析能力**接入 video-automation，替代 yt-dlp 的流地址解析，同时引入更强的直播录制能力。

**不做的事：**
- 不替换 yt-dlp（普通视频下载继续用它）
- 不用 OlivedPro 的 GUI/Wails 前端
- 不引入许可证系统

## 3. 方案对比

### 方案 A：Go CLI 工具（推荐）

```
Python (video-automation)
    │
    ├── downloads.py ──→ yt-dlp（不变）
    │
    └── live_recordings.py
            │
            ├── 解析流地址 ──→ olived-resolver（Go CLI）
            │                    ↓
            │                 返回 JSON：流URL + 平台 + 主播名 + 画质列表
            │
            └── 录制 ──→ ffmpeg（现有逻辑，稍作增强）
```

**优点：**
- Python 代码改动最小（只改 `_resolve_stream_url()`）
- Go CLI 独立编译，跨平台，无依赖
- olivetv 的 80+ 平台解析能力直接可用
- 后续可逐步替换 ffmpeg 为自研拉流器

**缺点：**
- 需要维护一个 Go 项目
- 子进程调用有约 100ms 启动开销

**工作量：** 约 2-3 天

### 方案 B：Python 重写 olivetv

将 olivetv 的核心逻辑用 Python 重写，直接集成到 video_automation 包中。

**优点：**
- 纯 Python，无外部依赖
- 完全可控，可深度集成

**缺点：**
- 工作量巨大（olivetv 有 563 个方法，80+ 平台）
- Go 的并发模型（goroutine）在 Python 中需要 asyncio 重写
- 部分平台用了 Go 特有的加密库（SM3、RC4 等）

**工作量：** 约 2-4 周

### 方案 C：Go 微服务

将 olivetv 包装成一个常驻的 HTTP/gRPC 服务，Python 通过 HTTP 调用。

**优点：**
- 无子进程开销
- 可以常驻后台，支持流式传输
- 可以复用连接池

**缺点：**
- 需要管理服务生命周期（启动/停止/健康检查）
- 增加部署复杂度
- 对于"解析一个 URL 返回结果"这种简单场景，HTTP 服务有点重

**工作量：** 约 3-5 天

### 方案 D：增强现有 yt-dlp 调用

不引入 olivetv，而是优化现有的 yt-dlp + ffmpeg 方案：
- 加自动重连逻辑
- 加直播状态轮询
- 加断流重试

**优点：**
- 零新依赖
- 改动最小

**缺点：**
- yt-dlp 对部分平台支持不好（抖音 WebSocket 推流等）
- 没有弹幕录制能力
- 本质是打补丁，不是升级

**工作量：** 约 1 天

## 4. 推荐方案 A 详细设计

### 4.1 Go CLI 工具：olived-resolver

```
olived-resolver/
├── main.go           # CLI 入口
├── resolver.go       # 调用 olivetv 解析
├── recorder.go       # 自研 FLV/M3U8 拉流（Phase 2）
├── go.mod
└── go.sum
```

**CLI 接口设计：**

```bash
# 解析直播流地址
olived-resolver resolve "https://live.bilibili.com/12345"
# 输出 JSON：
# {
#   "platform": "bilibili",
#   "room_id": "12345",
#   "streamer": "主播名",
#   "room_name": "直播间标题",
#   "status": "live",
#   "streams": [
#     {"quality": "原画", "url": "https://...", "type": "flv"},
#     {"quality": "高清", "url": "https://...", "type": "hls"}
#   ]
# }

# 检查直播状态（轻量，不解析流地址）
olived-resolver check "https://live.bilibili.com/12345"
# 输出：{"status": "live", "streamer": "主播名"}

# 直接录制（Phase 2，替代 ffmpeg）
olived-resolver record "https://live.bilibili.com/12345" --output /path/to/output.flv --quality 原画
```

**Go 核心代码骨架：**

```go
package main

import (
    "encoding/json"
    "fmt"
    "os"

    "github.com/luxcgo/olivetv"
)

type ResolveResult struct {
    Platform string        `json:"platform"`
    RoomID   string        `json:"room_id"`
    Streamer string        `json:"streamer"`
    RoomName string        `json:"room_name"`
    Status   string        `json:"status"`
    Streams  []StreamInfo  `json:"streams"`
}

type StreamInfo struct {
    Quality string `json:"quality"`
    URL     string `json:"url"`
    Type    string `json:"type"` // flv, hls, ws
}

func resolve(url string) (*ResolveResult, error) {
    tv := olivetv.NewTV(url)
    info, err := tv.Snap()
    if err != nil {
        return nil, err
    }
    result := &ResolveResult{
        Platform: info.SiteName,
        RoomID:   info.RoomID,
        Streamer: info.StreamerName,
        RoomName: info.RoomName,
        Status:   map[bool]string{true: "live", false: "offline"}[info.Live],
    }
    for _, s := range info.StreamInfos {
        result.Streams = append(result.Streams, StreamInfo{
            Quality: s.Name,
            URL:     s.URL,
            Type:    s.Type,
        })
    }
    return result, nil
}

func main() {
    if len(os.Args) < 3 {
        fmt.Fprintln(os.Stderr, "usage: olived-resolver <resolve|check> <url>")
        os.Exit(1)
    }
    switch os.Args[1] {
    case "resolve":
        result, err := resolve(os.Args[2])
        if err != nil {
            fmt.Fprintf(os.Stderr, "error: %v\n", err)
            os.Exit(1)
        }
        json.NewEncoder(os.Stdout).Encode(result)
    case "check":
        // 轻量检查，只判断是否在播
        // ...
    }
}
```

### 4.2 Python 侧改动

**live_recordings.py 修改：**

```python
# 新增：使用 olived-resolver 解析流地址
def _resolve_stream_url(settings: Settings, url: str) -> str | dict:
    """解析直播流地址，返回流信息。"""
    resolver = settings.olived_resolver_path  # 新配置项
    if resolver and Path(resolver).is_file():
        return _resolve_with_olived(resolver, url)
    # 回退到 yt-dlp
    return _resolve_with_ytdlp(settings, url)


def _resolve_with_olived(resolver: str, url: str) -> dict | None:
    """调用 olived-resolver 解析。"""
    try:
        result = subprocess.run(
            [resolver, "resolve", url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        if not data.get("streams"):
            return None
        # 默认选第一个流（通常是最高画质）
        stream = data["streams"][0]
        return {
            "url": stream["url"],
            "platform": data.get("platform", ""),
            "streamer": data.get("streamer", ""),
            "quality": stream.get("quality", ""),
            "type": stream.get("type", "flv"),
        }
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
```

**config.py 新增配置项：**

```python
# olived-resolver 路径（为空则回退到 yt-dlp）
olived_resolver_path: str = ""
# 默认画质（原画/蓝光/高清/标清）
live_recording_quality: str = "原画"
```

### 4.3 分阶段实施

#### Phase 1：Go CLI 基础版（2天）

- [ ] 初始化 Go 项目，引入 olivetv 依赖
- [ ] 实现 `resolve` 命令：解析 URL → 返回流地址 JSON
- [ ] 实现 `check` 命令：检查直播状态
- [ ] 编译 Windows/Linux 二进制
- [ ] Python 侧 `_resolve_stream_url()` 对接

**交付物：**
- `olived-resolver.exe`（Windows）
- `olived-resolver`（Linux）
- 修改后的 `live_recordings.py` + `config.py`

#### Phase 2：增强录制（2天）

- [ ] Go CLI 增加 `record` 命令（自研 FLV/M3U8 拉流）
- [ ] 自动重连逻辑（断流后等待重试）
- [ ] 画质选择支持
- [ ] Python 侧集成 Go recorder（替代 ffmpeg）

#### Phase 3：弹幕录制（1天）

- [ ] Go CLI 增加 `danmaku` 命令
- [ ] 支持 B站/虎牙/抖音弹幕抓取
- [ ] 输出 ASS/JSON 格式
- [ ] Python 侧集成弹幕文件管理

#### Phase 4：直播监控（1天）

- [ ] Go CLI 增加 `monitor` 命令（持续监控直播间状态）
- [ ] 开播自动通知 Python 侧
- [ ] 支持多直播间同时监控

## 5. 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| olivetv 依赖 Go 版本 | 编译失败 | 锁定 Go 1.22 + olivetv 特定 commit |
| 部分平台 API 变动 | 解析失败 | olivetv 社区维护，定期更新依赖 |
| 子进程调用延迟 | 解析慢 | 单次调用约 100ms，可接受；Phase 2 改为内置录制 |
| Windows 编译问题 | 无法使用 | Go 原生支持交叉编译，CI 自动构建 |

## 6. 不做的事

- **不用 OlivedPro 的 GUI** — 你的项目有自己的 Web 界面
- **不用 OlivedPro 的许可证系统** — 这是学习项目，不引入商业限制
- **不替换 yt-dlp** — 普通视频下载它够用
- **不引入 Wails 框架** — Go CLI 保持极简

## 7. 依赖清单

### Go 侧
```
github.com/luxcgo/olivetv    # 直播平台解析库
github.com/imroc/req/v3      # HTTP 客户端（olivetv 依赖）
github.com/gorilla/websocket  # WebSocket（抖音等平台）
```

### Python 侧（无新增依赖）
仅修改 `live_recordings.py` 和 `config.py`，通过 subprocess 调用 Go CLI。

## 8. 验收标准

Phase 1 完成后：
- [ ] `olived-resolver resolve "https://live.bilibili.com/xxx"` 能返回正确的流地址
- [ ] `olived-resolver check "https://live.bilibili.com/xxx"` 能判断是否在播
- [ ] video-automation 的直播录制功能正常工作
- [ ] 支持至少 10 个主流平台（B站、虎牙、斗鱼、抖音、YouTube、Twitch 等）

---

*计划版本：v1.0 | 2026-06-01*
