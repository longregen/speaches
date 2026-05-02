from unittest.mock import MagicMock

from pydantic import SecretStr
import pytest
from pytest_mock import MockerFixture

from speaches.config import Config, WhisperConfig
from speaches.main import create_app
from speaches.realtime.session import create_session_object_configuration
from speaches.realtime.utils import verify_websocket_api_key


class TestRealtimeWebSocketAuthentication:
    @pytest.mark.asyncio
    async def test_websocket_auth_with_bearer_token(self) -> None:
        mock_ws = MagicMock()
        mock_ws.headers = {"authorization": "Bearer test-api-key"}
        mock_ws.query_params = {}

        config = Config(
            api_key=SecretStr("test-api-key"),
            whisper=WhisperConfig(),
            enable_ui=False,
            chat_completion_base_url="https://api.openai.com/v1",
            chat_completion_api_key=SecretStr("test-key"),
        )

        await verify_websocket_api_key(mock_ws, config)

    @pytest.mark.asyncio
    async def test_websocket_auth_with_x_api_key(self) -> None:
        mock_ws = MagicMock()
        mock_ws.headers = {"x-api-key": "test-api-key"}
        mock_ws.query_params = {}

        config = Config(
            api_key=SecretStr("test-api-key"),
            whisper=WhisperConfig(),
            enable_ui=False,
            chat_completion_base_url="https://api.openai.com/v1",
            chat_completion_api_key=SecretStr("test-key"),
        )

        await verify_websocket_api_key(mock_ws, config)

    @pytest.mark.asyncio
    async def test_websocket_auth_with_query_param(self) -> None:
        mock_ws = MagicMock()
        mock_ws.headers = {}
        mock_ws.query_params = {"api_key": "test-api-key"}

        config = Config(
            api_key=SecretStr("test-api-key"),
            whisper=WhisperConfig(),
            enable_ui=False,
            chat_completion_base_url="https://api.openai.com/v1",
            chat_completion_api_key=SecretStr("test-key"),
        )

        await verify_websocket_api_key(mock_ws, config)

    @pytest.mark.asyncio
    async def test_websocket_auth_no_api_key_configured(self) -> None:
        mock_ws = MagicMock()
        mock_ws.headers = {}
        mock_ws.query_params = {}

        config = Config(
            api_key=None,
            whisper=WhisperConfig(),
            enable_ui=False,
            chat_completion_base_url="https://api.openai.com/v1",
            chat_completion_api_key=SecretStr("test-key"),
        )

        await verify_websocket_api_key(mock_ws, config)

    @pytest.mark.asyncio
    async def test_websocket_auth_invalid_key(self) -> None:
        from fastapi import WebSocketException

        mock_ws = MagicMock()
        mock_ws.headers = {"authorization": "Bearer wrong-key"}
        mock_ws.query_params = {}

        config = Config(
            api_key=SecretStr("correct-key"),
            whisper=WhisperConfig(),
            enable_ui=False,
            chat_completion_base_url="https://api.openai.com/v1",
            chat_completion_api_key=SecretStr("test-key"),
        )

        with pytest.raises(WebSocketException):
            await verify_websocket_api_key(mock_ws, config)

    @pytest.mark.asyncio
    async def test_websocket_auth_missing_key(self) -> None:
        from fastapi import WebSocketException

        mock_ws = MagicMock()
        mock_ws.headers = {}
        mock_ws.query_params = {}

        config = Config(
            api_key=SecretStr("required-key"),
            whisper=WhisperConfig(),
            enable_ui=False,
            chat_completion_base_url="https://api.openai.com/v1",
            chat_completion_api_key=SecretStr("test-key"),
        )

        with pytest.raises(WebSocketException):
            await verify_websocket_api_key(mock_ws, config)


class TestRealtimeSessionConfiguration:
    def test_conversation_mode_default(self) -> None:
        session = create_session_object_configuration("gpt-4o-realtime-preview")

        assert session.model == "gpt-4o-realtime-preview"
        assert session.input_audio_transcription.model == "Systran/faster-distil-whisper-small.en"
        assert session.turn_detection is not None and session.turn_detection.create_response is True
        assert session.input_audio_transcription.language is None

    def test_conversation_mode_with_custom_transcription(self) -> None:
        session = create_session_object_configuration(
            model="gpt-4o-realtime-preview", intent="conversation", transcription_model="whisper-1"
        )

        assert session.model == "gpt-4o-realtime-preview"
        assert session.input_audio_transcription.model == "whisper-1"
        assert session.turn_detection is not None and session.turn_detection.create_response is True

    def test_transcription_only_mode(self) -> None:
        session = create_session_object_configuration(
            model="deepdml/faster-whisper-large-v3-turbo-ct2", intent="transcription"
        )

        assert session.model == "gpt-4o-realtime-preview"
        assert session.input_audio_transcription.model == "deepdml/faster-whisper-large-v3-turbo-ct2"
        assert session.turn_detection is not None and session.turn_detection.create_response is False

    def test_transcription_mode_with_language(self) -> None:
        session = create_session_object_configuration(model="whisper-1", intent="transcription", language="ru")

        assert session.input_audio_transcription.language == "ru"
        assert session.turn_detection is not None and session.turn_detection.create_response is False

    def test_transcription_mode_with_explicit_models(self) -> None:
        session = create_session_object_configuration(
            model="gpt-4o-realtime-preview", intent="transcription", transcription_model="custom-whisper-model"
        )

        assert session.model == "gpt-4o-realtime-preview"
        assert session.input_audio_transcription.model == "custom-whisper-model"
        assert session.turn_detection is not None and session.turn_detection.create_response is False

    def test_session_configuration_logging(self, caplog) -> None:  # noqa: ANN001
        with caplog.at_level("INFO"):
            create_session_object_configuration(model="test-model", intent="transcription")

        assert "Transcription-only mode" in caplog.text
        assert "test-model" in caplog.text

    def test_conversation_mode_logging(self, caplog) -> None:  # noqa: ANN001
        with caplog.at_level("INFO"):
            create_session_object_configuration(model="gpt-4o-realtime-preview", intent="conversation")

        assert "Conversation mode (OpenAI standard)" in caplog.text


class TestRealtimeWebSocketEndpoint:
    def test_websocket_endpoint_exists(self, mocker: MockerFixture) -> None:
        from pydantic import SecretStr

        from speaches.config import Config, WhisperConfig

        config = Config(
            api_key=None,
            whisper=WhisperConfig(),
            enable_ui=False,
            chat_completion_base_url="https://api.openai.com/v1",
            chat_completion_api_key=SecretStr("test-key"),
        )

        mocker.patch("speaches.main.get_config", return_value=config)
        mocker.patch("speaches.dependencies.get_config", return_value=config)

        app = create_app()

        ws_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/v1/realtime" in ws_paths

    @pytest.mark.asyncio
    async def test_websocket_parameter_parsing(self) -> None:
        session = create_session_object_configuration("test-model")
        assert session.model == "test-model"

        session = create_session_object_configuration("test-model", intent="transcription")
        assert session.turn_detection is not None and session.turn_detection.create_response is False

        session = create_session_object_configuration(
            model="test-model", intent="transcription", language="en", transcription_model="custom-model"
        )
        assert session.input_audio_transcription.model == "custom-model"
        assert session.input_audio_transcription.language == "en"


class TestRealtimeAPICompatibility:
    def test_default_models_configuration(self) -> None:
        session = create_session_object_configuration("gpt-4o-realtime-preview")

        assert session.input_audio_transcription.model == "Systran/faster-distil-whisper-small.en"
        assert session.speech_model == "speaches-ai/Kokoro-82M-v1.0-ONNX"
        assert session.voice == "af_heart"

    def test_openai_standard_behavior(self) -> None:
        session = create_session_object_configuration(model="gpt-4o-realtime-preview", intent="conversation")

        assert session.model == "gpt-4o-realtime-preview"
        assert session.input_audio_transcription.model == "Systran/faster-distil-whisper-small.en"
        assert session.turn_detection is not None and session.turn_detection.create_response is True

    def test_speaches_extension_behavior(self) -> None:
        session = create_session_object_configuration(model="custom-whisper-model", intent="transcription")

        assert session.input_audio_transcription.model == "custom-whisper-model"
        assert session.model == "gpt-4o-realtime-preview"
        assert session.turn_detection is not None and session.turn_detection.create_response is False

    def test_session_structure_compatibility(self) -> None:
        session = create_session_object_configuration("test-model")

        assert hasattr(session, "id")
        assert hasattr(session, "model")
        assert hasattr(session, "modalities")
        assert hasattr(session, "input_audio_transcription")
        assert hasattr(session, "turn_detection")
        assert hasattr(session, "speech_model")
        assert hasattr(session, "voice")

        assert isinstance(session.modalities, list)
        assert "audio" in session.modalities
        assert "text" in session.modalities
