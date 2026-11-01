#!/usr/bin/env python3
"""config/credentials.py: Canonical SSOT Credential and Configuration Engine."""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Set, Tuple, Union

logger = logging.getLogger("config.credentials")
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_JSON_PATH = WORKSPACE_ROOT / "config" / "runtime" / "model_registry.json"


@lru_cache(maxsize=1)
def resolve_ssot_env_file() -> Path:
    """Resolves the canonical env_di file, traversing git worktrees to the main repository root."""
    primary = WORKSPACE_ROOT / "env_di"
    if primary.is_file():
        return primary
    if WORKSPACE_ROOT.parent.name == ".worktrees" and (WORKSPACE_ROOT.parent.parent / "env_di").is_file():
        return WORKSPACE_ROOT.parent.parent / "env_di"
    try:
        res = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=5.0, check=False)
        if res.returncode == 0 and res.stdout.strip():
            c_dir = Path(res.stdout.strip())
            c_root = (WORKSPACE_ROOT / c_dir).resolve().parent if not c_dir.is_absolute() else c_dir.resolve().parent
            if (c_root / "env_di").is_file():
                return c_root / "env_di"
    except Exception:
        pass
    return primary


SSOT_ENV_FILE = resolve_ssot_env_file()
CENTRAL_SSOT_FILE = Path.home() / ".config" / "ai_env" / "common.env"
FALLBACK_SSOT_FILE = Path.home() / "env" / "common.env"
REASONING_MODEL_PREFIXES: Tuple[str, ...] = ("o1-", "o1-mini", "o3-mini", "gpt-5.6-", "gpt-6", "gemini-3.8")  # noqa: SSOT_EXEMPT
O200K_MODEL_PREFIXES: Tuple[str, ...] = ("gpt-6", "gpt-5", "o1", "o3", "gpt-4o", "o200k")  # noqa: SSOT_EXEMPT


def _load_registry_data() -> Dict[str, Any]:
    if REGISTRY_JSON_PATH.is_file():
        try:
            return json.loads(REGISTRY_JSON_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _get_approved_models_for_provider(provider: str) -> Set[str]:
    try:
        from dual_pipeline.model_registry import get_approved_models
        models = get_approved_models(provider)
        if models:
            return set(models)
    except Exception:
        pass
    return set(_load_registry_data().get("approved_models", {}).get(provider, []))


def _get_default_tier_model(tier_name: str) -> str:
    try:
        from dual_pipeline.model_registry import get_binding
        return get_binding(tier_name).model_id
    except Exception:
        data = _load_registry_data()
        tiers = data.get("model_tiers") or data.get("tiers") or {}
        spec = tiers.get(tier_name, {})
        return str(spec.get("default_model") or spec.get("model_id") or "")


def _get_model_aliases() -> Dict[str, str]:
    aliases = dict(_load_registry_data().get("model_aliases", {}))
    if aliases:
        return aliases
    oai = sorted(_get_approved_models_for_provider("openai"))
    sol61 = next((m for m in oai if "6.1-sol" in m), oai[0] if oai else "")
    sol6 = next((m for m in oai if m.endswith("-6-sol")), sol61)
    luna6 = next((m for m in oai if m.endswith("-6-luna")), sol61)
    luna56 = next((m for m in oai if "5.6-luna" in m), luna6)
    sol56 = next((m for m in oai if "5.6-sol" in m), sol61)
    terra56 = next((m for m in oai if "5.6-terra" in m), sol56)
    return {
        sol61: sol61, "gpt-6.1": sol61, "6.1-sol": sol61, "sol-6.1": sol61, "sol": sol61,  # noqa: SSOT_EXEMPT
        sol6: sol6, "sol-6": sol6, "gpt-sol-6": sol6,
        luna6: luna6, "luna-6": luna6, "gpt-luna-6": luna6, "6-luna": luna6, "luna": luna6,
        "gpt-luna-5.6": luna56, "gpt-6l": luna56, "6l": luna56, "gpt-sol-5.6": sol56,
        "gpt-terra-5.6": terra56, "gpt-terra-6.5": terra56, "terra": terra56,
    }


MODEL_ALIASES: Dict[str, str] = _get_model_aliases()
_DYNAMIC_TIER_ATTRS = {"X1_CHALLENGER_DEFAULT_MODEL", "X1_JUDGE_DEFAULT_MODEL", "DEFAULT_GEMINI_FAILOVER_MODEL", "DEFAULT_GEMINI_PRO_MODEL"}


def __getattr__(name: str) -> Any:
    if name == "OPENAI_APPROVED_MODELS":
        return _get_approved_models_for_provider("openai")
    if name == "GOOGLE_APPROVED_MODELS":
        return _get_approved_models_for_provider("google")
    if name == "GOVERNED_MODELS":
        return _get_approved_models_for_provider("openai") | _get_approved_models_for_provider("google") | _get_approved_models_for_provider("local")
    if name in _DYNAMIC_TIER_ATTRS:
        return _get_default_tier_model("ANTIGRAVITY_AGENT_FLASH")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def canonicalize_model_name(model_name: str) -> str:
    """Canonicalizes colloquial or transposed model strings into governed identifiers."""
    cleaned = (model_name or "").strip().lower()
    return MODEL_ALIASES.get(cleaned, (model_name or "").strip())


@dataclass(frozen=True)
class OpenAICredentials:
    api_key: Optional[str]
    is_mock: bool
    model: str
    reasoning_effort: str
    masked_key: str
    source: str
    base_url: Optional[str] = None
    organization: Optional[str] = None
    project: Optional[str] = None

    @property
    def has_live_key(self) -> bool:
        return bool(self.api_key) and not self.is_mock

    @property
    def is_reasoning_model(self) -> bool:
        return any(self.model.lower().startswith(pfx) for pfx in REASONING_MODEL_PREFIXES)


@dataclass(frozen=True)
class AnthropicCredentials:
    api_key: Optional[str]
    is_mock: bool
    masked_key: str
    source: str
    base_url: Optional[str] = None


@dataclass(frozen=True)
class GoogleCredentials:
    api_key: Optional[str]
    is_mock: bool
    masked_key: str
    source: str
    base_url: Optional[str] = None


@dataclass(frozen=True)
class LocalEmbeddingCredentials:
    model_path: Optional[str]
    manifest_path: Optional[str]
    device: str
    fp16: bool
    is_available: bool


def mask_secret(secret: Optional[str], prefix_len: int = 8, suffix_len: int = 4) -> str:
    """Safely masks sensitive tokens for telemetry, receipts, and audit logging."""
    if not secret:
        return "[NOT_CONFIGURED]"
    clean = secret.strip()
    prefix_len = 8 if clean.startswith("sk-proj-") else (3 if clean.startswith("sk-") else prefix_len)
    return "[REDACTED]" if len(clean) <= prefix_len + suffix_len else f"{clean[:prefix_len]}...{clean[-suffix_len:]}"


_SSOT_ENV_LOADED = False
_SSOT_ENV_MTIME: float = 0.0
_CREDENTIAL_TTL_SECONDS: float = float(os.environ.get("NEE_CREDENTIAL_TTL_SECONDS", 3600.0))
_LAST_CREDENTIAL_LOAD_TIME: float = 0.0


def invalidate_credentials(provider: Optional[str] = None) -> None:
    global _SSOT_ENV_LOADED, _LAST_CREDENTIAL_LOAD_TIME
    _SSOT_ENV_LOADED = False
    _LAST_CREDENTIAL_LOAD_TIME = 0.0
    logger.info("Invalidated credentials for provider '%s'; will reload on next access.", provider or "all")


def _check_env_mtime_or_ttl_expired() -> bool:
    target_env = resolve_ssot_env_file()
    if _LAST_CREDENTIAL_LOAD_TIME > 0 and (time.time() - _LAST_CREDENTIAL_LOAD_TIME) > _CREDENTIAL_TTL_SECONDS:
        return True
    try:
        return target_env.is_file() and target_env.stat().st_mtime > _SSOT_ENV_MTIME
    except Exception:
        return False


def refresh_credentials(force: bool = False) -> bool:
    global _SSOT_ENV_LOADED
    if force or _check_env_mtime_or_ttl_expired():
        _SSOT_ENV_LOADED = False
        return ensure_ssot_env_loaded()
    return True


try:
    if hasattr(signal, "SIGHUP") and threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGHUP, lambda s, f: refresh_credentials(force=True))
except Exception:
    pass


def _load_env_path(env_file: Path, override: bool) -> bool:
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=override)
        return True
    except ImportError:
        try:
            from config.secret_providers import EnvFileSecretProvider
            for k, v in EnvFileSecretProvider(env_file)._load().items():
                if override or k not in os.environ:
                    os.environ.update({k: v})
            return True
        except Exception as e:
            logger.warning("Failed manual env_di parsing: %s", e)
            return False


def ensure_ssot_env_loaded() -> bool:
    """Ensures SSOT environment files are loaded into process environment."""
    global _SSOT_ENV_LOADED, _SSOT_ENV_MTIME, _LAST_CREDENTIAL_LOAD_TIME
    if _SSOT_ENV_LOADED and not _check_env_mtime_or_ttl_expired():
        return True
    _SSOT_ENV_LOADED = False
    should_override = os.environ.get("SSOT_ENV_NO_OVERRIDE", "0") != "1"
    central_path = CENTRAL_SSOT_FILE if CENTRAL_SSOT_FILE.is_file() else (FALLBACK_SSOT_FILE if FALLBACK_SSOT_FILE.is_file() else None)
    if central_path:
        _load_env_path(central_path, override=False)
    target_env = resolve_ssot_env_file()
    if not target_env.is_file():
        _LAST_CREDENTIAL_LOAD_TIME = time.time()
        return central_path is not None
    if _load_env_path(target_env, override=should_override):
        _SSOT_ENV_LOADED = True
        _SSOT_ENV_MTIME = target_env.stat().st_mtime
        _LAST_CREDENTIAL_LOAD_TIME = time.time()
        return True
    return False


def _sanitize_reasoning_kwargs(clean: Dict[str, Any]) -> None:
    effort_str = str(clean.get("reasoning_effort") or "medium").lower()
    clean["reasoning_effort"] = "xhigh" if effort_str in ("max", "extreme") else ("low" if effort_str == "minimal" else effort_str)
    if clean["reasoning_effort"] != "none":
        for p in ("temperature", "top_p", "presence_penalty", "frequency_penalty"):
            clean.pop(p, None)
    if "max_tokens" in clean and "max_completion_tokens" not in clean:
        clean["max_completion_tokens"] = clean.pop("max_tokens")


def sanitize_model_call_kwargs(model: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitizes model invocation kwargs enforcing parameter immunity for reasoning families."""
    clean = dict(kwargs)
    canonical = canonicalize_model_name(clean.get("model", model))
    clean["model"] = canonical
    if any(canonical.lower().startswith(pfx) for pfx in REASONING_MODEL_PREFIXES) or "reasoning_effort" in clean:
        _sanitize_reasoning_kwargs(clean)
    elif "temperature" not in clean:
        clean["temperature"] = 0.0
    return clean


def _get_legacy_openai_default_model() -> str:
    oai = sorted(_get_approved_models_for_provider("openai"))
    return next((m for m in oai if "5.6-sol" in m), _get_default_tier_model("FRONTIER_HIGH_REASONING"))


def resolve_openai_credentials(
    allow_mock: bool = False, model: Optional[str] = None, reasoning_effort: Optional[str] = None,
    default_model: Optional[str] = None, default_reasoning_effort: str = "max",
    base_url: Optional[str] = None, organization: Optional[str] = None, project: Optional[str] = None,
    api_key: Optional[str] = None,
) -> OpenAICredentials:
    ensure_ssot_env_loaded()
    raw_key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    fallback_m = default_model or _get_legacy_openai_default_model()
    requested_model = canonicalize_model_name(model or os.environ.get("OPENAI_MODEL") or fallback_m)
    approved_oai = _get_approved_models_for_provider("openai")
    if requested_model not in approved_oai:
        raise ValueError(
            f"Model '{requested_model}' is not an approved OpenAI model (approved: {sorted(approved_oai)}). "
            "Direct execution barred under Decision MI-D2 (Fail-Closed Rejection)."
        )
    target_effort = reasoning_effort or os.environ.get("OPENAI_REASONING_EFFORT", default_reasoning_effort)
    target_base = base_url or os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
    target_org = organization or os.environ.get("OPENAI_ORG_ID")
    target_proj = project or os.environ.get("OPENAI_PROJECT_ID")
    if not raw_key:
        if allow_mock:
            return OpenAICredentials(None, True, requested_model, target_effort, "[MOCK_MODE]", "mock", target_base, target_org, target_proj)
        raise ValueError("OPENAI_API_KEY is required but not configured in SSOT `env_di` or environment.")
    src = "env_di" if SSOT_ENV_FILE.exists() else "environment"
    return OpenAICredentials(raw_key, False, requested_model, target_effort, mask_secret(raw_key, 8, 4), src, target_base, target_org, target_proj)


def resolve_anthropic_credentials(allow_mock: bool = False) -> AnthropicCredentials:
    ensure_ssot_env_loaded()
    raw = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if not raw:
        if allow_mock:
            return AnthropicCredentials(None, True, "[MOCK_MODE]", "mock", base_url)
        raise ValueError("ANTHROPIC_API_KEY is required but not configured in SSOT.")
    return AnthropicCredentials(raw, False, mask_secret(raw), "env_di" if SSOT_ENV_FILE.exists() else "environment", base_url)


def resolve_google_credentials(allow_mock: bool = False, base_url: Optional[str] = None) -> GoogleCredentials:
    ensure_ssot_env_loaded()
    raw = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    target_base = base_url or os.environ.get("GOOGLE_BASE_URL") or os.environ.get("GEMINI_BASE_URL")
    if not raw:
        if allow_mock:
            return GoogleCredentials(None, True, "[MOCK_MODE]", "mock", target_base)
        raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY is required but not configured.")
    return GoogleCredentials(raw, False, mask_secret(raw), "env_di" if SSOT_ENV_FILE.exists() else "environment", target_base)


def resolve_local_embedding_credentials() -> LocalEmbeddingCredentials:
    ensure_ssot_env_loaded()
    m_path = os.environ.get("NEE_BGE_M3_MODEL_PATH")
    return LocalEmbeddingCredentials(
        m_path, os.environ.get("NEE_BGE_M3_MODEL_MANIFEST"), os.environ.get("NEE_EMBEDDING_DEVICE", "cpu"),
        os.environ.get("NEE_EMBEDDING_FP16", "true").lower() == "true", bool(m_path and Path(m_path).exists()),
    )


def _extract_system_prefix(messages: Any) -> str:
    if not messages:
        return ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") in ("system", "developer"):
            return str(m.get("content", ""))
    return str(messages[0].get("content", "")) if isinstance(messages[0], dict) else ""


def _inject_prompt_cache_key(kwargs: Dict[str, Any], canon_m: str, cache_ns: Any) -> None:
    if kwargs.get("prompt_cache_key") or not cache_ns:
        return
    sys_text = _extract_system_prefix(kwargs.get("messages") or [])
    if sys_text:
        try:
            from dual_pipeline.prompt_cache import derive_cache_key
            kwargs["prompt_cache_key"] = derive_cache_key(workflow=str(cache_ns), model=canon_m, static_prefix=sys_text)
        except Exception as exc:
            logger.debug("Prompt cache key derivation bypassed: %s", exc)


def _prepare_call_kwargs(kwargs: Dict[str, Any], default_model: str) -> Tuple[str, Dict[str, Any]]:
    raw_m = kwargs.get("model") or default_model
    canon_m = canonicalize_model_name(raw_m)
    approved_oai = _get_approved_models_for_provider("openai")
    if canon_m not in approved_oai:
        raise ValueError(
            f"Model '{raw_m}' (canonical: '{canon_m}') is not an approved OpenAI model (approved: {sorted(approved_oai)}). "
            "Direct transport dispatch blocked under Decision MI-D2 (Fail-Closed Rejection)."
        )
    kwargs["model"] = canon_m
    cache_ns = kwargs.pop("cache_namespace", None)
    pc_opts = kwargs.pop("prompt_cache_options", None)
    if isinstance(pc_opts, dict):
        cache_ns = cache_ns or pc_opts.get("cache_namespace") or pc_opts.get("workflow")
        kwargs.setdefault("prompt_cache_key", pc_opts.get("prompt_cache_key"))
        if kwargs.get("prompt_cache_key") is None:
            kwargs.pop("prompt_cache_key", None)
        if "retention" in pc_opts and not kwargs.get("prompt_cache_retention"):
            kwargs["prompt_cache_retention"] = pc_opts["retention"]
    _inject_prompt_cache_key(kwargs, canon_m, cache_ns)
    return canon_m, sanitize_model_call_kwargs(canon_m, kwargs)


def _guard_client_completions(client: Any, default_model: str) -> Any:
    import asyncio
    import inspect
    from config.request_deadline import remaining_timeout

    orig_create = client.chat.completions.create
    if inspect.iscoroutinefunction(orig_create):
        async def _async_guarded(*args: Any, **kwargs: Any) -> Any:
            _, san = _prepare_call_kwargs(kwargs, default_model)
            eff_t = remaining_timeout(san.get("timeout") if san.get("timeout") is not None else 120.0)
            san["timeout"] = eff_t
            return await asyncio.wait_for(orig_create(*args, **san), timeout=eff_t)
        client.chat.completions.create = _async_guarded
    else:
        def _sync_guarded(*args: Any, **kwargs: Any) -> Any:
            _, san = _prepare_call_kwargs(kwargs, default_model)
            san["timeout"] = remaining_timeout(san.get("timeout") if san.get("timeout") is not None else 120.0)
            return orig_create(*args, **san)
        client.chat.completions.create = _sync_guarded
    return client


def _build_openai_client_kwargs(creds: OpenAICredentials, timeout: Optional[float], max_retries: Optional[int], is_async: bool) -> Dict[str, Any]:
    from config.request_deadline import remaining_timeout
    from config.transport import build_resilient_async_http_client, build_resilient_http_client

    base_t = timeout if timeout is not None else float(os.environ.get("OPENAI_TIMEOUT", 120.0))
    min_floor = 600.0 if creds.reasoning_effort in ("medium", "high", "max", "xhigh") else 120.0
    eff_t = remaining_timeout(max(base_t, min_floor))
    eff_r = max_retries if max_retries is not None else int(os.environ.get("OPENAI_MAX_RETRIES", 1))
    http_c = (build_resilient_async_http_client if is_async else build_resilient_http_client)(timeout=eff_t, max_retries=eff_r)
    kw: Dict[str, Any] = {"api_key": creds.api_key, "timeout": eff_t, "max_retries": eff_r}
    if http_c is not None:
        kw["http_client"] = http_c
    for attr in ("base_url", "organization", "project"):
        if getattr(creds, attr, None):
            kw[attr] = getattr(creds, attr)
    return kw


def get_openai_client(
    allow_mock: bool = False, model: Optional[str] = None, reasoning_effort: Optional[str] = None,
    base_url: Optional[str] = None, organization: Optional[str] = None, project: Optional[str] = None,
    timeout: Optional[float] = None, max_retries: Optional[int] = None, api_key: Optional[str] = None,
) -> Tuple[Optional[Any], OpenAICredentials]:
    creds = resolve_openai_credentials(allow_mock=allow_mock, model=model, reasoning_effort=reasoning_effort, base_url=base_url, organization=organization, project=project, api_key=api_key)
    if creds.is_mock or not creds.api_key:
        return None, creds
    try:
        from openai import OpenAI
        return _guard_client_completions(OpenAI(**_build_openai_client_kwargs(creds, timeout, max_retries, False)), creds.model), creds
    except ImportError as err:
        if not allow_mock:
            raise RuntimeError(f"openai package is required but not installed: {err}") from err
        return None, OpenAICredentials(None, True, creds.model, creds.reasoning_effort, "[IMPORT_ERROR_MOCK]", "mock_fallback")


def get_async_openai_client(
    allow_mock: bool = False, model: Optional[str] = None, reasoning_effort: Optional[str] = None,
    base_url: Optional[str] = None, organization: Optional[str] = None, project: Optional[str] = None,
    timeout: Optional[float] = None, max_retries: Optional[int] = None, api_key: Optional[str] = None,
) -> Tuple[Optional[Any], OpenAICredentials]:
    creds = resolve_openai_credentials(allow_mock=allow_mock, model=model, reasoning_effort=reasoning_effort, base_url=base_url, organization=organization, project=project, api_key=api_key)
    if creds.is_mock or not creds.api_key:
        return None, creds
    try:
        from openai import AsyncOpenAI
        return _guard_client_completions(AsyncOpenAI(**_build_openai_client_kwargs(creds, timeout, max_retries, True)), creds.model), creds
    except ImportError as err:
        if not allow_mock:
            raise RuntimeError(f"openai package is required but not installed: {err}") from err
        return None, OpenAICredentials(None, True, creds.model, creds.reasoning_effort, "[IMPORT_ERROR_MOCK]", "mock_fallback")


class CredentialManager:
    """Governed Single Source of Truth for multi-provider API keys, gateway configs, and client pools."""

    @classmethod
    def get_credential(cls, key_name: str, default: Optional[str] = None) -> Optional[str]:
        ensure_ssot_env_loaded()
        val = os.environ.get(key_name)
        return val.strip() if val is not None and val.strip() else default

    get_secret = get_credential
    get_env = get_credential

    @classmethod
    def get_github_token(cls) -> Optional[str]:
        return cls.get_credential("GITHUB_TOKEN") or cls.get_credential("GH_TOKEN")

    @classmethod
    def get_hf_token(cls) -> Optional[str]:
        return cls.get_credential("HF_TOKEN") or cls.get_credential("HUGGING_FACE_HUB_TOKEN")

    @classmethod
    def load_local_env_key(cls, env_path: Union[str, Path], key_name: str) -> Optional[str]:
        from config.secret_providers import EnvFileSecretProvider
        p = Path(env_path)
        val = EnvFileSecretProvider(p).get_secret(key_name) if p.is_file() else None
        return val.strip() if val and val.strip() else None

    @classmethod
    def has_credential(cls, key_name: str) -> bool:
        ensure_ssot_env_loaded()
        return key_name in os.environ

    @classmethod
    def set_credential(cls, key_name: str, value: str) -> None:
        ensure_ssot_env_loaded()
        os.environ[key_name] = value

    @classmethod
    def set_credentials(cls, mapping: Mapping[str, str]) -> None:
        ensure_ssot_env_loaded()
        os.environ.update(mapping)

    @classmethod
    def prepend_path(cls, path_dir: Union[str, Path]) -> None:
        p_str, cur = str(path_dir), os.environ.get("PATH", "")
        if p_str not in (cur.split(os.pathsep) if cur else []):
            os.environ.update({"PATH": f"{p_str}{os.pathsep}{cur}" if cur else p_str})

    @classmethod
    def get_process_environment(cls) -> Dict[str, str]:
        ensure_ssot_env_loaded()
        return dict(os.environ)

    @classmethod
    def is_local_execution_mode(cls) -> bool:
        ensure_ssot_env_loaded()
        return os.environ.get("LOCAL_EXECUTION_MODE", "").strip().lower() in ("true", "1", "yes")

    @classmethod
    @contextmanager
    def temporary_env(cls, updates: Mapping[str, str]) -> Iterator[None]:
        ensure_ssot_env_loaded()
        orig = {k: os.environ.get(k) for k in updates}
        os.environ.update(updates)
        try:
            yield
        finally:
            for k, v in orig.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    @staticmethod
    def get_openai_api_key(allow_mock: bool = False) -> Optional[str]:
        return resolve_openai_credentials(allow_mock=allow_mock).api_key

    @staticmethod
    def get_anthropic_api_key(allow_mock: bool = False) -> Optional[str]:
        return resolve_anthropic_credentials(allow_mock=allow_mock).api_key

    @staticmethod
    def get_google_api_key(allow_mock: bool = False) -> Optional[str]:
        return resolve_google_credentials(allow_mock=allow_mock).api_key

    get_openai_client = staticmethod(get_openai_client)
    get_async_openai_client = staticmethod(get_async_openai_client)
    sanitize_call_kwargs = staticmethod(sanitize_model_call_kwargs)
    mask = staticmethod(mask_secret)
    ensure_ssot_env_loaded = staticmethod(ensure_ssot_env_loaded)
    refresh_credentials = staticmethod(refresh_credentials)
    invalidate_credentials = staticmethod(invalidate_credentials)

    @classmethod
    def has_live_openai_key(cls) -> bool:
        ensure_ssot_env_loaded()
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())


ensure_ssot_env_loaded()
__all__ = [
    "CredentialManager", "OpenAICredentials", "AnthropicCredentials", "GoogleCredentials",
    "LocalEmbeddingCredentials", "resolve_openai_credentials", "resolve_anthropic_credentials",
    "resolve_google_credentials", "resolve_local_embedding_credentials", "get_openai_client",
    "get_async_openai_client", "sanitize_model_call_kwargs", "canonicalize_model_name", "mask_secret",
    "ensure_ssot_env_loaded", "refresh_credentials", "invalidate_credentials", "resolve_ssot_env_file",
    "GOVERNED_MODELS", "OPENAI_APPROVED_MODELS", "GOOGLE_APPROVED_MODELS", "DEFAULT_GEMINI_FAILOVER_MODEL",
    "DEFAULT_GEMINI_PRO_MODEL", "X1_CHALLENGER_DEFAULT_MODEL", "X1_JUDGE_DEFAULT_MODEL", "MODEL_ALIASES",
    "REASONING_MODEL_PREFIXES", "O200K_MODEL_PREFIXES", "CENTRAL_SSOT_FILE", "SSOT_ENV_FILE",
]
