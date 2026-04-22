from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from speaches.audio import Audio
from speaches.executors.kokoro import (
    SAMPLE_RATE,
    VOICES,
    KokoroModel,
    normalize_text_for_tts,
    split_text_into_chunks,
)
from speaches.executors.shared.base_model_manager import BaseModelManager
from speaches.hf_utils import HfModelFilter, get_cached_model_repos_info
from speaches.model_registry import ModelRegistry
from speaches.tracing import traced_generator

if TYPE_CHECKING:
    from collections.abc import Generator

    from kokoro import KModel, KPipeline

    from speaches.executors.shared.handler_protocol import SpeechRequest, SpeechResponse

logger = logging.getLogger(__name__)

SUPPORTED_MODELS = {"hexgrad/Kokoro-82M"}
TASK_NAME_TAG = "text-to-speech"

# Dummy filter — not used for matching, only satisfies the Executor type.
# Actual model matching is done via SUPPORTED_MODELS in list_local_models().
hf_model_filter = HfModelFilter(model_name="hexgrad/Kokoro", task=TASK_NAME_TAG)


class KokoroPytorchModelState:
    def __init__(self, model: KModel, repo_id: str, device: str) -> None:
        self.model = model
        self.repo_id = repo_id
        self.device = device
        self._pipelines: dict[str, KPipeline] = {}

    def get_pipeline(self, lang_code: str) -> KPipeline:
        if lang_code not in self._pipelines:
            from kokoro import KPipeline as _KPipeline

            self._pipelines[lang_code] = _KPipeline(
                lang_code=lang_code,
                repo_id=self.repo_id,
                model=self.model,
                device=self.device,
            )
        return self._pipelines[lang_code]


class KokoroPytorchModelRegistry(ModelRegistry):
    def __init__(self) -> None:
        super().__init__(hf_model_filter=hf_model_filter)

    def list_remote_models(self) -> Generator[KokoroModel]:
        for model_id in SUPPORTED_MODELS:
            yield KokoroModel(
                id=model_id,
                created=0,
                owned_by=model_id.split("/")[0],
                language=["en"],
                task=TASK_NAME_TAG,
                sample_rate=SAMPLE_RATE,
                voices=VOICES,
            )

    def list_local_models(self) -> Generator[KokoroModel]:
        cached_repos = get_cached_model_repos_info()
        cached_ids = {info.repo_id for info in cached_repos}
        for model_id in SUPPORTED_MODELS:
            if model_id in cached_ids:
                yield KokoroModel(
                    id=model_id,
                    created=0,
                    owned_by=model_id.split("/")[0],
                    language=["en"],
                    task=TASK_NAME_TAG,
                    sample_rate=SAMPLE_RATE,
                    voices=VOICES,
                )

    def get_model_files(self, model_id: str) -> None:  # noqa: ARG002
        # KPipeline handles file resolution internally via repo_id
        return None

    def download_model_files(self, model_id: str) -> None:
        import huggingface_hub

        huggingface_hub.snapshot_download(repo_id=model_id, repo_type="model")


kokoro_pytorch_model_registry = KokoroPytorchModelRegistry()


class KokoroPytorchModelManager(BaseModelManager[KokoroPytorchModelState]):
    def __init__(self, ttl: int, device: str = "cuda") -> None:
        super().__init__(ttl)
        self.device = device

    def _load_fn(self, model_id: str) -> KokoroPytorchModelState:
        from kokoro import KModel

        model = KModel(repo_id=model_id)
        return KokoroPytorchModelState(model, model_id, self.device)

    @traced_generator()
    def handle_speech_request(
        self,
        request: SpeechRequest,
        **_kwargs,
    ) -> SpeechResponse:
        from speaches.api_types import OPENAI_SUPPORTED_SPEECH_VOICE_NAMES

        if request.speed < 0.5 or request.speed > 2.0:
            msg = f"Speed must be between 0.5 and 2.0, got {request.speed}"
            raise ValueError(msg)
        if request.voice not in [v.name for v in VOICES]:
            if request.voice in OPENAI_SUPPORTED_SPEECH_VOICE_NAMES:
                logger.warning(
                    f"Voice '{request.voice}' is not supported by the model '{request.model}'. "
                    f"It will be replaced with '{VOICES[0].name}'."
                )
                request.voice = VOICES[0].name
            else:
                msg = f"Voice '{request.voice}' is not supported. Supported voices: {VOICES}"
                raise ValueError(msg)

        normalized_text = normalize_text_for_tts(request.text)
        chunks = split_text_into_chunks(normalized_text)

        if not chunks:
            return

        lang_code = request.voice[0]

        with self.load_model(request.model) as state:
            pipeline = state.get_pipeline(lang_code)
            start = time.perf_counter()
            for chunk in chunks:
                for result in pipeline(chunk, voice=request.voice, speed=request.speed):
                    if result.audio is not None:
                        audio_np = result.audio.detach().cpu().float().numpy()
                        yield Audio(audio_np, sample_rate=SAMPLE_RATE)

        logger.info(f"Generated audio for {len(request.text)} characters in {time.perf_counter() - start}s")
