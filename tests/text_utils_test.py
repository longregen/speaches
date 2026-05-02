import asyncio

import pytest

from speaches.text_utils import (
    EOFTextChunker,
    srt_format_timestamp,
    strip_markdown_emphasis,
    vtt_format_timestamp,
)


def test_srt_format_timestamp() -> None:
    assert srt_format_timestamp(0.0) == "00:00:00,000"
    assert srt_format_timestamp(1.0) == "00:00:01,000"
    assert srt_format_timestamp(1.234) == "00:00:01,234"
    assert srt_format_timestamp(60.0) == "00:01:00,000"
    assert srt_format_timestamp(61.0) == "00:01:01,000"
    assert srt_format_timestamp(61.234) == "00:01:01,234"
    assert srt_format_timestamp(3600.0) == "01:00:00,000"
    assert srt_format_timestamp(3601.0) == "01:00:01,000"
    assert srt_format_timestamp(3601.234) == "01:00:01,234"
    assert srt_format_timestamp(23423.4234) == "06:30:23,423"


def test_vtt_format_timestamp() -> None:
    assert vtt_format_timestamp(0.0) == "00:00:00.000"
    assert vtt_format_timestamp(1.0) == "00:00:01.000"
    assert vtt_format_timestamp(1.234) == "00:00:01.234"
    assert vtt_format_timestamp(60.0) == "00:01:00.000"
    assert vtt_format_timestamp(61.0) == "00:01:01.000"
    assert vtt_format_timestamp(61.234) == "00:01:01.234"
    assert vtt_format_timestamp(3600.0) == "01:00:00.000"
    assert vtt_format_timestamp(3601.0) == "01:00:01.000"
    assert vtt_format_timestamp(3601.234) == "01:00:01.234"
    assert vtt_format_timestamp(23423.4234) == "06:30:23.423"


def test_strip_markdown_emphasis() -> None:
    assert strip_markdown_emphasis("Hello my name is **Jon**") == "Hello my name is Jon"
    assert strip_markdown_emphasis("I *really* like this") == "I really like this"
    assert strip_markdown_emphasis("This is __underlined__") == "This is underlined"
    assert strip_markdown_emphasis("This is _italic_") == "This is italic"
    assert strip_markdown_emphasis("Mixed **bold** and *italic* text") == "Mixed bold and italic text"
    assert strip_markdown_emphasis("No markdown here") == "No markdown here"
    assert (
        strip_markdown_emphasis("**Bold** at the *beginning* and _end_ of **text**")
        == "Bold at the beginning and end of text"
    )
    assert strip_markdown_emphasis("Nested **bold *with italic* inside**") == "Nested bold with italic inside"


@pytest.mark.asyncio
async def test_eof_text_chunker() -> None:
    chunker = EOFTextChunker()

    chunker.add_token("Hello ")
    chunker.add_token("world!")

    results = []

    async def collect_chunks() -> None:
        async for chunk in chunker:
            results.append(chunk)  # noqa: PERF401

    task = asyncio.create_task(collect_chunks())

    await asyncio.sleep(0.1)

    assert len(results) == 0

    chunker.close()

    await asyncio.sleep(0.1)
    await task

    assert len(results) == 1
    assert results[0] == "Hello world!"


@pytest.mark.asyncio
async def test_eof_text_chunker_empty() -> None:
    chunker = EOFTextChunker()

    results = []

    async def collect_chunks() -> None:
        async for chunk in chunker:
            results.append(chunk)  # noqa: PERF401

    task = asyncio.create_task(collect_chunks())

    await asyncio.sleep(0.1)

    chunker.close()

    await asyncio.sleep(0.1)
    await task

    assert len(results) == 0


@pytest.mark.asyncio
async def test_eof_text_chunker_closed_error() -> None:
    chunker = EOFTextChunker()
    chunker.close()

    with pytest.raises(RuntimeError):
        chunker.add_token("This should fail")
