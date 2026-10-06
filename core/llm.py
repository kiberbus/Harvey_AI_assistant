"""Запросы к Ollama: один ответ целиком или поток кусочков."""

from __future__ import annotations

import ollama

from config import (
    KEEP_ALIVE,
    MODEL,
    NUM_CTX,
)


def _kwargs(messages: list[dict], tools: list | None, num_predict: int, num_ctx: int | None) -> dict:
    kwargs = dict(
        model=MODEL,
        messages=messages,
        options={"num_ctx": num_ctx or NUM_CTX, "temperature": 0, "num_predict": num_predict},
        keep_alive=KEEP_ALIVE,
    )
    if tools:
        kwargs["tools"] = tools
    return kwargs


def chat(messages: list[dict], tools: list | None = None, num_predict: int = 120, num_ctx: int | None = None):
    kwargs = _kwargs(messages, tools, num_predict, num_ctx)
    try:
        return ollama.chat(think=False, **kwargs)
    except (TypeError, ollama.ResponseError):
        return ollama.chat(**kwargs)


def chat_stream(messages: list[dict], tools: list | None = None, num_predict: int = 120,
                num_ctx: int | None = None):
    """Потоковый ответ. Ошибка про think приходит только на первом чанке, тогда повторяю без think."""
    kwargs = _kwargs(messages, tools, num_predict, num_ctx)
    started = False
    try:
        for chunk in ollama.chat(think=False, stream=True, **kwargs):
            started = True
            yield chunk
    except (TypeError, ollama.ResponseError):
        if started:
            raise
        yield from ollama.chat(stream=True, **kwargs)
