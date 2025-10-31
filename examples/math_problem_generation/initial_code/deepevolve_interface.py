from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

from .generator import MathProblemGenerator
from .llm_evaluator import LLMEvaluator
from .verification import VerificationRunner
from utils.code import ensure_problem_id, save_problem_pair
from utils.datatypes import EvalRecord, FeedbackBundle, ProblemPair, VerificationReport

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "generated_problems"


def _build_metrics(
    verification: VerificationReport,
    evaluation: EvalRecord,
    feedback: FeedbackBundle,
    problem: ProblemPair,
) -> Dict[str, float]:
    valid_score = 1.0 if (verification.substitution_pass and verification.symbolic_pass and not verification.counterexample_found) else 0.0
    llm_solved = 1.0 if evaluation.llm_solved else 0.0
    llm_score = max(0.0, min(1.0, evaluation.llm_score))
    combined_score = max(0.0, valid_score * (1.0 - llm_score))

    return {
        "valid": valid_score,
        "llm_solved": llm_solved,
        "llm_score": llm_score,
        "combined_score": combined_score,
        "problem_id": problem.id,
        "llm_model": evaluation.llm_model,
        "llm_attempts": float(evaluation.attempts or 0),
        "llm_tokens": float(evaluation.tokens_used or 0),
        "verification_notes": verification.notes,
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
        verification = verifier.run(problem)
        problem.verification = verification

        evaluator = LLMEvaluator()
        evaluation, feedback = evaluator.evaluate(problem)

        problem.metadata.update(
            {
                "verification_notes": verification.notes or "",
                "evaluation_model": evaluation.llm_model,
                "evaluation_prompt": evaluation.prompt_style,
                "evaluation_feedback": feedback.model_dump(),
                "difficulty_message": feedback.message,
                "difficulty_suggestions": feedback.suggestions,
            }
        )

        output_path = save_problem_pair(problem, str(OUTPUT_DIR))

        metrics = _build_metrics(verification, evaluation, feedback, problem)
        metrics["saved_path"] = output_path

        return True, metrics
    except Exception as exc:  # pragma: no cover - 초기 구조 검증용
        return False, f"math_problem_generation failed: {exc}"
