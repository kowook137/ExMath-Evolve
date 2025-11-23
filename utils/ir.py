"""Lightweight IR schema and helpers for structured math problems.

This is intentionally minimal so that existing natural-language seeds keep working
while allowing IR-based seeds (JSON) to be loaded, validated, and rendered into
natural language for downstream evaluation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationError


class IRObject(BaseModel):
    id: str
    type: str
    params: Dict[str, Any] = Field(default_factory=dict)


class IRAssumption(BaseModel):
    kind: Optional[str] = None
    expr: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None


class IRQuestion(BaseModel):
    type: Optional[str] = None
    targets: List[Dict[str, Any]] = Field(default_factory=list)
    comparisons: List[Dict[str, Any]] = Field(default_factory=list)
    prompt: Optional[str] = None  # optional free-form prompt


class IRProblem(BaseModel):
    objects: List[IRObject] = Field(default_factory=list)
    assumptions: List[IRAssumption] = Field(default_factory=list)
    question: IRQuestion
    metadata: Dict[str, Any] = Field(default_factory=dict)


def load_ir(path: str) -> IRProblem:
    import json

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    try:
        return IRProblem(**data)
    except ValidationError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid IR schema in {path}: {exc}")


def render_ir_to_text(ir: IRProblem) -> str:
    """Render a human-readable summary from IR.

    This is a simple deterministic renderer to keep generator/evaluator flows working
    without extra LLM calls. It lists objects, assumptions, and question prompts.
    """

    lines: List[str] = []
    if ir.objects:
        lines.append("Objects:")
        for obj in ir.objects:
            params_str = ", ".join(f"{k}={v}" for k, v in obj.params.items())
            lines.append(f"- {obj.id}: {obj.type} ({params_str})")

    if ir.assumptions:
        lines.append("Assumptions:")
        for asm in ir.assumptions:
            desc = asm.description or asm.expr or asm.kind or "assumption"
            lines.append(f"- {desc}")

    if ir.question:
        q = ir.question
        if q.prompt:
            lines.append(f"Question: {q.prompt}")
        else:
            lines.append("Question:")
        if q.targets:
            lines.append("  Targets:")
            for tgt in q.targets:
                name = tgt.get("name") or tgt.get("expr")
                expr = tgt.get("expr", "?")
                lines.append(f"  - {name}: compute {expr}")
        if q.comparisons:
            lines.append("  Comparisons:")
            for cmp in q.comparisons:
                lhs = cmp.get("lhs")
                rhs = cmp.get("rhs")
                rel = cmp.get("relation", "?")
                lines.append(f"  - check {lhs} {rel} {rhs}")

    return "\n".join(lines).strip()


def ir_to_problem_text(ir: IRProblem) -> str:
    """Alias for render_ir_to_text for clarity."""

    return render_ir_to_text(ir)


def validate_ir_semantics(ir: IRProblem) -> tuple[bool, str]:
    """Lightweight, domain-agnostic plausibility checks for IR.

    Checks uniqueness of object ids, presence of question/targets, and that
    question expressions reference defined object ids (best-effort substring match).
    """

    messages: List[str] = []

    # object ids
    ids = [obj.id for obj in ir.objects]
    if len(ids) != len(set(ids)):
        messages.append("Duplicate object ids detected.")
    if not ids:
        messages.append("No objects defined.")

    # question existence
    if ir.question is None:
        messages.append("Question is missing.")
        return False, "\n".join(messages)

    # targets/comparisons reference check (best-effort)
    defined_ids = set(ids)
    def refs_defined(expr: Optional[str]) -> bool:
        if not expr:
            return False
        return any(token in expr for token in defined_ids)

    # check targets
    for tgt in ir.question.targets:
        expr = tgt.get("expr")
        if not refs_defined(expr):
            messages.append(f"Target '{tgt.get('name') or expr}' does not reference any defined object id.")

    # check comparisons
    for cmp in ir.question.comparisons:
        lhs = cmp.get("lhs")
        rhs = cmp.get("rhs")
        if not refs_defined(lhs):
            messages.append(f"Comparison lhs '{lhs}' does not reference any defined object id.")
        if rhs and isinstance(rhs, str) and not refs_defined(rhs):
            # rhs could be a bound name or numeric; only warn if string and not referencing ids
            messages.append(f"Comparison rhs '{rhs}' does not reference any defined object id (if intended).")

    ok = len(messages) == 0
    notes = "\n".join(messages) if messages else "semantic IR check passed"
    return ok, notes
