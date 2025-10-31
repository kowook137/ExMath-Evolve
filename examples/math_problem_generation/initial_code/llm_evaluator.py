from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from utils.code import compute_text_similarity, normalize_math_text
from utils.datatypes import EvalRecord, FeedbackBundle, ProblemPair

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_STYLE = "cot-zero-shot"


@dataclass
class _LLMResponse:
    text: str
    total_tokens: Optional[int] = None


class LLMEvaluator:
    """
    LLM 난이도 평가 모듈.

    - 설정된 LLM API를 호출하여 문제를 풀도록 시도한다.
    - 정답과의 텍스트 유사도를 기반으로 난이도 점수를 산출한다.
    - API가 구성되지 않은 환경에서는 오프라인 모드로 동작하여 실패로 간주한다.
    """

    def __init__(
        self,
        client_factory: Optional[Callable[[], Any]] = None,
        model: str = "gpt-4o-mini",
        prompt_style: str = DEFAULT_PROMPT_STYLE,
        temperature: float = 0.2,
        max_output_tokens: int = 512,
        solve_threshold: float = 0.8,
        max_attempts: int = 2,
    ):
        self.model = model
        self.prompt_style = prompt_style
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.solve_threshold = solve_threshold
        self.max_attempts = max(1, max_attempts)

        if client_factory is not None:
            self._client = self._safe_create_client(client_factory)
        else:
            self._client = self._default_client()

        if self._client is None:
            logger.warning(
                "LLMEvaluator is running in offline mode (no client configured). "
                "All evaluations will result in `llm_solved = False`."
            )

    # ------------------------------------------------------------------
    def evaluate(self, problem: ProblemPair) -> Tuple[EvalRecord, FeedbackBundle]:
        reference_solution = problem.solution_text or ""
        reference_norm = normalize_math_text(reference_solution)

        attempts = 0
        best_score = 0.0
        best_response = ""
        tokens_used_total: Optional[int] = 0

        if self._client is None:
            attempts = 0
            tokens_used_total = None
            feedback = self._offline_feedback()
            record = EvalRecord(
                llm_model="offline",
                prompt_style=self.prompt_style,
                temperature=self.temperature,
                llm_solved=False,
                llm_score=0.0,
                rationale_quality=None,
                tokens_used=None,
                attempts=0,
                raw_response="Evaluation skipped: no LLM client configured.",
            )
            return record, feedback

        for attempt in range(1, self.max_attempts + 1):
            attempts = attempt
            response = self._invoke_model(problem.problem_text)

            if response is None:
                logger.warning("LLM call failed on attempt %d.", attempt)
                continue

            best_response = response.text
            tokens_used_total = (tokens_used_total or 0) + (response.total_tokens or 0)
            score = self._score_response(response.text, reference_norm)
            best_score = max(best_score, score)

            if score >= self.solve_threshold:
                break

        llm_solved = best_score >= self.solve_threshold
        feedback = self._build_feedback(problem, best_score, llm_solved, best_response)

        record = EvalRecord(
            llm_model=self.model if self._client else "offline",
            prompt_style=self.prompt_style,
            temperature=self.temperature,
            llm_solved=llm_solved,
            llm_score=float(best_score),
            rationale_quality=None,
            tokens_used=tokens_used_total,
            attempts=attempts,
            raw_response=best_response,
        )
        return record, feedback

    # ------------------------------------------------------------------
    def _default_client(self) -> Optional[Any]:
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return None
        try:
            from openai import OpenAI

            return OpenAI()
        except Exception as exc:  # pragma: no cover - 환경 의존
            logger.warning("Failed to create default OpenAI client: %s", exc)
            return None

    def _safe_create_client(self, factory: Callable[[], Any]) -> Optional[Any]:
        try:
            return factory()
        except Exception as exc:
            logger.warning("Custom client factory failed: %s", exc)
            return None

    def _invoke_model(self, prompt: str) -> Optional[_LLMResponse]:
        if self._client is None:  # pragma: no cover - guard
            return None

        try:
            if hasattr(self._client, "responses"):
                response = self._client.responses.create(
                    model=self.model,
                    input=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                    max_output_tokens=self.max_output_tokens,
                )
                text = getattr(response, "output_text", None)
                if text is None:
                    # fall back to concatenating content
                    try:
                        text = "".join(
                            block.text for block in response.output if hasattr(block, "text")
                        )
                    except Exception:
                        text = json.dumps(response.model_dump())
                usage = getattr(response, "usage", None)
                total_tokens = getattr(usage, "total_tokens", None) if usage else None
                return _LLMResponse(text=text, total_tokens=total_tokens)

            # ChatCompletion fallback
            if hasattr(self._client, "chat") and hasattr(self._client.chat, "completions"):
                completion = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an expert mathematician. Solve the user's problem step by step.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_output_tokens,
                )
                message = completion.choices[0].message
                text = message.get("content") if isinstance(message, dict) else message.content
                total_tokens = getattr(completion, "usage", {}).get("total_tokens")
                return _LLMResponse(text=text, total_tokens=total_tokens)

        except Exception as exc:  # pragma: no cover - 외부 API 오류
            logger.warning("LLM invocation failed: %s", exc)
            return None

        logger.warning("Unsupported LLM client interface: %s", type(self._client))
        return None

    def _score_response(self, response_text: str, reference_norm: str) -> float:
        if not response_text.strip():
            return 0.0

        response_norm = normalize_math_text(response_text)

        if not reference_norm:
            # Reference solution이 없으면 낮은 점수만 부여
            return 0.1

        return compute_text_similarity(reference_norm, response_norm)

    def _build_feedback(
        self,
        problem: ProblemPair,
        score: float,
        llm_solved: bool,
        raw_response: str,
    ) -> FeedbackBundle:
        message_lines: list[str] = []
        suggestions: list[str] = []

        if llm_solved:
            message_lines.append(
                "LLM solved the problem with high confidence. Difficulty may be insufficient."
            )
            suggestions.extend(
                [
                    "Introduce additional constraints or sub-questions that require deeper reasoning.",
                    "Modify numeric parameters to avoid direct recall of standard examples.",
                    "Require a proof of optimality or consider a more challenging variant.",
                ]
            )
        else:
            if score > 0.4:
                message_lines.append(
                    "LLM produced a partially relevant answer but failed to reach the exact solution."
                )
                suggestions.append(
                    "Clarify intermediate steps in the solution to help validator scripts catch subtle errors."
                )
            else:
                message_lines.append(
                    "LLM failed to produce a meaningful solution. Problem appears difficult."
                )
                suggestions.append(
                    "Consider adding optional hints or intermediate checks so we can assess reasoning quality."
                )

        if not raw_response.strip():
            suggestions.append("Ensure the evaluator model is reachable; configure OPENAI_API_KEY.")

        message = "\n".join(message_lines)
        return FeedbackBundle(
            message=message,
            suggestions=suggestions,
            evaluator=self.model if self._client else "offline",
        )

    def _offline_feedback(self) -> FeedbackBundle:
        return FeedbackBundle(
            message="LLM evaluator is not configured; problem difficulty could not be assessed.",
            suggestions=[
                "Set OPENAI_API_KEY or provide a custom client factory for LLMEvaluator.",
                "Until evaluation is enabled, treat generated problems as unverified for hardness.",
            ],
            evaluator="offline",
        )
