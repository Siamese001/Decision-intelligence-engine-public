"""
Local MLX Model Runtime for Apple Silicon Metal.

Provides unified interface to load, govern, and execute local open-weights models
defined in `dual_pipeline/config/local_models.yaml`. Supports hermetic mock mode
for zero-hardware CI pipelines and real Apple Metal execution via `mlx_lm`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from config.credentials import CredentialManager
from dual_pipeline.model_registry import resolve_acoustic_asr
from dual_pipeline.parameters import get_local_mlx_model

from .memory_governor import MemoryGovernor, get_memory_governor

logger = logging.getLogger("dual_pipeline.runtime.model_runtime")

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "local_models.yaml"


@dataclass(frozen=True)
class ModelInferenceResult:
    """Structured inference response from local model execution."""
    content: str
    model_key: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    is_mock: bool
    parsed_json: Optional[Dict[str, Any]] = None

    def to_performance_record(self) -> Dict[str, Any]:
        """Converts result to performance evidence record, strictly rejecting mock runs."""
        if self.is_mock:
            raise ValueError(
                "Mock inference result rejected: mock execution is strictly excluded from "
                "performance evidence and benchmark receipts."
            )
        return {
            "model_key": self.model_key,
            "model_id": self.model_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "finish_reason": self.finish_reason,
            "is_mock": False,
        }


class LocalModelRuntime:
    """Orchestrates local model loading, memory reservations, and generation."""

    def __init__(
        self,
        config_path: Path = CONFIG_PATH,
        governor: Optional[MemoryGovernor] = None,
        mock_mode: bool = False,
    ):
        self.config_path = Path(config_path)
        self.governor = governor if governor is not None else get_memory_governor()
        if mock_mode or CredentialManager.get_credential("LOCAL_MODEL_MOCK", "").lower() in ("1", "true"):
            raise RuntimeError(
                "MOCK_MODEL_PROHIBITED: LocalModelRuntime mock execution is prohibited; "
                "authentic Apple Silicon Metal execution via mlx_lm is required."
            )
        self.mock_mode = False
        self._lock = threading.RLock()
        self._incarnation_id = str(uuid.uuid4().hex[:12])
        self._load_locks: Dict[str, threading.Lock] = {}
        self._load_locks_guard = threading.Lock()
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._loaded_models: Dict[str, Any] = {}
        self._loaded_tokenizers: Dict[str, Any] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        """Loads canonical local model definitions from YAML."""
        if not self.config_path.exists():
            logger.warning(f"Model config not found at {self.config_path}. Using minimal default registry.")
            self._registry = {
                "llama_3_1_8b_instruct": {"model_id": get_local_mlx_model("instruct_8b"), "memory_footprint_gb": 5.5},
                "qwen_2_5_14b_instruct": {"model_id": get_local_mlx_model("instruct_14b"), "memory_footprint_gb": 9.2},
                "qwen_2_5_32b_instruct": {"model_id": get_local_mlx_model("instruct_32b"), "memory_footprint_gb": 19.5},
                "deepseek_r1_distill_qwen_14b": {"model_id": get_local_mlx_model("reasoning_14b"), "memory_footprint_gb": 9.5},
            }
            return

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            self._registry = data.get("models", {})
        # Inject SSOT Acoustic Model dynamically
        try:
            asr_pin = resolve_acoustic_asr()
            self._registry["acoustic_mlx_whisper"] = {
                "model_id": asr_pin.model_id,
                "memory_footprint_gb": 2.5,
                "format": "mlx-fp16",
                "supports_metal": True
            }
        except Exception as e:
            logger.warning(f"Could not resolve SSOT acoustic model: {e}")

        logger.info(f"Loaded {len(self._registry)} local model definition(s) from {self.config_path}.")

    def get_model_spec(self, model_key: str) -> Dict[str, Any]:
        """Returns model specification or raises KeyError."""
        if model_key not in self._registry and model_key == "deepseek_r1_distill_14b":
            model_key = "deepseek_r1_distill_qwen_14b"
        if model_key not in self._registry:
            raise KeyError(f"Model key '{model_key}' not defined in local model registry.")
        return self._registry[model_key]

    def load_model(self, model_key: str) -> bool:
        """Loads a model into unified memory with single-flight synchronization."""
        with self._load_locks_guard:
            if model_key not in self._load_locks:
                self._load_locks[model_key] = threading.Lock()
            key_lock = self._load_locks[model_key]

        with key_lock:
            with self._lock:
                if model_key in self._loaded_models:
                    return True

            spec = self.get_model_spec(model_key)
            footprint = float(spec.get("memory_footprint_gb", 4.0))

            # Register memory reservation with process and incarnation binding
            self.governor.register_allocation(
                model_key=model_key,
                required_gb=footprint,
                process_id=os.getpid(),
                incarnation_id=self._incarnation_id,
                metadata={"model_id": spec.get("model_id"), "format": spec.get("format")},
            )

            if self.mock_mode:
                raise RuntimeError(f"MOCK_MODEL_PROHIBITED: Mock loading for '{model_key}' is prohibited.")

            try:
                import mlx_lm
                model_id = spec["model_id"]
                logger.info(f"[RUNTIME] Loading MLX model '{model_id}' on Metal GPU...")
                model, tokenizer = mlx_lm.load(model_id)
                with self._lock:
                    self._loaded_models[model_key] = model
                    self._loaded_tokenizers[model_key] = tokenizer
                logger.info(f"[RUNTIME] Successfully loaded '{model_key}' on Metal GPU.")
                return True
            except Exception as e:
                self.governor.release_allocation(model_key, force_reclaim=True)
                logger.error(f"[RUNTIME] Failed loading model '{model_key}': {e}. Reservation rolled back.")
                raise RuntimeError(f"Failed loading model '{model_key}' on Metal GPU: {e}") from e

    def unload_model(self, model_key: str, force: bool = False) -> bool:
        """Unloads a model from memory and releases its memory reservation."""
        with self._lock:
            if model_key not in self._loaded_models:
                return False
            self.governor.release_allocation(model_key, force_reclaim=force)
            del self._loaded_models[model_key]
            self._loaded_tokenizers.pop(model_key, None)
            try:
                import gc
                import mlx.core as mx
                mx.metal.clear_cache()
                gc.collect()
            except Exception:
                pass
            logger.info(f"[RUNTIME] Unloaded model '{model_key}'.")
            return True

    def generate(
        self,
        model_key: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8192,
        response_format: Optional[str] = None,
    ) -> ModelInferenceResult:
        """
        Executes text generation using the specified local model.
        Enforces Parameter Immunity: never passes forbidden temperature parameters to reasoning models.
        """
        spec = self.get_model_spec(model_key)
        self.load_model(model_key)
        self.governor.acquire_in_flight(model_key)

        try:
            is_mock = self.mock_mode or self._loaded_models.get(model_key) == "MOCK_MODEL_OBJECT"

            if is_mock:
                raise RuntimeError(f"MOCK_MODEL_PROHIBITED: Mock generation for '{model_key}' is prohibited; authentic mlx_lm execution is required.")

            return self._generate_mlx(model_key, spec, prompt, system_prompt, max_tokens, response_format)
        finally:
            self.governor.release_in_flight(model_key)

    def _generate_mlx(
        self,
        model_key: str,
        spec: Dict[str, Any],
        prompt: str,
        system_prompt: Optional[str],
        max_tokens: int,
        response_format: Optional[str],
    ) -> ModelInferenceResult:
        """Executes generation using real mlx_lm engine on Apple Metal."""
        import mlx_lm
        model = self._loaded_models[model_key]
        tokenizer = self._loaded_tokenizers[model_key]

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        if hasattr(tokenizer, "apply_chat_template"):
            formatted_prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        else:
            formatted_prompt = f"System: {system_prompt}\nUser: {prompt}\nAssistant:"

        # Parameter immunity: omit temperature to ensure deterministic reasoning
        raw_output = mlx_lm.generate(
            model=model,
            tokenizer=tokenizer,
            prompt=formatted_prompt,
            max_tokens=max_tokens,
            verbose=False,
        )

        parsed_json = None
        if response_format == "json_object":
            parsed_json = self._extract_json(raw_output)

        return ModelInferenceResult(
            content=raw_output,
            model_key=model_key,
            model_id=spec["model_id"],
            prompt_tokens=len(formatted_prompt.split()),
            completion_tokens=len(raw_output.split()),
            finish_reason="stop",
            is_mock=False,
            parsed_json=parsed_json,
        )

    def _generate_mock(
        self,
        model_key: str,
        spec: Dict[str, Any],
        prompt: str,
        system_prompt: Optional[str],
        response_format: Optional[str],
    ) -> ModelInferenceResult:
        """Returns deterministic, contract-conforming mock responses."""
        raise RuntimeError(f"MOCK_MODEL_PROHIBITED: Mock generation for '{model_key}' is prohibited; authentic mlx_lm execution is required.")

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        """Safely parses JSON substring from markdown fences or text."""
        cleaned = text.strip()
        if "```" in cleaned:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1)
        try:
            return json.loads(cleaned)
        except Exception:
            return None

    def validate_for_performance_evidence(self, result: ModelInferenceResult) -> None:
        """Validates that inference result is eligible for performance evidence."""
        if result.is_mock:
            raise ValueError(
                "Mock inference result rejected: mock execution is strictly excluded from "
                "performance evidence and benchmark receipts."
            )

    def record_performance_evidence(
        self,
        result: ModelInferenceResult,
        elapsed_seconds: float,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Records validated performance evidence, strictly excluding mock results."""
        self.validate_for_performance_evidence(result)
        perf_record = result.to_performance_record()
        perf_record["elapsed_seconds"] = float(elapsed_seconds)
        perf_record["tokens_per_second"] = (
            float(result.completion_tokens) / elapsed_seconds if elapsed_seconds > 0 else 0.0
        )
        if metrics:
            perf_record["metrics"] = metrics
        return perf_record


_GLOBAL_RUNTIME: Optional[LocalModelRuntime] = None
_RUNTIME_LOCK = threading.Lock()


def get_model_runtime(mock_mode: bool = False) -> LocalModelRuntime:
    """Returns singleton LocalModelRuntime instance."""
    global _GLOBAL_RUNTIME
    with _RUNTIME_LOCK:
        if _GLOBAL_RUNTIME is None:
            _GLOBAL_RUNTIME = LocalModelRuntime(mock_mode=mock_mode)
        return _GLOBAL_RUNTIME
