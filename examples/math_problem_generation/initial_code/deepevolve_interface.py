from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple
import os

from generator import MathProblemGenerator
from llm_evaluator import LLMEvaluator
from verification import VerificationRunner
from utils.code import ensure_problem_id, save_problem_pair
from utils.datatypes import (
    EvalRecord,
    FeedbackBundle,
    ProblemMetadata,
    ProblemPair,
    VerificationReport,
)

_env_dir = os.getenv("DEEPEVOLVE_OUTPUT_DIR")
if _env_dir:
    OUTPUT_DIR = Path(_env_dir)
else:
    # Fallback to current working directory to avoid TemporaryDirectory paths
    OUTPUT_DIR = Path.cwd() / "generated_problems"


def _build_metrics(
    verification: VerificationReport,
    evaluation: EvalRecord,
    feedback: FeedbackBundle,
    problem: ProblemPair,
) -> Dict[str, float]:
    valid_score = 1.0 if (verification.substitution_pass and verification.symbolic_pass and not verification.counterexample_found) else 0.0
    # Prefer semantic validity flag if provided in metadata
    if problem.metadata and problem.metadata.semantic_valid is not None:
        valid_score = 1.0 if problem.metadata.semantic_valid else 0.0

    llm_solved = 1.0 if evaluation.llm_solved else 0.0
    llm_score = max(0.0, min(1.0, evaluation.llm_score))
    combined_score = max(0.0, valid_score * (1.0 - llm_score))

    verification_notes = verification.notes
    if problem.metadata and problem.metadata.semantic_notes:
        verification_notes = problem.metadata.semantic_notes

    return {
        "valid": valid_score,
        "llm_solved": llm_solved,
        "llm_score": llm_score,
        "combined_score": combined_score,
        "problem_id": problem.id,
        "llm_model": evaluation.llm_model,
        "llm_attempts": float(evaluation.attempts or 0),
        "llm_tokens": float(evaluation.tokens_used or 0),
        "llm_elapsed_seconds": float(evaluation.elapsed_seconds or 0.0),
        "verification_notes": verification_notes,
        "difficulty_message": feedback.message,
        "difficulty_suggestions": "\n".join(feedback.suggestions),
    }


def deepevolve_interface() -> Tuple[bool, Dict[str, float] | str]:
    """
    DeepEvolve 진입점.

    현재는 기본 파이프라인만 구현되어 있으며, 추후 단계에서 실제 Planner/Writer 결과와
    LLM 평가 로직을 연결한다.
    """
    try:
        generator = MathProblemGenerator()
        problem = generator.generate()
        problem = ensure_problem_id(problem, prefix="math")

        verifier = VerificationRunner()
        if problem.metadata and problem.metadata.semantic_valid is not None:
            # Build a synthetic verification report from semantic check
            verification = VerificationReport(
                substitution_pass=bool(problem.metadata.semantic_valid),
                symbolic_pass=bool(problem.metadata.semantic_valid),
                counterexample_found=not bool(problem.metadata.semantic_valid),
                notes=problem.metadata.semantic_notes or "Semantic check only",
                extra_data=[],
            )
        else:
            verification = verifier.run(problem)
        problem.verification = verification

        evaluator = LLMEvaluator()
        evaluation, feedback = evaluator.evaluate(problem)

        metadata = problem.metadata or ProblemMetadata()
        metadata.verification_notes = verification.notes or ""
        metadata.evaluation_model = evaluation.llm_model
        metadata.evaluation_prompt = evaluation.prompt_style
        metadata.evaluation_feedback = feedback
        metadata.difficulty_message = feedback.message
        metadata.difficulty_suggestions = list(feedback.suggestions)
        metadata.evaluation_elapsed_seconds = evaluation.elapsed_seconds
        metadata.evaluation_attempt_details = evaluation.attempt_details
        problem.metadata = metadata

        output_path = save_problem_pair(problem, str(OUTPUT_DIR))

        metrics = _build_metrics(verification, evaluation, feedback, problem)
        metrics["saved_path"] = output_path

        return True, metrics
    except Exception as exc:  # pragma: no cover - 초기 구조 검증용
        return False, f"math_problem_generation failed: {exc}"
