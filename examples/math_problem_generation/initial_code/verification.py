from __future__ import annotations

import random
from typing import Dict, Iterable, List, Optional, Tuple

import sympy as sp

from utils.datatypes import (
    ExtraDataItem,
    ProblemMetadata,
    ProblemPair,
    SubstitutionCheck,
    SymbolicEqualityCheck,
    VariableConstraint,
    VerificationReport,
    VerificationTasks,
)


class VerificationError(Exception):
    """Raised when verification encounters an unrecoverable error."""


class VerificationRunner:
    """
    Execute symbolic and sampling-based checks to validate the drafted solution.

    기본 전략:
    1) problem.metadata.verification_tasks에 정의된 지침을 기반으로 검증.
    2) substitution 테스트: 지정된 범위의 정수/실수 샘플을 치환해 기대 결과와 일치하는지 검사.
    3) symbolic equalities: SymPy를 활용해 두 식의 차이를 단순화, 필요 시 추가 샘플링으로 확인.
    4) 실패한 첫 번째 반례나 오류를 기록하여 추후 Planner/Developer가 참고할 수 있도록 함.
    """

    def __init__(self, seed: int = 42, max_attempts: int = 256):
        self._rng = random.Random(seed)
        self._max_attempts = max_attempts

    def run(self, problem: ProblemPair) -> VerificationReport:
        metadata: ProblemMetadata = problem.metadata or ProblemMetadata()
        tasks: VerificationTasks = metadata.verification_tasks or VerificationTasks()

        substitution_result, substitution_notes, counterexamples = self._run_substitution_checks(
            tasks.substitution
        )
        symbolic_result, symbolic_notes = self._run_symbolic_checks(
            tasks.symbolic_equalities
        )

        notes: List[str] = []
        if substitution_notes:
            notes.append(substitution_notes)
        if symbolic_notes:
            notes.append(symbolic_notes)
        if not tasks.substitution and not tasks.symbolic_equalities:
            notes.append("verification_tasks metadata not provided; defaulted to pass with warnings.")

        extra_data: List[ExtraDataItem] = []
        if counterexamples:
            extra_data.append(
                ExtraDataItem(
                    key="counterexamples",
                    value=repr(counterexamples[:5]),
                )
            )

        return VerificationReport(
            substitution_pass=substitution_result,
            symbolic_pass=symbolic_result,
            counterexample_found=bool(counterexamples),
            notes="\n".join(notes).strip(),
            extra_data=extra_data,
        )

    # ------------------------------------------------------------------
    # Substitution checks
    # ------------------------------------------------------------------
    def _run_substitution_checks(
        self, checks: Iterable[SubstitutionCheck]
    ) -> Tuple[bool, str, List[Dict[str, str]]]:
        if not checks:
            return True, "No substitution checks specified.", []

        overall_pass = True
        notes: List[str] = []
        counterexamples: List[Dict[str, str]] = []

        for idx, spec in enumerate(checks, start=1):
            try:
                expr = self._parse_expression(spec.expression)
            except VerificationError as exc:
                overall_pass = False
                notes.append(f"[Substitution {idx}] Failed to parse expression: {exc}")
                continue

            variables = self._create_symbols(spec.variables)
            num_samples = int(spec.num_samples or 30)

            failures: List[Dict[str, str]] = []
            success_count = 0

            for assignment in self._sample_assignments(variables, num_samples):
                try:
                    value = sp.simplify(expr.subs(assignment))
                except Exception as exc:  # pragma: no cover - sympy edge cases
                    failures.append(
                        {
                            "reason": f"substitution_error: {exc}",
                            "assignment": repr(assignment),
                        }
                    )
                    continue

                if not self._check_substitution_result(spec, value, assignment, failures):
                    overall_pass = False
                    counterexamples.append(
                        {
                            "expression": spec.expression,
                            "value": repr(value),
                            "assignment": repr(assignment),
                        }
                    )
                else:
                    success_count += 1

                if len(failures) > 3:
                    break

            description = spec.description or spec.expression or f"check-{idx}"
            if failures:
                notes.append(
                    f"[Substitution {idx}] {description}: {len(failures)} failure(s) out of {num_samples} samples. "
                    f"Examples: {failures[:2]}"
                )
            else:
                notes.append(
                    f"[Substitution {idx}] {description}: passed ({success_count}/{num_samples})."
                )

        return overall_pass, "\n".join(notes), counterexamples

    def _check_substitution_result(
        self,
        spec: SubstitutionCheck,
        value: sp.Expr,
        assignment: Dict[sp.Symbol, sp.Expr],
        failures: List[Dict[str, str]],
    ) -> bool:
        """
        Validate the substituted expression result against the expectations.
        Supports several spec keys:
            - expected: exact equality to a numeric constant
            - mod + expected / expected_mod
            - target_values: value must belong to set
            - predicate: custom expression that should evaluate True
        """
        if value.has(sp.zoo, sp.nan):
            failures.append({"reason": "non-finite", "assignment": repr(assignment)})
            return False
        expected = spec.expected
        expected_mod = spec.remainder
        modulus = spec.modulus
        target_values = spec.target_values or None
        predicate = spec.predicate

        simplified_value = sp.simplify(value)
        try:
            numeric_value = float(simplified_value)
        except Exception:
            numeric_value = None

        # Exact equality
        if expected is not None:
            try:
                expected_val = sp.simplify(expected)
            except Exception:
                expected_val = expected
            if simplified_value != expected_val:
                failures.append(
                    {
                        "reason": f"value {simplified_value} != expected {expected_val}",
                        "assignment": repr(assignment),
                    }
                )
                return False

        # Modulo checks
        if modulus is not None:
            try:
                modulus_val = int(modulus)
                remainder = int(sp.Mod(simplified_value, modulus_val))
            except Exception:
                failures.append(
                    {
                        "reason": f"mod computation failed for value {simplified_value}",
                        "assignment": repr(assignment),
                    }
                )
                return False

            if expected_mod is not None and remainder != int(expected_mod):
                failures.append(
                    {
                        "reason": f"remainder {remainder} != expected {expected_mod} (mod {modulus_val})",
                        "assignment": repr(assignment),
                    }
                )
                return False

            if target_values and remainder not in [int(v) for v in target_values]:
                failures.append(
                    {
                        "reason": f"remainder {remainder} not in allowed set {target_values}",
                        "assignment": repr(assignment),
                    }
                )
                return False

        elif target_values is not None:
            evaluated = numeric_value if numeric_value is not None else simplified_value
            allowed = [sp.simplify(v) for v in target_values]
            if evaluated not in allowed:
                failures.append(
                    {
                        "reason": f"value {evaluated} not in {allowed}",
                        "assignment": repr(assignment),
                    }
                )
                return False

        # Custom predicate: expression that should evaluate True
        if predicate is not None:
            pred_expr = self._parse_expression(predicate)
            pred_value = pred_expr.subs(assignment)
            if not bool(pred_value):
                failures.append(
                    {
                        "reason": f"predicate {predicate} evaluated to False",
                        "assignment": repr(assignment),
                    }
                )
                return False

        return True

    # ------------------------------------------------------------------
    # Symbolic equalities
    # ------------------------------------------------------------------
    def _run_symbolic_checks(
        self, specs: Iterable[SymbolicEqualityCheck]
    ) -> Tuple[bool, str]:
        if not specs:
            return True, "No symbolic equality checks specified."

        overall_pass = True
        notes: List[str] = []

        for idx, spec in enumerate(specs, start=1):
            description = spec.description or f"symbolic-equality-{idx}"
            try:
                lhs = self._parse_expression(spec.lhs)
                rhs = self._parse_expression(spec.rhs)
            except VerificationError as exc:
                overall_pass = False
                notes.append(f"[Symbolic {idx}] {description}: parsing failed ({exc})")
                continue

            variables = self._create_symbols(spec.variables)
            diff = sp.simplify(lhs - rhs)

            if diff == 0:
                notes.append(f"[Symbolic {idx}] {description}: passed via direct simplification.")
                continue

            # Attempt factoring or other simplifications
            simplified_diff = sp.simplify(sp.factor(diff))
            if simplified_diff == 0:
                notes.append(f"[Symbolic {idx}] {description}: passed after factor simplification.")
                continue

            # Fallback to random sampling
            sample_failures = self._sample_symbolic_counterexamples(
                simplified_diff, variables
            )
            if sample_failures:
                overall_pass = False
                notes.append(
                    f"[Symbolic {idx}] {description}: failed. Counterexamples: {sample_failures[:2]}"
                )
            else:
                notes.append(
                    f"[Symbolic {idx}] {description}: passed via random sampling despite non-zero simplified diff."
                )

        return overall_pass, "\n".join(notes)

    def _sample_symbolic_counterexamples(
        self, diff: sp.Expr, variables: Dict[str, sp.Symbol], samples: int = 50
    ) -> List[Dict[str, str]]:
        failures: List[Dict[str, str]] = []
        for assignment in self._sample_assignments(variables, samples):
            try:
                value = sp.simplify(diff.subs(assignment))
            except Exception as exc:  # pragma: no cover
                failures.append({"reason": f"substitution error: {exc}", "assignment": repr(assignment)})
                continue
            if value != 0:
                failures.append(
                    {
                        "reason": f"value {value} != 0",
                        "assignment": repr(assignment),
                    }
                )
            if len(failures) > 3:
                break
        return failures

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _parse_expression(self, expression: Optional[str]) -> sp.Expr:
        if not expression:
            raise VerificationError("Expression is missing.")
        try:
            return sp.sympify(expression, convert_xor=True)
        except Exception as exc:
            raise VerificationError(f"Could not parse expression '{expression}': {exc}") from exc

    def _create_symbols(self, spec: Iterable[VariableConstraint]) -> Dict[str, Dict[str, object]]:
        symbols: Dict[str, Dict[str, object]] = {}
        for variable in spec:
            kind = (variable.kind or "real")
            assumptions = {}
            if kind == "integer":
                assumptions["integer"] = True
            elif kind == "positive_integer":
                assumptions["integer"] = True
                assumptions["positive"] = True
            elif kind == "natural":
                assumptions["integer"] = True
                assumptions["nonnegative"] = True
            elif kind == "real":
                assumptions["real"] = True
            metadata: Dict[str, object] = {"type": kind}
            if variable.minimum is not None:
                metadata["min"] = variable.minimum
            if variable.maximum is not None:
                metadata["max"] = variable.maximum
            symbols[variable.name] = {
                "symbol": sp.symbols(variable.name, **assumptions),
                "meta": metadata,
            }
        return symbols

    def _sample_assignments(
        self,
        symbols: Dict[str, Dict[str, object]],
        num_samples: int,
    ) -> Iterable[Dict[sp.Symbol, sp.Expr]]:
        entries = list(symbols.values())

        for _ in range(num_samples):
            assignment: Dict[sp.Symbol, sp.Expr] = {}
            for entry in entries:
                symbol: sp.Symbol = entry["symbol"]
                metadata: Dict[str, object] = entry["meta"]
                assignment[symbol] = self._sample_value(metadata)
            yield assignment

    def _sample_value(self, metadata: Dict[str, object]) -> sp.Expr:
        kind = metadata.get("type", "integer")
        if kind in {"integer", "positive_integer", "natural"}:
            min_value = int(metadata.get("min", 0 if kind != "integer" else -20))
            max_value = int(metadata.get("max", 20))
            if min_value > max_value:
                min_value, max_value = max_value, min_value
            value = self._rng.randint(min_value, max_value)
            if kind == "positive_integer" and value <= 0:
                value = max(1, abs(value))
            if kind == "natural" and value < 0:
                value = abs(value)
            return sp.Integer(value)

        if kind == "real":
            min_value = float(metadata.get("min", -10.0))
            max_value = float(metadata.get("max", 10.0))
            value = self._rng.uniform(min_value, max_value)
            return sp.Float(value)

        # Fallback to integer sampling
        return sp.Integer(self._rng.randint(-10, 10))
