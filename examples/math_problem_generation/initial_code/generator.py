from __future__ import annotations

from utils.datatypes import ProblemPair, TheoremRef
from utils.code import ensure_problem_id


class MathProblemGenerator:
    """
    Placeholder 수학 문제 생성기.

    추후 Planner/Searcher/Writer의 산출물을 받아 구체적인 문제를 합성하도록 확장한다.
    현재는 하드코딩된 예제 문제들을 번갈아 반환하여 파이프라인을 검증한다.
    """

    def _parity_problem(self) -> ProblemPair:
        return ProblemPair(
            id="",
            problem_text=(
                "자연수 n에 대해 n(n+1)이 항상 짝수임을 증명하시오. "
                "추가로, 해당 성질을 이용해 연속된 두 정수의 곱이 4로 나누어 떨어지지 않는 경우를 찾아보시오."
            ),
            solution_text=(
                "연속된 두 정수 중 하나는 짝수이므로 n(n+1)은 항상 짝수이다. "
                "또한 n ≡ 0 (mod 2)인 경우 n(n+1) ≡ 0 (mod 4)이지만, n ≡ 1 (mod 2)인 경우 "
                "n(n+1) ≡ 2 (mod 4)이므로 4로 나누어 떨어지지 않는다."
            ),
            tags=["number_theory", "parity"],
            prerequisites=["기초 정수론", "모듈러 산술"],
            theorem_refs=[
                TheoremRef(
                    name="Parity Principle",
                    statement="어떤 두 연속된 정수 중 하나는 반드시 짝수이다.",
                    source="기초 정수론 교재",
                )
            ],
            metadata={
                "status": "placeholder",
                "verification_tasks": {
                    "substitution": [
                        {
                            "expression": "n*(n+1)",
                            "mod": 2,
                            "expected": 0,
                            "variables": {
                                "n": {"type": "integer", "min": 1, "max": 200}
                            },
                            "num_samples": 20,
                            "description": "연속된 두 정수의 곱은 항상 짝수인지 확인",
                        },
                        {
                            "expression": "n*(n+1) % 4",
                            "target_values": [0, 2],
                            "variables": {
                                "n": {"type": "integer", "min": 1, "max": 200}
                            },
                            "num_samples": 20,
                            "description": "짝수일 때는 0, 홀수일 때는 2가 되는지 확인",
                        },
                    ],
                    "symbolic_equalities": [
                        {
                            "lhs": "n*(n+1)",
                            "rhs": "2*k",
                            "variables": {
                                "n": {"type": "integer"},
                                "k": {"type": "integer"},
                            },
                            "description": "연속된 두 정수의 곱이 2의 배수임을 나타내는 표현",
                        }
                    ],
                },
            },
        )

    def _consecutive_product_problem(self) -> ProblemPair:
        return ProblemPair(
            id="",
            problem_text=(
                "2부터 시작하는 연속된 10개의 자연수를 모두 곱한 값을 구하시오. "
                "또한, 해당 곱을 소인수분해하고 소인수의 지수 합을 계산하시오."
            ),
            solution_text=(
                "연속된 10개의 자연수는 2, 3, ..., 11이다. 따라서 곱은 11! = 39916800이다. "
                "11!의 소인수분해는 2^8 · 3^4 · 5^2 · 7^1 · 11^1이므로 지수의 합은 16이다."
            ),
            tags=["number_theory", "factorial"],
            prerequisites=["기초 정수론", "소인수분해", "팩토리얼"],
            theorem_refs=[
                TheoremRef(
                    name="Factorial Definition",
                    statement="n!는 1부터 n까지의 자연수의 곱이다.",
                    source="기초 정수론 교재",
                )
            ],
            metadata={
                "status": "placeholder",
                "verification_tasks": {
                    "substitution": [
                        {
                            "expression": "factorial(11)",
                            "expected": 39916800,
                            "variables": {},
                            "num_samples": 1,
                            "description": "11! 값 확인",
                        }
                    ],
                    "symbolic_equalities": [
                        {
                            "lhs": "factorial(11)",
                            "rhs": "2**8 * 3**4 * 5**2 * 7 * 11",
                            "variables": {},
                            "description": "11!의 소인수분해 검증",
                        }
                    ],
                },
            },
        )

    def generate(self) -> ProblemPair:
        from random import random

        if random() < 0.5:
            return ensure_problem_id(self._parity_problem(), prefix="parity")
        return ensure_problem_id(self._consecutive_product_problem(), prefix="factorial")
