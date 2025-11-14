from __future__ import annotations

from pathlib import Path
import json
import os

from utils.datatypes import ProblemPair, ProblemMetadata
from utils.code import ensure_problem_id


class MathProblemGenerator:
    """
    FrontierMath problem generator.

    If the research pipeline has produced a fresh problem it is used; otherwise we
    fall back to a curated list of FrontierMath benchmark problems. The fallback
    seeds are consumed in a round-robin order so each seed is used as an initial
    problem before repetition.
    """

    def __init__(self) -> None:
        self._frontier_entries = self._frontier_math_entries()
        self._research_dir = Path(__file__).resolve().parent.parent / "research"
        self._seed_pointer_path = self._research_dir / "seed_pointer.json"

    def generate(self) -> ProblemPair:
        research_problem = self._from_research()
        if research_problem is not None:
            return research_problem

        entry = self._next_seed_entry()
        problem = self._build_problem(entry)
        return ensure_problem_id(problem, prefix=entry.get("prefix", "frontier"))

    def _from_research(self) -> ProblemPair | None:
        """Load the latest problem synthesized by the researcher, if present."""
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
                return ensure_problem_id(ProblemPair(**data), prefix="research")
        except Exception:
            return None
        return None

    def _build_problem(self, entry: dict) -> ProblemPair:
        metadata = ProblemMetadata(
            status="frontier_math",
            difficulty_message=entry.get("difficulty_message"),
            difficulty_suggestions=entry.get("difficulty_suggestions", []),
        )
        theorem_refs = self._normalize_theorem_refs(entry.get("theorem_refs", []))
        return ProblemPair(
            id="",
            problem_text=entry["problem_text"],
            solution_text=entry["solution_text"],
            tags=entry.get("tags", []),
            prerequisites=entry.get("prerequisites", []),
            theorem_refs=theorem_refs,
            metadata=metadata,
        )

    def _next_seed_entry(self) -> dict:
        entries = self._frontier_entries
        if not entries:
            raise RuntimeError("No frontier seed entries defined.")

        # ensure research directory exists
        self._research_dir.mkdir(parents=True, exist_ok=True)

        idx = 0
        if self._seed_pointer_path.exists():
            try:
                with open(self._seed_pointer_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    idx = int(data.get("index", 0))
            except Exception:
                idx = 0

        entry = entries[idx % len(entries)]
        next_idx = (idx + 1) % len(entries)
        try:
            with open(self._seed_pointer_path, "w", encoding="utf-8") as f:
                json.dump({"index": next_idx}, f)
        except Exception:
            pass
        return entry

    def _normalize_theorem_refs(self, refs) -> list[dict]:
        """Ensure theorem references are dictionaries with at least a name field."""
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
                normalized.append(
                    {
                        "name": item.strip(),
                        "statement": "",
                        "source": "",
                    }
                )
        return normalized

    def _frontier_math_entries(self) -> list[dict]:
        return [
            {
                "prefix": "frontier_seed_gaussian_bmo",
                "problem_text": (
                    "Let f : [0,1] → ℝ be integrable with ∫₀¹ f(t) dt = -10, ∫₀¹ f(t)² dt = 100 + 1/12, "
                    "and sup_{J⊂[0,1]} |J|^{-1} ∫_J |f(t) - |J|^{-1}∫_J f(s) ds|² dt ≤ 1/12. "
                    "Define c = sup_f ∫₀¹ (f(t)³ + |f(t)|) dt. Compute c + 1985/2."
                ),
                "solution_text": (
                    "The Bellman-function method converts the constrained optimisation into a Monge–Ampère equation. "
                    "Boundary data force the extremiser to be a piecewise constant “trolleybus” profile, so the "
                    "supremum equals B(-10, 100 + 1/12; 1/12). Adding 1985/2 to this value yields the requested "
                    "quantity."
                ),
                "tags": ["analysis", "optimization", "bmo"],
                "prerequisites": ["Real analysis", "Calculus of variations", "BMO theory"],
            },
            {
                "prefix": "frontier_seed_modular_pair",
                "problem_text": (
                    "With F(z), G(z), and α as defined in the benchmark problem, and primes ℓ₁, ℓ₂ satisfying the "
                    "listed conditions, compute Ω = (1/[K_α:ℚ]) · (P_α(ℓ₁) + P_α(ℓ₂))^{ℓ₂-1}."
                ),
                "solution_text": (
                    "The unique prime pair is ℓ₁ = 227, ℓ₂ = 5. Ramanujan’s mock theta function shows "
                    "α = -4ζ⁶ + 4ζ⁵ - 4ζ² + 12 for ζ = e^{2πi·227/20}. From this one finds "
                    "P_α(227) = 4,931,792,858,591,731,505 and P_α(5) = 6,546,641. Because [K_α:ℚ] = 8, "
                    "Ω = 1/8 · (P_α(227) + P_α(5))⁴ = 73948492097301691765464030714938921180352979774791349212169829996615700482."
                ),
                "tags": ["number_theory", "elliptic_curves", "galois_theory"],
                "prerequisites": ["Complex analysis", "Modular forms", "Galois theory"],
            },
            {
                "prefix": "frontier_seed_tsirelson",
                "problem_text": (
                    "In Tsirelson’s Banach space T, determine the largest N for which there exists x ∈ T with x₃ = 1/2, "
                    "x_N > 1/20, and x_i > x_j for every 3 ≤ i < j ≤ N. Writing N = m·2ᵖ with m odd, find p."
                ),
                "solution_text": (
                    "Nested block constructions yield vectors whose final support occurs at f³(3) with f(n) = n·2ⁿ. "
                    "The 2-adic valuation of f³(3) equals 402,653,211, and density arguments show this is optimal; "
                    "hence p = 402,653,211."
                ),
                "tags": ["functional_analysis", "banach_spaces", "combinatorics"],
                "prerequisites": ["Banach space theory", "Infinite-dimensional combinatorics"],
            },
            {
                "prefix": "frontier_seed_conic_tangency",
                "problem_text": (
                    "For the finite étale morphism π : Z → U⁵ that records tangency configurations of smooth conics, "
                    "evaluate L = lim_{p→∞} |V(𝔽_p)|^{-1} Σ_{x∈V(𝔽_p)} |π^{-1}(x)| and compute ⌊100 L⌋."
                ),
                "solution_text": (
                    "Harris demonstrated that π has degree 3264 with full Galois group S₃₂₆₄, and this remains true over "
                    "large finite fields. The average fibre size is the harmonic number H₃₂₆₄, so L ≈ 8.668 and "
                    "⌊100 L⌋ = 866."
                ),
                "tags": ["algebraic_geometry", "enumerative_geometry"],
                "prerequisites": ["Algebraic geometry", "Galois theory"],
            },
            {
                "prefix": "frontier_seed_artin_density",
                "problem_text": (
                    "Let d_x be the density of primes p with ord_{p,x}(2) > ord_{p,x}(3) in the Artin primitive root "
                    "framework. Compute d_∞ = lim_{x→∞} d_x and report ⌊10⁶ d_∞⌋."
                ),
                "solution_text": (
                    "Chebotarev density and inclusion–exclusion express d_∞ as an Euler product. Numerical evaluation "
                    "gives d_∞ ≈ 0.367707, and therefore ⌊10⁶ d_∞⌋ = 367,707."
                ),
                "tags": ["analytic_number_theory", "galois_theory"],
                "prerequisites": ["Analytic number theory", "Field theory"],
            },
            {
                "prefix": "frontier_seed_permutation_map",
                "problem_text": (
                    "Let F be the recursive map on words that sends a permutation σ ∈ S_{10^{12}} to F(σ). "
                    "If X is the expected number of indices i with (F(σ))^{-1}(i+1) < (F(σ))^{-1}(i), compute ⌊X⌋."
                ),
                "solution_text": (
                    "For each i, the probability that i+1 precedes i in F(σ) equals 1/2 - 1/(n+1). Summing yields "
                    "X = (n+1)/2 - H_n. Substituting n = 10^{12} gives ⌊X⌋ = 499,999,999,972."
                ),
                "tags": ["combinatorics", "probability"],
                "prerequisites": ["Combinatorics", "Probability theory"],
            },
            {
                "prefix": "frontier_seed_divisor_asymptotics",
                "problem_text": (
                    "Count the solutions to ab + 1 = cde with a, b, c, d, e ∈ ℕ and ab ≤ x. Find constants C, α, β so that "
                    "the count is asymptotic to C x^α log^β x, and determine ⌊1000 C⌋."
                ),
                "solution_text": (
                    "The count equals Σ_{n≤x} d_2(n) d_3(n+1). Known analytic results provide "
                    "Σ_{n≤x} d_2(n) d_3(n+1) ∼ (C_{2,3}/2) x log³ x with "
                    "C_{2,3} = ∏_p ((1-1/p) + (1-1/p)² - (1-1/p)³) ≈ 0.4284. Thus α = 1, β = 3, "
                    "C ≈ 0.2142, and ⌊1000 C⌋ = 214."
                ),
                "tags": ["analytic_number_theory", "divisor_functions"],
                "prerequisites": ["Analytic number theory", "Multiplicative functions"],
            },
            {
                "prefix": "frontier_seed_degree19_poly",
                "problem_text": (
                    "Construct a monic, odd polynomial p(x) ∈ ℂ[x] of degree 19 with linear term −19x such that the curve "
                    "{(x, y) ∈ ℙ¹×ℙ¹ : p(x) = p(y)} has at least three irreducible components, not all linear. Compute p(19)."
                ),
                "solution_text": (
                    "Monodromy considerations force p to arise from the Chebyshev polynomial. Imposing oddness and "
                    "monicity yields p(x) = 2·T_{19}(x/2), so p(19) = 2·T_{19}(19/2)."
                ),
                "tags": ["algebraic_geometry", "polynomials", "galois_theory"],
                "prerequisites": ["Complex analysis", "Algebraic number theory"],
            },
            {
                "prefix": "frontier_seed_padic_extension",
                "problem_text": (
                    "Consider the recurrence a_n = 198130309625 a_{n-1} + 354973292077 a_{n-2} − 427761277677 a_{n-3} "
                    "+ 370639957 a_{n-4} with a_i = i for 0 ≤ i ≤ 3. Find the smallest prime p ≡ 4 (mod 7) such that "
                    "n ↦ a_n extends continuously to ℤ_p."
                ),
                "solution_text": (
                    "The Skolem–Mahler–Lech criterion requires each characteristic root to be p-adically close to 1. "
                    "Among the candidate primes only p = 9811 works, and reduction modulo 9811 shows all roots coalesce "
                    "to (x−1)⁴. Hence p = 9811."
                ),
                "tags": ["p_adic_analysis", "linear_recurrences"],
                "prerequisites": ["p-adic number theory", "Linear algebra"],
            },
            {
                "prefix": "frontier_seed_cubic_curve",
                "problem_text": (
                    "How many non-zero projective points over 𝔽_{5^{18}} satisfy the homogeneous cubic "
                    "equation x³y + y³z + z³x = 0?"
                ),
                "solution_text": (
                    "The zeta function of the curve shows |C(𝔽_{5^n})| = 5^n + 1 − Σ α_i^n with |α_i| = √5 determined "
                    "from small field counts. Consequently |C(𝔽_{5^{18}})| = 5^{18} + 6·5⁹ + 1, and removing the point "
                    "at infinity leaves 3,814,708,984,376 non-zero projective solutions."
                ),
                "tags": ["finite_fields", "algebraic_geometry"],
                "prerequisites": ["Finite field theory", "Weil conjectures"],
            },
            {
                "prefix": "frontier_seed_random_polygon",
                "problem_text": (
                    "Each diagonal of a regular 101-gon of circumradius 1 is drawn independently with probability 0.001. "
                    "Let E be the expected perimeter of the region containing the centre. Compute E and ⌊10⁹ E⌋."
                ),
                "solution_text": (
                    "Classify diagonals relative to a fixed segment as blockers or crossers. A segment contributes to the "
                    "centre region precisely when no blockers appear and no later crossers occur on either side. Summing "
                    "the contributions yields E ≈ 4.771880153 × 10^{-1}, so ⌊10⁹ E⌋ = 4,771,880,153."
                ),
                "tags": ["probability", "computational_geometry"],
                "prerequisites": ["Probability theory", "Geometry"],
            },
            {
                "prefix": "frontier_seed_matrix_orbits",
                "problem_text": (
                    "Let S be the set of quadruples (A₁, A₂, A₃, A₄) of invertible 1000×1000 complex matrices satisfying "
                    "A_i² = I and the Coxeter-style relations stated in the benchmark problem. Determine the number of "
                    "GL(1000)-conjugacy orbits |S/GL(1000)|."
                ),
                "solution_text": (
                    "The relations coincide with those of the Coxeter group S₅, so each tuple corresponds to a 1000-dimensional "
                    "complex representation of S₅. Conjugacy orbits match representation isomorphism classes, i.e. "
                    "decompositions of 1000 into irreducible dimensions {1,1,4,4,5,5,6}. The generating function "
                    "(1 − t)^{-2}(1 − t⁴)^{-2}(1 − t⁵)^{-2}(1 − t⁶)^{-1} has coefficient 625,243,878,951 at t^{1000}, "
                    "which equals |S/GL(1000)|."
                ),
                "tags": ["representation_theory", "linear_algebra"],
                "prerequisites": ["Representation theory", "Linear algebra"],
            },
        ]
