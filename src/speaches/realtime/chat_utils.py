import logging

from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionStreamOptionsParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_message_tool_call_param import Function
from openai.types.chat.completion_create_params import (
    CompletionCreateParamsStreaming,
)
from openai.types.shared_params.function_definition import FunctionDefinition

from speaches.types.realtime import ConversationItem, Response

logger = logging.getLogger(__name__)


def create_completion_params(
    model_id: str, messages: list[ChatCompletionMessageParam], response: Response
) -> CompletionCreateParamsStreaming:
    max_tokens = None if response.max_response_output_tokens == "inf" else response.max_response_output_tokens
    kwargs = {}
    if len(response.tools) > 0:
        # TODO: check if the tool conversion is necessary or if raw tool dicts work
        kwargs["tools"] = [
            ChatCompletionToolParam(
                type=tool.type,
                # HACK: figure out why `tool.description` is nullable
                function=FunctionDefinition(
                    name=tool.name, description=tool.description or "", parameters=tool.parameters
                ),
            )
            for tool in response.tools
        ]
        kwargs["tool_choice"] = response.tool_choice

    system_messages: list[ChatCompletionMessageParam] = (
        [ChatCompletionSystemMessageParam(role="system", content=response.instructions)]
        if response.instructions
        else []
    )
    params = CompletionCreateParamsStreaming(
        model=model_id,
        messages=[*system_messages, *messages],
        stream=True,
        temperature=response.temperature,
        max_tokens=max_tokens,
        stream_options=ChatCompletionStreamOptionsParam(include_usage=True),
        **kwargs,
    )
    extra_body: dict = {"chat_template_kwargs": {"enable_thinking": False}}
    if response.extra_body:
        extra_body.update(response.extra_body)
    params["extra_body"] = extra_body  # ty: ignore[invalid-key]  # pyright: ignore[reportTypedDictUnknownKey]
    return params


def conversation_item_to_chat_message(
    item: ConversationItem,
    audio_direct_prompt: str | None = None,
) -> ChatCompletionMessageParam | None:
    match item.type:
        case "message":
            content_list = item.content
            assert content_list is not None and len(content_list) == 1, item
            content = content_list[0]
            if item.status != "completed":
                logger.warning(f"Item {item} is not completed. Skipping.")
                return None
            match content.type:  # pyrefly: ignore[non-exhaustive-match]
                case "text":
                    assert content.text, content
                    return ChatCompletionAssistantMessageParam(role="assistant", content=content.text)
                case "output_audio":
                    assert content.transcript, content
                    return ChatCompletionAssistantMessageParam(role="assistant", content=content.transcript)
                case "input_text":
                    assert content.text, content
                    return ChatCompletionUserMessageParam(role="user", content=content.text)
                case "input_audio":
                    if content.audio and audio_direct_prompt is not None:
                        parts: list = [
                            {"type": "input_audio", "input_audio": {"data": content.audio, "format": "wav"}},
                        ]
                        if audio_direct_prompt:
                            parts.append({"type": "text", "text": audio_direct_prompt})
                        return ChatCompletionUserMessageParam(role="user", content=parts)
                    if content.audio:
                        return ChatCompletionUserMessageParam(role="user", content="[user spoke via audio]")
                    if not content.transcript:
                        logger.error(f"Conversation item doesn't have a non-empty transcript: {item}")
                        return None
                    return ChatCompletionUserMessageParam(role="user", content=content.transcript)
        case "function_call":
            assert item.call_id and item.name and item.arguments and item.status == "completed", item
            return ChatCompletionAssistantMessageParam(
                role="assistant",
                tool_calls=[
                    ChatCompletionMessageToolCallParam(
                        id=item.call_id,
                        type="function",
                        function=Function(
                            name=item.name,
                            arguments=item.arguments,
                        ),
                    )
                ],
            )
        case "function_call_output":
            assert item.call_id and item.output, item
            return ChatCompletionToolMessageParam(
                role="tool",
                tool_call_id=item.call_id,
                content=item.output,
            )


def items_to_chat_messages(
    items: list[ConversationItem], audio_direct_prompt: str | None = None
) -> list[ChatCompletionMessageParam]:
    if audio_direct_prompt is None:
        return [m for m in (conversation_item_to_chat_message(item) for item in items) if m is not None]
    # Only attach audio on the last input_audio item; earlier ones become text placeholders
    last_audio_idx = -1
    for i, item in enumerate(items):
        if (
            item.type == "message"
            and item.content
            and len(item.content) == 1
            and item.content[0].type == "input_audio"
            and getattr(item.content[0], "audio", None)
        ):
            last_audio_idx = i
    result: list[ChatCompletionMessageParam] = []
    for i, item in enumerate(items):
        prompt = audio_direct_prompt if i == last_audio_idx else None
        m = conversation_item_to_chat_message(item, prompt)
        if m is not None:
            result.append(m)
    return result
