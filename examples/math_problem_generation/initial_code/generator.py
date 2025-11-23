from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable
import logging

from utils.datatypes import ProblemPair, ProblemMetadata
from utils.code import ensure_problem_id
from utils.ir import IRProblem, render_ir_to_text, load_ir, validate_ir_semantics


logger = logging.getLogger(__name__)


class MathProblemGenerator:
    """
    Seed-driven problem generator.

    - 모든 문제는 `seed.json`에 정의된 시드 문제를 기반으로 한다.
    - 연구 산출물이 있더라도 시드를 대체하지 않고, 이후 단계(코더/리서처)에서
      시드 기반 crossover/변이를 적용하도록 설계한다.
    - 시드를 순환하며 사용하되, 필요시 `seed_pointer.json`으로 진행 위치를 유지한다.
    """

    def __init__(self, seed_file: str | Path | None = None) -> None:
        self._seed_file = Path(seed_file) if seed_file else Path(__file__).resolve().parent / "seed.json"
        self._research_dir = Path(__file__).resolve().parent.parent / "research"
        self._seed_pointer_path = self._research_dir / "seed_pointer.json"
        self._seeds = self._load_seeds(self._seed_file)
        if not self._seeds:
            raise RuntimeError(f"No seeds found in {self._seed_file}; seed-based evolution cannot proceed.")

    def generate(self) -> ProblemPair:
        # Prefer latest research artefact if available
        research_problem = self._from_research()
        if research_problem is not None:
            return research_problem

        entry = self._next_seed_entry()
        if entry is None:
            raise RuntimeError("Failed to select a seed entry.")
        problem = self._build_problem(entry)
        return ensure_problem_id(problem, prefix=entry.get("prefix", "seed"))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _load_seeds(self, seed_file: Path) -> list[dict]:
        try:
            with open(seed_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            seeds = data.get("seeds", []) if isinstance(data, dict) else []
            if not isinstance(seeds, list):
                logger.warning("seed.json format unexpected; expected {'seeds': [...]} list")
                return []
            return seeds
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"Failed to load seeds from {seed_file}: {exc}")
            return []

    def _build_problem(self, entry: dict) -> ProblemPair:
        metadata = ProblemMetadata(
            status="seed",
            difficulty_message=entry.get("difficulty_message"),
            difficulty_suggestions=entry.get("difficulty_suggestions", []),
            verification_tasks=entry.get("verification_tasks"),
        )
        theorem_refs = self._normalize_theorem_refs(entry.get("theorem_refs", []))

        # IR-based seeds: if entry has an "ir" block or "ir_file", render it to natural language and run semantic check
        problem_text = entry.get("problem_text", "")
        if not problem_text:
            ir_obj = None
            if entry.get("ir"):
                try:
                    ir_obj = IRProblem(**entry["ir"])
                except Exception:
                    ir_obj = None
            if ir_obj is None and entry.get("ir_file"):
                try:
                    ir_obj = load_ir(entry["ir_file"])
                except Exception:
                    ir_obj = None

            if ir_obj is not None:
                problem_text = render_ir_to_text(ir_obj)
                try:
                    sem_ok, sem_notes = validate_ir_semantics(ir_obj)
                    metadata.semantic_valid = sem_ok
                    metadata.semantic_notes = sem_notes
                    # surface semantic notes in verification_notes for visibility
                    if sem_notes:
                        metadata.verification_notes = sem_notes
                except Exception:
                    pass

        return ProblemPair(
            id="",
            problem_text=problem_text,
            solution_text=entry.get("solution_text", ""),
            tags=entry.get("tags", []),
            prerequisites=entry.get("prerequisites", []),
            theorem_refs=theorem_refs,
            metadata=metadata,
        )

    def _normalize_theorem_refs(self, refs: Iterable) -> list[dict]:
        normalized: list[dict] = []
        if not refs:
            return normalized
        for item in refs:
            if isinstance(item, dict):
                normalized_item = {
                    "name": str(item.get("name", "")).strip(),
                    "statement": str(item.get("statement", "") or "").strip(),
                    "source": str(item.get("source", "") or "").strip(),
                }
                if "notes" in item and item["notes"]:
                    normalized_item["notes"] = str(item["notes"]).strip()
                normalized.append(normalized_item)
            elif isinstance(item, str):
                normalized.append({"name": item.strip(), "statement": "", "source": ""})
        return normalized

    def _next_seed_entry(self) -> dict | None:
        seeds = self._seeds
        if not seeds:
            return None

        self._research_dir.mkdir(parents=True, exist_ok=True)

        idx = 0
        if self._seed_pointer_path.exists():
            try:
                with open(self._seed_pointer_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    idx = int(data.get("index", 0))
            except Exception:
                idx = 0

        entry = seeds[idx % len(seeds)]
        next_idx = (idx + 1) % len(seeds)
        try:
            with open(self._seed_pointer_path, "w", encoding="utf-8") as f:
                json.dump({"index": next_idx}, f)
        except Exception:
            pass
        return entry

    def _from_research(self) -> ProblemPair | None:
        """Load the latest research-synthesized problem if present, else None."""
        env_dir = os.getenv("DEEPEVOLVE_RESEARCH_DIR")
        if env_dir:
            research_path = Path(env_dir) / "latest_problem.json"
        else:
            research_path = (
                Path(__file__).resolve().parent.parent / "research" / "latest_problem.json"
            )
        try:
            if research_path.exists():
                with open(research_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "theorem_refs" in data:
                    data["theorem_refs"] = self._normalize_theorem_refs(data["theorem_refs"])
                prob = ProblemPair(**data)
                return ensure_problem_id(prob, prefix="research")
        except Exception:
            return None
        return None
