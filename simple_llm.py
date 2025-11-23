"""
Lightweight chat-completion wrapper to replace the Agents SDK for this repo.

It uses the OpenAI Python client pointed at an OpenAI-compatible endpoint
(`OPENAI_API_BASE`/`OPENAI_BASE_URL`) so it can talk to LiteLLM/OpenRouter proxies
or the OpenAI API directly.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, List, Optional, Type, Union

from openai import AsyncOpenAI
from pydantic import BaseModel


def _get_client() -> OpenAI:
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
    api_key = os.environ.get("OPENAI_API_KEY")
    # Optional OpenRouter ranking headers
    referer = os.environ.get("HTTP_REFERER")
    title = os.environ.get("X_TITLE")
    headers = {}
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    if not headers:
        headers = None
    return AsyncOpenAI(api_key=api_key, base_url=base_url, default_headers=headers)


class SimpleResult:
    """Mimics the minimal interface used from Agents SDK results."""

    def __init__(self, text: str, messages: List[dict[str, Any]]):
        self.text = text
        self._messages = messages

    def final_output_as(self, typ: Union[Type[str], Type[BaseModel], Any]) -> Any:
        if typ is str:
            return self.text
        if isinstance(typ, type) and issubclass(typ, BaseModel):
            try:
                data = json.loads(self.text)
                return typ.model_validate(data)
            except Exception:
                # fallback: wrap raw text
                return typ.model_validate({"markdown_report": self.text})  # type: ignore[arg-type]
        return self.text

    def to_input_list(self) -> List[dict[str, Any]]:
        return self._messages


class SimpleAgent:
    def __init__(self, name: str, instructions: str, model: str, output_type: Any = str):
        self.name = name
        self.instructions = instructions
        self.model = model
        self.output_type = output_type


class SimpleRunner:
    @classmethod
    async def run(cls, agent: SimpleAgent, input: Union[str, List[dict[str, str]]]) -> SimpleResult:
        """
        Execute a chat completion with the given agent and input.
        `input` may be a plain string or an accumulated message list.
        """
        if isinstance(input, list):
            messages = input.copy()
        else:
            messages = [{"role": "user", "content": str(input)}]

        # Prepend system instructions if provided
        if agent.instructions:
            messages = [{"role": "system", "content": agent.instructions}] + messages

        client = _get_client()

        async def _call() -> str:
            resp = await client.chat.completions.create(
                model=agent.model,
                messages=messages,
            )
            return resp.choices[0].message.content or ""

        # Run in asyncio context; OpenAI client is async
        text = await _call()
        return SimpleResult(text=text, messages=messages + [{"role": "assistant", "content": text}])
