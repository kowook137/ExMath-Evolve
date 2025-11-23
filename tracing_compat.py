"""
No-op tracing helpers to replace agents.tracing in this minimal LiteLLM-backed path.
"""
from __future__ import annotations

import contextlib
import uuid


def gen_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex}"


@contextlib.contextmanager
def trace(name: str, **kwargs):
    yield


@contextlib.contextmanager
def custom_span(name: str, **kwargs):
    yield
