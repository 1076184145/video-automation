from __future__ import annotations

import re
from typing import Any


Advice = dict[str, Any]


def advise_error(error: str | BaseException | None) -> Advice:
    text = str(error or "").strip()
    lowered = text.lower()
    for matcher, factory in _RULES:
        if matcher(lowered):
            advice = factory()
            advice["raw_error"] = text
            return advice
    advice = _generic_advice()
    advice["raw_error"] = text
    return advice


def _gpu_memory_advice() -> Advice:
    return {
        "code": "gpu_memory",
        "title": "显卡内存不足",
        "summary": "转写或渲染时显存不够，任务没有继续执行。",
        "next_steps": [
            "把 Whisper 模型切换到 medium 或更小模型。",
            "降低 faster-whisper batch size，或改用 CPU/int8。",
            "减少并行任务后重试当前阶段。",
        ],
        "actions": [
            {
                "type": "settings_patch_and_rerun",
                "label": "切换到 medium 模型并重试转写",
                "env": {
                    "WHISPER_MODEL": "medium",
                    "FASTER_WHISPER_BATCH_SIZE": "1",
                },
                "stage": "transcribe",
            },
            {
                "type": "open_settings",
                "label": "打开设置页",
                "target": "#/settings",
            },
        ],
    }


def _missing_ffmpeg_advice() -> Advice:
    return {
        "code": "missing_ffmpeg",
        "title": "缺少 FFmpeg 工具",
        "summary": "本地视频探测、音频提取或渲染需要 FFmpeg/FFprobe。",
        "next_steps": [
            "打开健康检查页。",
            "点击一键修复环境安装便携 FFmpeg。",
            "修复完成后重试失败任务。",
        ],
        "actions": [
            {
                "type": "open_health",
                "label": "去健康检查一键修复",
                "target": "#/health",
            },
        ],
    }


def _empty_transcript_advice() -> Advice:
    return {
        "code": "empty_transcript",
        "title": "未检测到语音内容",
        "summary": "转写结果为空，可能是视频没有人声、音轨过小、音频提取失败或语言/模型选择不合适。",
        "next_steps": [
            "检查视频是否有可听见的人声。",
            "重跑提取音频阶段确认 audio.wav 正常。",
            "如果这是无语音素材，可以跳过转写继续生成画面剪辑。",
        ],
        "actions": [
            {
                "type": "rerun_stage",
                "label": "重跑提取音频",
                "stage": "extract_audio",
            },
            {
                "type": "skip_transcribe",
                "label": "跳过转写，先重跑静音检测",
                "stage": "detect_silence",
            },
        ],
    }


def _disk_space_advice() -> Advice:
    return {
        "code": "disk_space",
        "title": "磁盘空间不足",
        "summary": "生成中间文件、预览视频或成片时，磁盘剩余空间不够。",
        "next_steps": [
            "删除不需要的旧任务或成片产物。",
            "清理 processing/jobs、logs 和临时下载目录。",
            "确保目标磁盘至少有源视频数倍的可用空间。",
        ],
        "actions": [
            {
                "type": "open_cleanup",
                "label": "查看任务并清理旧项目",
                "target": "#/",
            },
        ],
    }


def _generic_advice() -> Advice:
    return {
        "code": "generic",
        "title": "任务失败",
        "summary": "系统遇到一个暂未识别的错误。原始错误已保留，方便进一步排查。",
        "next_steps": [
            "先重试失败阶段。",
            "打开健康检查页确认依赖状态。",
            "如果仍失败，查看 job.log 获取详细上下文。",
        ],
        "actions": [
            {
                "type": "open_health",
                "label": "打开健康检查",
                "target": "#/health",
            },
        ],
    }


def _contains_any(*tokens: str):
    return lambda text: any(token in text for token in tokens)


def _matches(pattern: str):
    regex = re.compile(pattern, re.IGNORECASE)
    return lambda text: bool(regex.search(text))


_RULES: list[tuple[Any, Any]] = [
    (_matches(r"(cuda out of memory|cublas|cudnn|vram|gpu memory|outofmemory|显存)"), _gpu_memory_advice),
    (_matches(r"(ffmpeg|ffprobe).*(not found|winerror 2|no such file|cannot find)|winerror 2.*(ffmpeg|ffprobe)"), _missing_ffmpeg_advice),
    (_matches(r"(empty transcript|empty segments|no speech|未检测到语音|转写.*空|transcription returned empty)"), _empty_transcript_advice),
    (_contains_any("no space left", "not enough space", "disk full", "磁盘空间不足"), _disk_space_advice),
]
