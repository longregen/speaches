from __future__ import annotations

from collections import OrderedDict
import logging
import threading
from typing import TYPE_CHECKING

from faster_whisper import WhisperModel

from speaches.model_manager import SelfDisposingModel

if TYPE_CHECKING:
    from speaches.config import (
        WhisperConfig,
    )

logger = logging.getLogger(__name__)


# TODO: enable concurrent model downloads


class WhisperModelManager:
    def __init__(self, whisper_config: WhisperConfig) -> None:
        self.whisper_config = whisper_config
        self.loaded_models: OrderedDict[str, SelfDisposingModel[WhisperModel]] = OrderedDict()
        self._lock = threading.Lock()

    def _load_fn(self, model_id: str) -> WhisperModel:
        # Check if we're in offline mode and need to use local files
        import os
        from speaches.hf_utils import get_model_repo_path
        
        # If HF_HUB_OFFLINE is set, try to use the local path directly
        if os.environ.get("HF_HUB_OFFLINE", False) != False:
            model_repo_path = get_model_repo_path(model_id)
            if model_repo_path:
                # Find the snapshot directory
                snapshots_dir = model_repo_path / "snapshots"
                if snapshots_dir.exists():
                    # Get the first (and likely only) snapshot
                    snapshot_dirs = list(snapshots_dir.iterdir())
                    if snapshot_dirs:
                        # Use the snapshot path directly
                        logger.info(f"Using local snapshot path for {model_id}: {snapshot_dirs[0]}")
                        return WhisperModel(
                            str(snapshot_dirs[0]),
                            device=self.whisper_config.inference_device,
                            device_index=self.whisper_config.device_index,
                            compute_type=self.whisper_config.compute_type,
                            cpu_threads=self.whisper_config.cpu_threads,
                            num_workers=self.whisper_config.num_workers,
                            local_files_only=True,
                        )
        
        # Default behavior - let WhisperModel handle the download
        return WhisperModel(
            model_id,
            device=self.whisper_config.inference_device,
            device_index=self.whisper_config.device_index,
            compute_type=self.whisper_config.compute_type,
            cpu_threads=self.whisper_config.cpu_threads,
            num_workers=self.whisper_config.num_workers,
        )

    def _handle_model_unloaded(self, model_id: str) -> None:
        with self._lock:
            if model_id in self.loaded_models:
                del self.loaded_models[model_id]

    def unload_model(self, model_id: str) -> None:
        with self._lock:
            model = self.loaded_models.get(model_id)
            if model is None:
                raise KeyError(f"Model {model_id} not found")
            # WARN: ~300 MB of memory will still be held by the model. See https://github.com/SYSTRAN/faster-whisper/issues/992
            self.loaded_models[model_id].unload()

    def load_model(self, model_id: str) -> SelfDisposingModel[WhisperModel]:
        logger.debug(f"Loading model {model_id}")
        with self._lock:
            logger.debug("Acquired lock")
            if model_id in self.loaded_models:
                logger.debug(f"{model_id} model already loaded")
                return self.loaded_models[model_id]
            self.loaded_models[model_id] = SelfDisposingModel[WhisperModel](
                model_id,
                load_fn=lambda: self._load_fn(model_id),
                ttl=self.whisper_config.ttl,
                model_unloaded_callback=self._handle_model_unloaded,
            )
            return self.loaded_models[model_id]
