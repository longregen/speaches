from collections.abc import Generator
import logging
import threading
import time
from typing import Any, cast

import huggingface_hub
import numpy as np
from pydantic import BaseModel, computed_field

from speaches.api_types import (
    OPENAI_SUPPORTED_SPEECH_VOICE_NAMES,
    Model,
)
from speaches.audio import Audio
from speaches.executors.shared.base_model_manager import BaseModelManager
from speaches.executors.shared.handler_protocol import SpeechRequest, SpeechResponse
from speaches.hf_utils import (
    HfModelFilter,
    extract_language_list,
    get_cached_model_repos_info,
    get_model_card_data_from_cached_repo_info,
)
from speaches.model_registry import ModelRegistry
from speaches.tracing import traced_generator

try:
    from pocket_tts import TTSModel
    import torch  # noqa: F401 -- must be imported before ctranslate2 to avoid OpenMP segfault

    POCKET_TTS_AVAILABLE = True
except ImportError:
    POCKET_TTS_AVAILABLE = False

SAMPLE_RATE = 24000
LIBRARY_NAME = "pocket-tts"
TASK_NAME_TAG = "text-to-speech"

PREDEFINED_VOICE_NAMES = [
    "alba",
    "marius",
    "javert",
    "jean",
    "cosette",
    "fantine",
    "eponine",
    "azelma",
    "anna",
    "vera",
    "charles",
    "paul",
    "george",
    "mary",
    "jane",
    "michael",
    "eve",
    "bill_boerst",
    "peter_yearsley",
    "stuart_bell",
    "caro_davy",
]

logger = logging.getLogger(__name__)


class PocketTTSModelVoice(BaseModel):
    name: str

    @computed_field
    @property
    def id(self) -> str:
        return self.name


VOICES = [PocketTTSModelVoice(name=name) for name in PREDEFINED_VOICE_NAMES]


class PocketTTSModel(Model):
    sample_rate: int
    voices: list[PocketTTSModelVoice]


KNOWN_MODELS: dict[str, list[str]] = {
    "kyutai/pocket-tts-without-voice-cloning": ["en", "fr", "es", "de", "it", "pt", "zh", "ja", "ko"],
}

hf_model_filter = HfModelFilter(
    library_name=LIBRARY_NAME,
    # NOTE: kyutai/pocket-tts-without-voice-cloning has no pipeline_tag set on HuggingFace,
    # so we only filter by library_name to ensure discovery works
)


class PocketTTSModelRegistry(ModelRegistry):
    def list_remote_models(self) -> Generator[PocketTTSModel]:
        models = huggingface_hub.list_models(**self.hf_model_filter.list_model_kwargs(), cardData=True)
        for model in models:
            if model.created_at is None or model.card_data is None:
                continue
            yield PocketTTSModel(
                id=model.id,
                created=int(model.created_at.timestamp()),
                owned_by=model.id.split("/")[0],
                language=extract_language_list(model.card_data),
                task=TASK_NAME_TAG,
                sample_rate=SAMPLE_RATE,
                voices=VOICES,
            )

    def list_local_models(self) -> Generator[PocketTTSModel]:
        cached_model_repos_info = get_cached_model_repos_info()
        seen_ids: set[str] = set()
        for cached_repo_info in cached_model_repos_info:
            model_card_data = get_model_card_data_from_cached_repo_info(cached_repo_info)
            if model_card_data is not None and self.hf_model_filter.passes_filter(
                cached_repo_info.repo_id, model_card_data
            ):
                seen_ids.add(cached_repo_info.repo_id)
                yield PocketTTSModel(
                    id=cached_repo_info.repo_id,
                    created=int(cached_repo_info.last_modified),
                    owned_by=cached_repo_info.repo_id.split("/")[0],
                    language=extract_language_list(model_card_data),
                    task=TASK_NAME_TAG,
                    sample_rate=SAMPLE_RATE,
                    voices=VOICES,
                )
        for cached_repo_info in cached_model_repos_info:
            if cached_repo_info.repo_id in seen_ids:
                continue
            if cached_repo_info.repo_id in KNOWN_MODELS:
                seen_ids.add(cached_repo_info.repo_id)
                yield PocketTTSModel(
                    id=cached_repo_info.repo_id,
                    created=int(cached_repo_info.last_modified),
                    owned_by=cached_repo_info.repo_id.split("/")[0],
                    language=KNOWN_MODELS[cached_repo_info.repo_id],
                    task=TASK_NAME_TAG,
                    sample_rate=SAMPLE_RATE,
                    voices=VOICES,
                )

    def get_model_files(self, model_id: str) -> None:
        huggingface_hub.hf_hub_download(
            repo_id=model_id,
            filename="tts_b6369a24.safetensors",
            local_files_only=True,
        )

    def download_model_files(self, model_id: str) -> None:
        huggingface_hub.snapshot_download(repo_id=model_id, repo_type="model")


pocket_tts_model_registry = PocketTTSModelRegistry(hf_model_filter=hf_model_filter)


if POCKET_TTS_AVAILABLE:

    class PocketTTSModelManager(BaseModelManager["TTSModel"]):
        def __init__(self, ttl: int) -> None:
            super().__init__(ttl)
            self._inference_lock = threading.Lock()

        def _load_fn(self, model_id: str) -> "TTSModel":  # noqa: ARG002
            return TTSModel.load_model()

        @traced_generator()
        def handle_speech_request(
            self,
            request: SpeechRequest,
            **_kwargs,
        ) -> SpeechResponse:
            if request.voice not in PREDEFINED_VOICE_NAMES:
                if request.voice in OPENAI_SUPPORTED_SPEECH_VOICE_NAMES:
                    logger.warning(
                        f"Voice '{request.voice}' is not supported by pocket-tts. Replacing with '{PREDEFINED_VOICE_NAMES[0]}'."
                    )
                    request.voice = PREDEFINED_VOICE_NAMES[0]
                else:
                    msg = f"Voice '{request.voice}' is not supported. Supported voices: {PREDEFINED_VOICE_NAMES}"
                    raise ValueError(msg)

            if request.speed != 1.0:
                logger.warning("pocket-tts does not support speed adjustment, ignoring speed parameter")

            text = request.text.strip()
            if not text:
                return

            with self._inference_lock, self.load_model(request.model) as tts:
                tts_any = cast("Any", tts)
                voice_state = tts_any.get_state_for_audio_prompt(request.voice)
                start = time.perf_counter()
                stream = tts_any.generate_audio_stream(
                    model_state=voice_state,
                    text_to_generate=text,
                    copy_state=True,
                )
                for audio_chunk in stream:
                    audio_data = audio_chunk.numpy().astype(np.float32)
                    yield Audio(audio_data, sample_rate=tts.sample_rate)

            logger.info(f"Generated audio for {len(request.text)} characters in {time.perf_counter() - start}s")
