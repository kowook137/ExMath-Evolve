"""
LangChain-based chat completion wrapper to replace the former Agents SDK / SimpleLLM.

We keep a minimal interface compatible with the codebase:
 - Agent object holding name/instructions/model/output_type
 - Runner.run(agent, input) -> Result with final_output_as(...) and to_input_list()
Environment:
 - OPENAI_API_KEY
 - OPENAI_BASE_URL or OPENAI_API_BASE (for proxies like LiteLLM/OpenRouter)
Optional headers for OpenRouter rankings:
 - HTTP_REFERER
 - X_TITLE
"""

from __future__ import annotations

import json
import os
from typing import Any, List, Type, Union

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel


def _get_client(enforce_json: bool = False) -> ChatOpenAI:
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
    api_key = os.environ.get("OPENAI_API_KEY")
    headers = {}
    if os.environ.get("HTTP_REFERER"):
        headers["HTTP-Referer"] = os.environ["HTTP_REFERER"]
    if os.environ.get("X_TITLE"):
        headers["X-Title"] = os.environ["X_TITLE"]
    if not headers:
        headers = None
    model_kwargs = {}
    if enforce_json:
        model_kwargs["response_format"] = {"type": "json_object"}
    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers=headers,
        streaming=False,
        model_kwargs=model_kwargs,
    )


class LCResult:
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
                try:
                    return typ.model_validate({"markdown_report": self.text})  # type: ignore[arg-type]
                except Exception:
                    return self.text
        return self.text

    def to_input_list(self) -> List[dict[str, Any]]:
        return self._messages


class LCAgent:
    def __init__(self, name: str, instructions: str, model: str, output_type: Any = str):
        self.name = name
        self.instructions = instructions
        self.model = model
        self.output_type = output_type


class LCRunner:
    @classmethod
    async def run(cls, agent: LCAgent, input: Union[str, List[dict[str, str]]]) -> LCResult:
        # Normalize messages
        if isinstance(input, list):
            messages = [m.copy() for m in input]
        else:
            messages = [{"role": "user", "content": str(input)}]

        # Build LangChain messages
        lc_messages = []
        if agent.instructions:
            lc_messages.append(SystemMessage(content=agent.instructions))
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            else:
                lc_messages.append(HumanMessage(content=content))

        enforce_json = isinstance(agent.output_type, type) and issubclass(agent.output_type, BaseModel)
        client = _get_client(enforce_json=enforce_json)
        output = await client.ainvoke(lc_messages, model=agent.model)
        text = output.content if isinstance(output.content, str) else str(output.content)

        full_messages = messages + [{"role": "assistant", "content": text}]
        return LCResult(text=text, messages=full_messages)
