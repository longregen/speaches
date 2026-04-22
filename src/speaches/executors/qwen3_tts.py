from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any

# Privacy: disable HF hub telemetry and gradio analytics BEFORE anything imports them.
# HF hub otherwise pings api.huggingface.co with a usage UUID on every model load.
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import huggingface_hub
from pydantic import BaseModel, computed_field

from speaches.api_types import OPENAI_SUPPORTED_SPEECH_VOICE_NAMES, Model
from speaches.audio import Audio
from speaches.executors.kokoro import normalize_text_for_tts, split_text_into_chunks
from speaches.executors.shared.base_model_manager import BaseModelManager
from speaches.hf_utils import HfModelFilter, get_cached_model_repos_info
from speaches.model_registry import ModelRegistry
from speaches.tracing import traced_generator

try:
    import torch  # noqa: F401 -- must be imported before ctranslate2 to avoid OpenMP segfault

    # transformers >=5.0 has a bug in `TokenizersBackend.__init__`: the call
    # `self._patch_mistral_regex(..., fix_mistral_regex=kwargs.get(...), **kwargs)`
    # duplicates the `fix_mistral_regex` kwarg whenever the flag is present in
    # `kwargs` (from tokenizer_config.json via `_from_pretrained`), raising a
    # TypeError at the Python arg-binding layer — before any wrapper body runs.
    # The fix has to be at the call site, so we rewrite the original __init__
    # source: replace `**kwargs,` in the `_patch_mistral_regex(...)` call with
    # a filtered spread that excludes `fix_mistral_regex`.
    from transformers import tokenization_utils_tokenizers as _ttk

    _BACKEND_CLS = _ttk.TokenizersBackend
    if not getattr(_BACKEND_CLS.__init__, "_speaches_patched", False):
        import inspect as _inspect
        import textwrap as _textwrap

        _orig_src = _textwrap.dedent(_inspect.getsource(_BACKEND_CLS.__init__))
        _needle = 'fix_mistral_regex=kwargs.get("fix_mistral_regex"),\n            **kwargs,'
        _replacement = (
            'fix_mistral_regex=kwargs.get("fix_mistral_regex"),\n'
            '            **{_k: _v for _k, _v in kwargs.items() if _k != "fix_mistral_regex"},'
        )
        if _needle in _orig_src:
            _patched_src = _orig_src.replace(_needle, _replacement, 1)
            # `super()` without args needs a `__class__` cell, which is only created
            # inside a class body. Exec'd free functions don't get one, so rewrite
            # the zero-arg `super()` to an explicit two-arg call bound to the class.
            _patched_src = _patched_src.replace(
                "super().__init__(**kwargs)",
                "super(_SpeachesBackendCls, self).__init__(**kwargs)",
                1,
            )
            _patched_src = _patched_src.replace("def __init__(self", "def _speaches_patched_init(self", 1)
            _ns: dict[str, Any] = {"_SpeachesBackendCls": _BACKEND_CLS}
            exec(  # noqa: S102
                compile(_patched_src, "<speaches qwen3_tts patched __init__>", "exec"),
                {**_ttk.__dict__, "_SpeachesBackendCls": _BACKEND_CLS},
                _ns,
            )
            _ns["_speaches_patched_init"]._speaches_patched = True  # noqa: SLF001
            _BACKEND_CLS.__init__ = _ns["_speaches_patched_init"]  # type: ignore[method-assign]
        else:
            logging.getLogger(__name__).warning(
                "Qwen3-TTS: could not apply TokenizersBackend init patch; mistral-regex warning expected"
            )

    from qwen_tts import Qwen3TTSModel

    QWEN3_TTS_AVAILABLE = True
except ImportError:
    QWEN3_TTS_AVAILABLE = False

if TYPE_CHECKING:
    from collections.abc import Generator

    from speaches.executors.shared.handler_protocol import SpeechRequest, SpeechResponse

logger = logging.getLogger(__name__)

TASK_NAME_TAG = "text-to-speech"
_VARIANT_CUSTOM = "custom_voice"
_VARIANT_DESIGN = "voice_design"

_CUSTOM_VOICE_MODEL_IDS = frozenset(
    {
        "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    }
)
_VOICE_DESIGN_MODEL_IDS = frozenset({"Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"})
SUPPORTED_MODEL_IDS = _CUSTOM_VOICE_MODEL_IDS | _VOICE_DESIGN_MODEL_IDS

# Preset speakers on the CustomVoice variants, mapped to their native language
# (https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice#supported-speakers).
_CUSTOM_VOICE_LANGUAGES: dict[str, str] = {
    "Vivian": "Chinese",
    "Serena": "Chinese",
    "Uncle_Fu": "Chinese",
    "Dylan": "Chinese",
    "Eric": "Chinese",
    "Ryan": "English",
    "Aiden": "English",
    "Ono_Anna": "Japanese",
    "Sohee": "Korean",
}

_SUPPORTED_LANGUAGES = sorted(
    ["Chinese", "English", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian"]
)

# Dummy filter — discovery is driven by SUPPORTED_MODEL_IDS in list_local_models().
hf_model_filter = HfModelFilter(model_name="Qwen/Qwen3-TTS", task=TASK_NAME_TAG)


class Qwen3TTSModelVoice(BaseModel):
    name: str

    @computed_field
    @property
    def id(self) -> str:
        return self.name


_CUSTOM_VOICE_VOICES = [Qwen3TTSModelVoice(name=v) for v in _CUSTOM_VOICE_LANGUAGES]


class Qwen3TTSModelInfo(Model):
    sample_rate: int
    voices: list[Qwen3TTSModelVoice]


def _variant_for(model_id: str) -> str:
    if model_id in _CUSTOM_VOICE_MODEL_IDS:
        return _VARIANT_CUSTOM
    if model_id in _VOICE_DESIGN_MODEL_IDS:
        return _VARIANT_DESIGN
    msg = f"Unsupported Qwen3-TTS model id: {model_id!r}"
    raise ValueError(msg)


# Generation kwargs — aligned with upstream's own FAQ stability recipe
# (https://www.mintlify.com/QwenLM/Qwen3-TTS/resources/faq): temperature=0.7,
# top_p=0.9, repetition_penalty=1.1. The ComfyUI-Qwen3-TTS community also
# recommends lowering `max_new_tokens` from 2048 to 1024 to bound the blast
# radius of the known EOS-non-emission bug (upstream issue #118). Greedy
# decoding loops forever — avoid. Retry logic below catches the residual
# runaway cases.
_GEN_KWARGS: dict[str, Any] = {
    "temperature": 0.7,
    "top_p": 0.9,
    "subtalker_top_p": 0.9,
    "repetition_penalty": 1.1,
    "max_new_tokens": 1024,
}
# Runaway-detection thresholds. English speech averages ~15 chars/s; we allow 4x
# before declaring runaway (~0.25 s/char) plus a floor so short inputs have room.
# Empirically, Qwen3-TTS-12Hz-1.7B-CustomVoice runs away on ~30-50% of seeds
# regardless of sampling parameters (tested top_p in 0.5..1.0, temperature in
# 0.7..0.9) due to upstream issue #118 (EOS not emitted). Four retries reduce
# the user-visible failure rate to <=4% at worst case (0.5^5 = 3.125%).
_SECONDS_PER_CHAR_CAP = 0.25
_MIN_AUDIO_BUDGET_SECONDS = 6.0
_MAX_RETRIES = 4


_MISTRAL_SPLIT_REGEX = (
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+"
    r"|[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*"
    r"|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n/]*|\s*[\r\n]+|\s+(?!\S)|\s+"
)


def _trim_trailing_silence(samples: Any, sample_rate: int, top_db: float = 25.0, pad_ms: int = 200) -> Any:
    """Trim trailing silence/low-energy padding from a mono float32 waveform.

    Qwen3-TTS does not reliably emit EOS — it generates the target speech then
    fills the remaining `max_new_tokens` budget with low-energy audio (hum,
    rustle) that can sit several dB above pure silence. We use librosa's
    `effects.trim` (a dB-threshold trim with hysteresis) to find where the
    signal drops `top_db` below its peak, which robustly catches the end of
    speech, and keep `pad_ms` of tail so the last word doesn't clip.
    """
    import librosa  # type: ignore[import-untyped]
    import numpy as np

    if samples.size == 0:
        return samples
    try:
        _, (_, end) = librosa.effects.trim(samples.astype(np.float32), top_db=top_db)
    except Exception:  # noqa: BLE001 -- librosa can fail on degenerate arrays; keep untrimmed.
        return samples
    end = int(end) + int(pad_ms * sample_rate / 1000)
    return samples[: min(end, samples.size)]


def _apply_mistral_regex_fix(tokenizer: Any) -> bool:
    """Apply the mistral pre-tokenizer regex fix directly on a loaded tokenizer.

    `transformers.tokenization_utils_tokenizers._patch_mistral_regex` has two bugs
    in 5.x (duplicate-kwarg TypeError, and an AttributeError when invoked with the
    raw `tokenizers.Tokenizer`), so we apply the fix ourselves from
    `processor.tokenizer` after `from_pretrained`. Without this the Qwen3-TTS
    text encoder mis-tokenizes input and the model runs unbounded to
    `max_new_tokens`, producing minutes of garbled audio.
    """
    import tokenizers as _tk  # type: ignore[import-untyped]

    backend = getattr(tokenizer, "backend_tokenizer", None) or getattr(tokenizer, "_tokenizer", None)
    if backend is None:
        return False
    split_pretokenizer = _tk.pre_tokenizers.Split(
        pattern=_tk.Regex(_MISTRAL_SPLIT_REGEX),
        behavior="isolated",
    )
    current = backend.pre_tokenizer
    if current is None:
        backend.pre_tokenizer = split_pretokenizer
    elif isinstance(current, _tk.pre_tokenizers.Sequence):
        # Replace the first element of the sequence.
        backend.pre_tokenizer[0] = split_pretokenizer
    else:
        if isinstance(current, _tk.pre_tokenizers.Metaspace):
            current = _tk.pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)
        backend.pre_tokenizer = _tk.pre_tokenizers.Sequence([split_pretokenizer, current])
    return True


def _build_model_info(model_id: str) -> Qwen3TTSModelInfo:
    return Qwen3TTSModelInfo(
        id=model_id,
        created=0,
        owned_by=model_id.split("/", maxsplit=1)[0],
        language=_SUPPORTED_LANGUAGES,
        task=TASK_NAME_TAG,
        sample_rate=24000,
        voices=_CUSTOM_VOICE_VOICES if model_id in _CUSTOM_VOICE_MODEL_IDS else [],
    )


class Qwen3TTSModelRegistry(ModelRegistry):
    def list_remote_models(self) -> Generator[Qwen3TTSModelInfo]:
        for mid in SUPPORTED_MODEL_IDS:
            yield _build_model_info(mid)

    def list_local_models(self) -> Generator[Qwen3TTSModelInfo]:
        cached_ids = {info.repo_id for info in get_cached_model_repos_info()}
        for mid in SUPPORTED_MODEL_IDS:
            if mid in cached_ids:
                yield _build_model_info(mid)

    def get_model_files(self, model_id: str) -> None:
        # Verify weights are locally cached; no network call when HF_HUB_OFFLINE=1.
        huggingface_hub.snapshot_download(repo_id=model_id, local_files_only=True)

    def download_model_files(self, model_id: str) -> None:
        huggingface_hub.snapshot_download(repo_id=model_id, repo_type="model")


qwen3_tts_model_registry = Qwen3TTSModelRegistry(hf_model_filter=hf_model_filter)


if QWEN3_TTS_AVAILABLE:

    class Qwen3TTSLoadedModel:
        __slots__ = ("model", "variant")

        def __init__(self, model: Any, variant: str) -> None:
            self.model = model
            self.variant = variant

    class Qwen3TTSModelManager(BaseModelManager["Qwen3TTSLoadedModel"]):
        def __init__(
            self,
            ttl: int,
            device: str = "cuda:0",
            load_in_8bit: bool | None = None,
            default_voice: str = "Ryan",
            default_design_instruct: str = "warm, neutral, natural speaking voice",
        ) -> None:
            super().__init__(ttl)
            self.device = device
            self.load_in_8bit = (
                load_in_8bit
                if load_in_8bit is not None
                else os.environ.get("QWEN3_TTS_LOAD_IN_8BIT", "").lower() in ("1", "true", "yes")
            )
            if default_voice not in _CUSTOM_VOICE_LANGUAGES:
                msg = (
                    f"qwen3_tts_default_voice={default_voice!r} is not a supported speaker. "
                    f"Supported: {sorted(_CUSTOM_VOICE_LANGUAGES)}"
                )
                raise ValueError(msg)
            self.default_voice = default_voice
            self.default_design_instruct = default_design_instruct
            # Qwen3-TTS internals are not thread-safe; serialise inference.
            self._inference_lock = threading.Lock()

        def _load_fn(self, model_id: str) -> Qwen3TTSLoadedModel:
            import torch as _torch

            variant = _variant_for(model_id)
            kwargs: dict[str, Any] = {
                "device_map": self.device,
                "dtype": _torch.bfloat16,
                "attn_implementation": "eager",
            }
            if self.load_in_8bit:
                try:
                    from transformers import BitsAndBytesConfig

                    kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
                    # bitsandbytes manages dtype itself.
                    kwargs.pop("dtype", None)
                    logger.info("Loading Qwen3-TTS %r with bitsandbytes int8", model_id)
                except ImportError:
                    logger.warning("bitsandbytes not installed; loading %r in bfloat16", model_id)

            model = Qwen3TTSModel.from_pretrained(model_id, **kwargs)
            _tok = getattr(getattr(model, "processor", None), "tokenizer", None)
            if _tok is not None and not _apply_mistral_regex_fix(_tok):
                logger.warning(
                    "Qwen3-TTS: failed to apply mistral pre-tokenizer regex fix; "
                    "generation may produce overlong garbled audio"
                )
            return Qwen3TTSLoadedModel(model=model, variant=variant)

        @traced_generator()
        def handle_speech_request(
            self,
            request: SpeechRequest,
            **_kwargs,
        ) -> SpeechResponse:
            # Qwen3-TTS exposes no true streaming audio API; we pseudo-stream by
            # splitting input text at sentence boundaries and yielding one Audio
            # per chunk. First-audio latency is `generate()` on the first chunk.
            if request.speed != 1.0:
                logger.warning("Qwen3-TTS does not support speed adjustment; ignoring speed=%r", request.speed)

            chunks = split_text_into_chunks(normalize_text_for_tts(request.text))
            if not chunks:
                return

            with self._inference_lock, self.load_model(request.model) as state:
                start = time.perf_counter()
                for chunk in chunks:
                    wavs, sr = self._generate_with_retry(state, chunk, request.voice)
                    trimmed = _trim_trailing_silence(wavs[0], int(sr))
                    yield Audio(trimmed, sample_rate=int(sr))

            logger.info("Generated audio for %d characters in %.3fs", len(request.text), time.perf_counter() - start)

        def _generate_with_retry(self, state: Qwen3TTSLoadedModel, text: str, voice: str) -> tuple[list, int]:
            """Generate audio for a text chunk, retrying when Qwen3-TTS fails to emit EOS.

            Qwen3-TTS fails to emit EOS on roughly 1 in 5 runs regardless of sampling
            parameters (tested across top_p=0.7..1.0, greedy, and with/without the
            mistral regex fix). When that happens the model runs to `max_new_tokens`
            and produces minutes of speech-shaped noise. We detect this by comparing
            output duration against a generous per-character budget and retry up to
            `_MAX_RETRIES` times.
            """
            max_audio_seconds = max(_MIN_AUDIO_BUDGET_SECONDS, len(text) * _SECONDS_PER_CHAR_CAP)
            last_wavs: list = []
            last_sr = 24000
            for attempt in range(_MAX_RETRIES + 1):
                if state.variant == _VARIANT_CUSTOM:
                    wavs, sr = self._gen_custom_voice(state.model, text, voice)
                else:
                    wavs, sr = self._gen_voice_design(state.model, text, voice)
                last_wavs, last_sr = wavs, sr
                dur = len(wavs[0]) / sr
                if dur <= max_audio_seconds:
                    return wavs, sr
                logger.warning(
                    "Qwen3-TTS runaway generation (attempt %d/%d): %.1fs for %d chars (cap %.1fs); retrying",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    dur,
                    len(text),
                    max_audio_seconds,
                )
            logger.warning(
                "Qwen3-TTS exhausted %d retries for text %r; returning last (possibly-runaway) output",
                _MAX_RETRIES + 1,
                text[:60],
            )
            return last_wavs, last_sr

        def _gen_custom_voice(self, model: Any, text: str, voice: str) -> tuple[list, int]:
            speaker = self._resolve_speaker(voice)
            language = _CUSTOM_VOICE_LANGUAGES[speaker]
            return model.generate_custom_voice(text=text, speaker=speaker, language=language, **_GEN_KWARGS)

        def _gen_voice_design(self, model: Any, text: str, voice: str) -> tuple[list, int]:
            # VoiceDesign has no preset speakers — the `voice` field carries a
            # natural-language description of the target voice. OpenAI voices
            # and empty strings fall back to the configured default description.
            instruct = (
                voice
                if voice and voice not in OPENAI_SUPPORTED_SPEECH_VOICE_NAMES and voice not in _CUSTOM_VOICE_LANGUAGES
                else self.default_design_instruct
            )
            return model.generate_voice_design(text=text, language="Auto", instruct=instruct, **_GEN_KWARGS)

        def _resolve_speaker(self, voice: str) -> str:
            """Map a request `voice` to a Qwen3-TTS CustomVoice preset speaker.

            - If `voice` is one of the 9 preset names (case-sensitive): use it.
            - If `voice` matches case-insensitively: canonicalise to the preset.
            - Else if `voice` is an OpenAI-compatible voice (e.g. 'alloy'):
              fall back to `Config.qwen3_tts_default_voice` with a warning.
            - Else: raise ValueError with the list of supported speakers.
            """
            if voice in _CUSTOM_VOICE_LANGUAGES:
                return voice
            lower_map = {k.lower(): k for k in _CUSTOM_VOICE_LANGUAGES}
            if voice.lower() in lower_map:
                return lower_map[voice.lower()]
            if voice in OPENAI_SUPPORTED_SPEECH_VOICE_NAMES:
                logger.warning(
                    "Voice %r is not supported by Qwen3-TTS CustomVoice; falling back to %r.",
                    voice,
                    self.default_voice,
                )
                return self.default_voice
            msg = f"Voice {voice!r} is not supported. Supported voices: {sorted(_CUSTOM_VOICE_LANGUAGES)}"
            raise ValueError(msg)
