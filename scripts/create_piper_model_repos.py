import json
from pathlib import Path
import shutil
from time import sleep
from typing import Literal

import huggingface_hub
from pydantic import BaseModel

# https://huggingface.co/docs/datasets/en/dataset_card


class VoiceLanguage(BaseModel):
    code: str
    family: str
    region: str
    name_native: str
    name_english: str
    country_english: str


class VoiceFile(BaseModel):
    size_bytes: int
    md5_digest: str


class Voice(BaseModel):
    key: str
    name: str
    language: VoiceLanguage
    quality: Literal["x_low", "low", "medium", "high"]
    num_speakers: int
    speaker_id_map: dict[str, int]
    files: dict[str, VoiceFile]
    aliases: list[str]


def voice_to_repo(repo_path: Path, voice: Voice) -> None:
    assert len(voice.key.split("-")) == 3, (
        f"Invalid voice key: {voice.key}. Voice key should have 3 parts: <language>-<name>-<quality>"
    )
    assert len(voice.files) == 3, voice.files
    language = voice.language.family
    repo_id = "speaches-ai" + "/" + "piper-" + voice.key
    model_card_data = huggingface_hub.ModelCardData(
        library_name="onnx",
        pipeline_tag="text-to-speech",
        tags=["speaches", "piper"],
        language=language,
    )

    # TODO: add details to to the README.md: dataset links, author attributions, etc.
    content = f"""
---
{model_card_data.to_yaml()}
---

Run this model using [speaches](https://github.com/speaches-ai/speaches)
""".strip()
    model_card = huggingface_hub.ModelCard(content, ignore_metadata_errors=False)

    Path(repo_id).mkdir(parents=True, exist_ok=True)
    model_card.save(Path(repo_id) / "README.md")

    for file_name in voice.files:
        file_path = repo_path / file_name
        if file_name.endswith(".onnx.json"):
            dest_path = Path(repo_id) / "config.json"
        elif file_name.endswith(".onnx"):
            dest_path = Path(repo_id) / "model.onnx"
        else:
            continue
        shutil.copy(file_path, dest_path)

    huggingface_hub.upload_folder(
        repo_id=repo_id,
        folder_path=repo_id,
        commit_message="init",
        repo_type="model",
        create_pr=False,
    )


def main() -> None:
    repo_path = Path(huggingface_hub.snapshot_download("rhasspy/piper-voices"))
    voices_path = repo_path / "voices.json"
    voices_dict = json.loads(voices_path.read_text())
    voices = [Voice.model_validate(voice) for voice in voices_dict.values()]
    for i, voice in enumerate(voices):
        print(f"Processing voice {i + 1}/{len(voices)}: {voice.key}")
        if voice.name == "tugão":
            print("Skipping voice 'tugão'")
            continue
        voice_to_repo(repo_path, voice)
        print(f"Processed voice {i + 1}/{len(voices)}: {voice.key}")
        sleep(3)  # to avoid rate limiting. Could probably be lowered


if __name__ == "__main__":
    main()
