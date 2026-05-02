from openai.types.chat import ChatCompletionUserMessageParam

from speaches.realtime.chat_utils import create_completion_params
from speaches.types.realtime import Response


def _response(extra_body: dict | None = None) -> Response:
    return Response(
        conversation="auto",
        input=[],
        instructions="",
        max_response_output_tokens="inf",
        modalities=["text"],
        output_audio_format="pcm16",
        temperature=0.7,
        tool_choice="auto",
        tools=[],
        voice="af_heart",
        extra_body=extra_body,
    )


def _user(content: str) -> ChatCompletionUserMessageParam:
    return ChatCompletionUserMessageParam(role="user", content=content)


def test_extra_body_none_keeps_default() -> None:
    params = create_completion_params("m", [_user("hi")], _response(None))
    assert params["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_extra_body_keys_merged() -> None:
    params = create_completion_params("m", [_user("hi")], _response({"conversation_id": "abc", "device_id": "xyz"}))
    eb = params["extra_body"]
    assert eb["chat_template_kwargs"] == {"enable_thinking": False}
    assert eb["conversation_id"] == "abc"
    assert eb["device_id"] == "xyz"


def test_extra_body_can_override_default() -> None:
    params = create_completion_params(
        "m", [_user("hi")], _response({"chat_template_kwargs": {"enable_thinking": True}})
    )
    assert params["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True}
