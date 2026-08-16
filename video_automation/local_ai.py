from __future__ import annotations

import atexit
import base64
import gc
import inspect
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO
from urllib.parse import urlparse

from .llm_output import StructuredOutputError
from .llm_output import parse_structured_json as _shared_parse_json
from .llm_output import validate_required_shape as _shared_validate_shape
from .provider_errors import (
    ProviderRequestError,
    provider_configuration_error,
    provider_http_error,
    provider_network_error,
)

if TYPE_CHECKING:
    from .config import Settings


LOCAL_AI_PROVIDER_NAME = "Local Hugging Face"

_RUNTIME_LOCK = threading.RLock()
_SERVER_PROCESS: subprocess.Popen[bytes] | None = None
_SERVER_LOG: BinaryIO | None = None
_COVER_PIPELINE: Any = None
_COVER_PIPELINE_KEY: tuple[str, str, str] | None = None

def call_local_structured_llm(
    settings: Settings,
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
) -> dict[str, Any]:
    if not settings.local_llm_model_path.is_file():
        raise provider_configuration_error(
            LOCAL_AI_PROVIDER_NAME,
            "structured request",
            "model_missing",
            f"Local model is missing: {settings.local_llm_model_path}",
        )
    with _RUNTIME_LOCK:
        _release_cover_pipeline()
        ensure_local_llm_server(settings)
        request_payload = {
            "model": settings.llm_model.strip() or settings.local_llm_model_path.stem,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        f"{user}\n\n/no_think\n"
                        f"Return exactly one JSON object matching schema {schema_name}. "
                        "Do not include markdown or commentary."
                    ),
                },
            ],
            "response_format": {
                "type": "json_object",
                "schema": schema,
            },
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": 2048,
            "seed": 42,
            "chat_template_kwargs": {"enable_thinking": False},
            "reasoning_effort": "none",
            "stream": False,
        }
        request = urllib.request.Request(
            _local_llm_endpoint(settings, "chat/completions"),
            data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=settings.local_llm_request_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise provider_http_error(
                LOCAL_AI_PROVIDER_NAME,
                "structured request",
                exc.code,
                detail,
            ) from exc
        except OSError as exc:
            raise provider_network_error(
                LOCAL_AI_PROVIDER_NAME,
                "structured request",
                exc,
            ) from exc
        text = _local_chat_text(payload)
        parsed = _parse_json_object(text)
        if not isinstance(parsed, dict):
            raise ProviderRequestError(
                LOCAL_AI_PROVIDER_NAME,
                "structured request",
                "response_invalid",
                "The local model returned a non-object JSON payload.",
            )
        _validate_required_shape(parsed, schema)
        return parsed


def ensure_local_llm_server(settings: Settings) -> str:
    global _SERVER_PROCESS, _SERVER_LOG  # noqa: PLW0603
    health_url = _local_llm_endpoint(settings, "health")
    status = _health_status(health_url)
    if status == 200:
        return settings.local_llm_base_url
    if status == 503:
        _wait_for_local_llm(settings, health_url)
        return settings.local_llm_base_url

    executable = resolve_local_llm_server(settings)
    model_path = settings.local_llm_model_path
    if not model_path.is_file():
        raise provider_configuration_error(
            LOCAL_AI_PROVIDER_NAME,
            "server startup",
            "model_missing",
            f"Local model is missing: {model_path}",
        )
    host, port = _loopback_host_port(settings.local_llm_base_url)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_dir / "local-llm-server.log"
    if _SERVER_LOG is not None:
        _SERVER_LOG.close()
    _SERVER_LOG = log_path.open("ab")
    command = _local_llm_server_command(settings, executable, host, port)
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        _SERVER_PROCESS = subprocess.Popen(
            command,
            cwd=str(executable.parent) if executable.is_absolute() else None,
            stdin=subprocess.DEVNULL,
            stdout=_SERVER_LOG,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    except OSError as exc:
        _close_server_log()
        raise provider_network_error(
            LOCAL_AI_PROVIDER_NAME,
            "server startup",
            exc,
        ) from exc
    _wait_for_local_llm(settings, health_url, log_path=log_path)
    return settings.local_llm_base_url


def resolve_local_llm_server(settings: Settings) -> Path:
    configured = settings.local_llm_server_path
    if configured.is_file():
        return configured.resolve()
    discovered = shutil.which(str(configured)) or shutil.which("llama-server")
    if discovered:
        return Path(discovered).resolve()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            package_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
            candidates = sorted(
                package_root.glob(
                    "ggml.llamacpp_Microsoft.Winget.Source_*"
                    "/llama-server.exe"
                )
            )
            if candidates:
                return candidates[-1].resolve()
    raise provider_configuration_error(
        LOCAL_AI_PROVIDER_NAME,
        "server startup",
        "provider_unsupported",
        "llama-server was not found. Install llama.cpp or configure LOCAL_LLM_SERVER_PATH.",
    )


def _local_llm_server_command(
    settings: Settings,
    executable: Path,
    host: str,
    port: int,
) -> list[str]:
    return [
        str(executable),
        "--model",
        str(settings.local_llm_model_path),
        "--host",
        host,
        "--port",
        str(port),
        "--alias",
        settings.llm_model.strip() or settings.local_llm_model_path.stem,
        "--ctx-size",
        str(settings.local_llm_context_size),
        "--n-gpu-layers",
        str(settings.local_llm_gpu_layers),
        "--threads",
        str(settings.local_llm_threads),
        "--parallel",
        "1",
        "--split-mode",
        "none",
        "--main-gpu",
        "0",
        "--cache-type-k",
        "q8_0",
        "--cache-type-v",
        "q8_0",
        "--reasoning",
        "off",
        "--offline",
        "--cors-origins",
        "localhost",
        "--no-webui",
        "--no-slots",
    ]


def generate_local_cover_images(
    settings: Settings,
    *,
    prompt: str,
    count: int,
    aspect: str,
    reference_path: Path | None,
) -> dict[str, Any]:
    with _RUNTIME_LOCK:
        _stop_owned_local_llm_server()
        pipe = _load_cover_pipeline(settings)
        try:
            import torch
            from PIL import Image
        except ImportError as exc:
            raise provider_configuration_error(
                LOCAL_AI_PROVIDER_NAME,
                "image generation",
                "provider_unsupported",
                "Install requirements-local-ai.txt and Pillow for local cover generation.",
            ) from exc

        width, height = _local_cover_dimensions(aspect, settings.local_cover_max_side)
        reference = None
        if reference_path is not None and reference_path.is_file():
            with Image.open(reference_path) as source:
                reference = _fit_local_cover_reference(source, width, height)
        data = []
        for index in range(max(1, count)):
            generator_device = (
                settings.local_cover_device
                if settings.local_cover_device.startswith("cuda")
                else "cpu"
            )
            generator = torch.Generator(device=generator_device).manual_seed(
                settings.local_cover_seed + index
            )
            kwargs: dict[str, Any] = {
                "prompt": prompt,
                "height": height,
                "width": width,
                "guidance_scale": settings.local_cover_guidance_scale,
                "num_inference_steps": settings.local_cover_steps,
                "max_sequence_length": settings.local_cover_max_sequence_length,
                "generator": generator,
            }
            if reference is not None:
                kwargs["image"] = [reference]
            kwargs = _supported_pipeline_kwargs(pipe, kwargs)
            try:
                with torch.inference_mode():
                    generated = pipe(**kwargs).images[0]
            except torch.OutOfMemoryError as exc:
                _release_cover_pipeline()
                raise ProviderRequestError(
                    LOCAL_AI_PROVIDER_NAME,
                    "image generation",
                    "provider_error",
                    "CUDA ran out of memory. Close other GPU apps or lower LOCAL_COVER_MAX_SIDE.",
                ) from exc
            buffer = BytesIO()
            generated.convert("RGB").save(buffer, format="JPEG", quality=94)
            data.append(
                {
                    "b64_json": base64.b64encode(buffer.getvalue()).decode("ascii"),
                    "revised_prompt": prompt,
                }
            )
        return {"data": data}


def _supported_pipeline_kwargs(pipe: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        parameters = inspect.signature(pipe.__call__).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return kwargs
    return {name: value for name, value in kwargs.items() if name in parameters}


def local_ai_health(settings: Settings) -> list[dict[str, Any]]:
    checks = [
        _path_check(
            "local_llm_model",
            settings.local_llm_model_path,
            required=settings.llm_provider.strip().lower() == "local",
        ),
        _cover_model_check(
            settings.local_cover_model_path,
            required=settings.cover_provider.strip().lower() == "local",
        ),
    ]
    try:
        executable = resolve_local_llm_server(settings)
    except Exception as exc:
        checks.append(
            {
                "name": "local_llm_server",
                "path": str(settings.local_llm_server_path),
                "exists": False,
                "required": settings.llm_provider.strip().lower() == "local",
                "optional": settings.llm_provider.strip().lower() != "local",
                "status": (
                    "missing"
                    if settings.llm_provider.strip().lower() == "local"
                    else "optional_missing"
                ),
                "version": str(exc),
            }
        )
    else:
        checks.append(
            {
                "name": "local_llm_server",
                "path": str(executable),
                "exists": True,
                "required": settings.llm_provider.strip().lower() == "local",
                "optional": settings.llm_provider.strip().lower() != "local",
                "status": "ok",
                "version": "",
            }
        )
    for module_name in ("transformers", "diffusers", "accelerate", "bitsandbytes"):
        try:
            import importlib.util

            exists = importlib.util.find_spec(module_name) is not None
        except Exception:
            exists = False
        required = settings.cover_provider.strip().lower() == "local"
        checks.append(
            {
                "name": f"local_cover_{module_name}",
                "path": f"python:{module_name}",
                "exists": exists,
                "required": required,
                "optional": not required,
                "status": "ok" if exists else "missing" if required else "optional_missing",
                "version": "",
            }
        )
    return checks


def release_local_ai() -> None:
    with _RUNTIME_LOCK:
        _stop_owned_local_llm_server()
        _release_cover_pipeline()


def _load_cover_pipeline(settings: Settings) -> Any:
    global _COVER_PIPELINE, _COVER_PIPELINE_KEY  # noqa: PLW0603
    model_path = settings.local_cover_model_path
    _require_local_cover_files(model_path)
    key = (
        str(model_path.resolve()),
        settings.local_cover_device,
        settings.local_cover_quantization.strip().lower(),
    )
    if _COVER_PIPELINE is not None and _COVER_PIPELINE_KEY == key:
        return _COVER_PIPELINE
    _release_cover_pipeline()
    try:
        import torch
        from diffusers import DiffusionPipeline, PipelineQuantizationConfig
    except ImportError as exc:
        raise provider_configuration_error(
            LOCAL_AI_PROVIDER_NAME,
            "image generation",
            "provider_unsupported",
            "Install requirements-local-ai.txt before using COVER_PROVIDER=local.",
        ) from exc

    device = settings.local_cover_device.strip() or "cuda"
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise provider_configuration_error(
            LOCAL_AI_PROVIDER_NAME,
            "image generation",
            "provider_unsupported",
            "LOCAL_COVER_DEVICE requests CUDA, but torch.cuda is unavailable.",
        )
    dtype = (
        torch.bfloat16
        if device.startswith("cuda") and torch.cuda.get_device_capability(0)[0] >= 8
        else torch.float16
    )
    quantization = settings.local_cover_quantization.strip().lower()
    pipeline_quantization = _cover_pipeline_quantization_config(
        quantization,
        dtype,
        PipelineQuantizationConfig,
    )
    load_device = device if device.startswith("cuda") else "cpu"
    load_kwargs: dict[str, Any] = {
        "torch_dtype": dtype,
        "local_files_only": True,
    }
    if pipeline_quantization is not None:
        load_kwargs["quantization_config"] = pipeline_quantization
        load_kwargs["device_map"] = load_device
    pipe = DiffusionPipeline.from_pretrained(model_path, **load_kwargs)
    if pipeline_quantization is None:
        pipe.to(device=device, dtype=dtype)
    vae = getattr(pipe, "vae", None)
    if vae is not None and hasattr(vae, "to"):
        vae.to(device=device, dtype=dtype)
    if vae is not None and hasattr(vae, "enable_slicing"):
        vae.enable_slicing()
    if vae is not None and hasattr(vae, "enable_tiling"):
        vae.enable_tiling()
    pipe.set_progress_bar_config(disable=True)
    _COVER_PIPELINE = pipe
    _COVER_PIPELINE_KEY = key
    return pipe


def _cover_pipeline_quantization_config(
    quantization: str,
    dtype: Any,
    pipeline_config_class: Any,
) -> Any:
    if quantization in {"nf4", "4bit", "int4"}:
        return pipeline_config_class(
            quant_backend="bitsandbytes_4bit",
            quant_kwargs={
                "load_in_4bit": True,
                "bnb_4bit_quant_type": "nf4",
                "bnb_4bit_compute_dtype": dtype,
                "bnb_4bit_use_double_quant": True,
            },
            components_to_quantize=["transformer", "text_encoder"],
        )
    if quantization in {"int8", "8bit"}:
        return pipeline_config_class(
            quant_backend="bitsandbytes_8bit",
            quant_kwargs={"load_in_8bit": True},
            components_to_quantize=["transformer", "text_encoder"],
        )
    if quantization in {"none", "bf16", "fp16"}:
        return None
    raise provider_configuration_error(
        LOCAL_AI_PROVIDER_NAME,
        "image generation",
        "provider_unsupported",
        f"Unsupported LOCAL_COVER_QUANTIZATION: {quantization}",
    )


def _release_cover_pipeline() -> None:
    global _COVER_PIPELINE, _COVER_PIPELINE_KEY  # noqa: PLW0603
    had_pipeline = _COVER_PIPELINE is not None
    _COVER_PIPELINE = None
    _COVER_PIPELINE_KEY = None
    if not had_pipeline:
        return
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _stop_owned_local_llm_server() -> None:
    global _SERVER_PROCESS  # noqa: PLW0603
    process = _SERVER_PROCESS
    _SERVER_PROCESS = None
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    _close_server_log()


def _close_server_log() -> None:
    global _SERVER_LOG  # noqa: PLW0603
    if _SERVER_LOG is not None:
        _SERVER_LOG.close()
        _SERVER_LOG = None


def _wait_for_local_llm(
    settings: Settings,
    health_url: str,
    *,
    log_path: Path | None = None,
) -> None:
    deadline = time.monotonic() + settings.local_llm_startup_timeout_seconds
    while time.monotonic() < deadline:
        if _health_status(health_url) == 200:
            return
        if _SERVER_PROCESS is not None and _SERVER_PROCESS.poll() is not None:
            detail = _tail_text(log_path) if log_path is not None else "server exited"
            _close_server_log()
            raise ProviderRequestError(
                LOCAL_AI_PROVIDER_NAME,
                "server startup",
                "provider_error",
                detail,
            )
        time.sleep(0.5)
    detail = _tail_text(log_path) if log_path is not None else "startup timed out"
    _stop_owned_local_llm_server()
    raise ProviderRequestError(
        LOCAL_AI_PROVIDER_NAME,
        "server startup",
        "network_error",
        detail or "Timed out waiting for llama-server.",
    )


def _health_status(url: str) -> int | None:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except OSError:
        return None


def _local_llm_endpoint(settings: Settings, endpoint: str) -> str:
    base = settings.local_llm_base_url.strip().rstrip("/")
    _loopback_host_port(base)
    if endpoint == "health":
        return f"{base}/health"
    return f"{base}/{endpoint.lstrip('/')}"


def _loopback_host_port(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme != "http" or host not in {"127.0.0.1", "localhost", "::1"}:
        raise provider_configuration_error(
            LOCAL_AI_PROVIDER_NAME,
            "local connection",
            "provider_unsupported",
            "LOCAL_LLM_BASE_URL must use an http loopback address.",
        )
    return host, parsed.port or 80


def _local_chat_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderRequestError(
            LOCAL_AI_PROVIDER_NAME,
            "structured request",
            "response_invalid",
            "The local server response did not include choices.",
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ProviderRequestError(
            LOCAL_AI_PROVIDER_NAME,
            "structured request",
            "response_invalid",
            "The local server response did not include JSON content.",
        )
    return content.strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        return _shared_parse_json(text, provider=LOCAL_AI_PROVIDER_NAME)
    except StructuredOutputError as exc:
        raise ProviderRequestError(
            LOCAL_AI_PROVIDER_NAME,
            "structured request",
            "response_invalid",
            str(exc),
        ) from exc


def _validate_required_shape(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    try:
        _shared_validate_shape(value, schema, path)
    except StructuredOutputError as exc:
        raise ProviderRequestError(
            LOCAL_AI_PROVIDER_NAME,
            "structured request",
            "response_invalid",
            str(exc),
        ) from exc


def _local_cover_dimensions(aspect: str, max_side: int) -> tuple[int, int]:
    ratios = {"9:16": (9, 16), "16:9": (16, 9)}
    ratio = ratios.get(aspect)
    if ratio is None:
        raise ValueError(f"unsupported local cover aspect: {aspect}")
    width_ratio, height_ratio = ratio
    if width_ratio >= height_ratio:
        width = max_side
        height = round(max_side * height_ratio / width_ratio)
    else:
        height = max_side
        width = round(max_side * width_ratio / height_ratio)
    return _multiple_of_64(width), _multiple_of_64(height)


def _fit_local_cover_reference(image: Any, width: int, height: int) -> Any:
    from PIL import Image, ImageOps

    oriented = ImageOps.exif_transpose(image).convert("RGB")
    source_ratio = oriented.width / max(1, oriented.height)
    target_ratio = width / max(1, height)
    if source_ratio > target_ratio * 1.2:
        trim = int(oriented.height * 0.1)
        oriented = oriented.crop((0, trim, oriented.width, oriented.height - trim))
    return ImageOps.fit(
        oriented,
        (width, height),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def _multiple_of_64(value: int) -> int:
    return max(64, int(round(value / 64)) * 64)


def _require_local_cover_files(model_path: Path) -> None:
    missing = _missing_local_cover_files(model_path)
    if missing:
        raise provider_configuration_error(
            LOCAL_AI_PROVIDER_NAME,
            "image generation",
            "model_missing",
            f"Local image model is incomplete; missing {missing[0]}",
        )


def _missing_local_cover_files(model_path: Path) -> list[str]:
    missing: list[str] = []
    if not (model_path / "model_index.json").is_file():
        missing.append("model_index.json")
    weight_patterns = ("*.safetensors", "*.bin", "*.pt", "*.pth")
    if not any(any(model_path.rglob(pattern)) for pattern in weight_patterns):
        missing.append("model weights")
    return missing


def _path_check(name: str, path: Path, *, required: bool) -> dict[str, Any]:
    exists = path.is_file()
    return {
        "name": name,
        "path": str(path),
        "exists": exists,
        "required": required,
        "optional": not required,
        "status": "ok" if exists else "missing" if required else "optional_missing",
        "version": "",
    }


def _cover_model_check(model_path: Path, *, required: bool) -> dict[str, Any]:
    missing = _missing_local_cover_files(model_path)
    exists = not missing
    return {
        "name": "local_cover_model",
        "path": str(model_path),
        "exists": exists,
        "required": required,
        "optional": not required,
        "status": "ok" if exists else "missing" if required else "optional_missing",
        "version": "" if exists else f"missing: {missing[0]}",
    }


def _tail_text(path: Path | None, max_chars: int = 2000) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-max_chars:]
    except OSError:
        return ""


atexit.register(release_local_ai)
