from __future__ import annotations

import logging
from typing import List, Optional

from rich.console import Console

from agents import Agent, Runner
from agents.tracing import gen_trace_id, trace
from agents.model_settings import ModelSettings
from black import format_str, Mode

from database import Program
from utils.code import apply_diff, parse_evolve_blocks, extract_diffs
from utils.datatypes import (
    IdeaData,
    ProblemPair,
    ProblemSpec,
    PlanningOutput,
    ReportData,
    FeedbackBundle,
    reasoning_models,
)
from utils.format import format_metrics_safe

logger = logging.getLogger(__name__)

console = Console()

CODER_INSTRUCTIONS = """You are the code evolution developer. Your job is to IMPLEMENT the latest research idea into the codebase so that each iteration produces harder, properly verified problems.

Inputs you receive:
- Problem statement and solution draft (if provided), problem specification, inspirations and prior evaluation feedback, and the full concatenated code.

Your responsibilities:
1) Translate the research idea/specification into concrete code changes that affect generation and/or verification (e.g., `examples/math_problem_generation/initial_code/generator.py`, `verification.py`, `deepevolve_interface.py`, or evaluation prompts in `llm_evaluator.py`).
2) Ensure changes INCREASE difficulty over the previous iteration (e.g., stricter constraints, multi-part proofs, parameter ranges that avoid trivialities, cross-topic fusion) while maintaining a precise final answer and deterministic verification.
3) Keep the evaluation interface intact: do not rename `deepevolve_interface()` or remove metrics.
4) Prefer minimal, surgical edits that directly implement the idea. Avoid placeholder content.

Output policy (MANDATORY):
- Return one or more SEARCH/REPLACE diff blocks to patch the provided concatenated code. Use exactly this format:
```
<<<<<<< SEARCH
<original code snippet to match>
=======
### >>> DEEPEVOLVE-BLOCK-START: <short title>
<updated code>
### <<< DEEPEVOLVE-BLOCK-END
>>>>>>> REPLACE
```
- Include at least ONE diff that changes generation/verification so the next evaluation reflects higher difficulty. If you need to add small helper functions, patch the appropriate file with a focused replacement.

Notes:
- If you think the solution draft has issues, briefly describe them, then STILL provide diffs that implement your fixes.
- Do NOT output standalone JSON validations as your final answer; your output must be diffs.
"""

DEBUGGER_INSTRUCTIONS = """You are the Evaluation Agent. Your job is to challenge the generated math problem with large language models and feed difficulty feedback back into the system.

Responsibilities:
- Invoke configured LLM APIs with the provided problem statement (no access to the reference solution).
- Analyse the model's answer, compare it against the reference solution, and determine whether the model solved the problem.
- Record quantitative metrics (solve flag, similarity score, rationale quality, token usage) AND a qualitative feedback message that will be sent to the planner.
- If the model solves the problem easily, suggest concrete strategies to increase difficulty (parameter adjustments, extra constraints, additional proof requirements).
- If the model fails, summarise where it struggled and confirm the problem is sufficiently challenging.

When code modifications are needed (e.g., updating `llm_evaluator.py` prompts or scoring logic), respond with SEARCH/REPLACE diffs using this template:
```
<<<<<<< SEARCH
# Original evaluator code (must match exactly)
=======
### >>> DEEPEVOLVE-BLOCK-START: <evaluation refinement>
# Improved evaluator code or feedback handling
### <<< DEEPEVOLVE-BLOCK-END
>>>>>>> REPLACE
```

Guidelines:
1. Never reveal the reference solution to the evaluated model.
2. Use deterministic settings where possible (temperature, seeds) so results are reproducible.
3. Aggregate feedback in a structured form the planner can consume (message + actionable suggestions).
4. Keep API keys and secrets out of code responses.
5. Only modify files directly related to evaluation (`llm_evaluator.py`, evaluation helpers). Leave unrelated code untouched.
"""

INSPIRATION_TEMPLATE = """### Inspiration {inspiration_number}
- Research Idea : {idea}
- Performance: {performance}
- Code changes: {code_changes}
"""

# User message template for diff-based evolution
DIFF_CODE_TEMPLATE = """
User query: {query}
Research problem: {problem}

Inspirations:
{inspirations}

Current idea:
{current_idea}

Evolution history:
{idea_evolution}

Problem specification (JSON):
{problem_spec}

Problem statement:
{problem_statement}

Solution outline:
{solution_outline}

Verification notes:
{verification_notes}

Searcher context:
{search_context}

Evaluator feedback:
{evaluator_feedback}

Task:
Act as the code evolution developer. Provide SEARCH/REPLACE diffs that update the code to implement the current idea, making the generated problems harder while remaining verifiable and with precise final answers. Target files likely to change:
- examples/math_problem_generation/initial_code/generator.py (synthesize harder multi-step problems, fuse topics, widen parameter ranges avoiding trivialities; update ProblemMetadata/VerificationTasks accordingly)
- examples/math_problem_generation/initial_code/verification.py (add stronger symbolic/substitution checks; edge cases)
- examples/math_problem_generation/initial_code/llm_evaluator.py (if needed, adjust prompts/settings but keep API calls safe)
- examples/math_problem_generation/initial_code/deepevolve_interface.py (keep interface; may adjust output directory via env var)

Constraints:
- Keep `deepevolve_interface()` signature and metrics.
- Ensure deterministic checks and bounded runtime.
- Return ONLY the required diff blocks—no extra commentary.

Current program (concatenated; use EXACT substrings for SEARCH blocks):
```{language}
{current_program}
```

Reminder: In each diff, the SEARCH section must match a contiguous region in the current program EXACTLY (including whitespace and indentation). Do not wrap the diff itself in backticks; output only raw diff blocks.
"""

REFLECTION_CONTENT = """
1. Code Correctness
   - Are there any syntax errors or runtime errors?
   - Are there inconsistencies in variable names or logic flow?
   - Are there any new functions used but not been defined or implemented?
   - Avoid hiding missing modules or errors with a bare try/except that simply passes. Handle exceptions with clear warnings or errors.

2. Alignment with Research Idea
   - Does the code accurately implement the stated research idea?
      - Please make sure the changes in the function have actually been implemented in the workflow.
      - Avoid the code parts that suppress errors silently

3. Machine Learning Performance
   - Can compute efficiency be improved with minimal code changes?
   - Are there hyperparameters that could be tuned to boost performance?

4. Other Issues
   - At the end of each code review, provide a short summary of checks performed.
   - Avoid the code parts that suppress errors silently.
   - Are there any other issues you think are important?
"""


DEBUGGER_TEMPLATE = """
Resolve the following issue in the evaluation pipeline.

An error occurred during execution:
```
{error_message}
```

Below is the code that triggered the issue:
```{language}
{modified_code}
```

Context for this iteration:
- Problem specification: {problem_spec}
- Problem statement: {problem_statement}
- Reference solution (for verification only): {solution_outline}
- Evaluator feedback so far: {evaluator_feedback}
- Research idea JSON: {idea}

Your responsibilities:

- Diagnose and fix faults in the evaluator or verification workflow.
- Keep the reference solution hidden from the LLM evaluation step (the code may inspect it but never send it to the solver).
- Ensure the pipeline records solve status, similarity scores, rationale quality, and token counts.
- Provide structured feedback (message + actionable suggestions) for the planner when the evaluator runs.
- Maintain deterministic behaviour where possible (temperature/seed control) and meaningful logging.
- Return patches using the required diff format.
"""

class CoderAgent:
    def __init__(self, developer: str, debugger: str, reasoning_effort: str = 'medium'):
        self.developer = Agent(
            name="Code development agent",
            instructions=CODER_INSTRUCTIONS,
            model=developer,
            model_settings=ModelSettings(reasoning={'effort': reasoning_effort}) if developer in reasoning_models else ModelSettings(),
            output_type=str,
        )
        
        self.debugger = Agent(
            name="Code debugging agent",
            instructions=DEBUGGER_INSTRUCTIONS,
            model=debugger,
            model_settings=ModelSettings(reasoning={'effort': reasoning_effort}) if debugger in reasoning_models else ModelSettings(),
            output_type=str,
        )

        self.query = None
        self.problem_description = None
        self.language = None
        self.trace_id = None
        self.problem_name = 'NA'
        self.latest_planning: Optional[PlanningOutput] = None
        self.current_problem_spec: Optional[ProblemSpec] = None
        self.current_problem_pair: Optional[ProblemPair] = None
        self.current_feedback: Optional[FeedbackBundle] = None
        self.verification_notes: str = ""
        self.search_context: List[str] = []
        self.validation_report: Optional[dict] = None

    def update_topic(self, query: str, problem_name: str, problem_description: str):
        self.query = query
        self.problem_name = problem_name
        self.problem_description = problem_description

    def update_problem_context(
        self,
        planning_outputs: Optional[List[PlanningOutput]] = None,
        report: Optional[ReportData] = None,
        search_results: Optional[List[str]] = None,
    ) -> None:
        if planning_outputs:
            self.latest_planning = planning_outputs[-1]
            self.current_problem_spec = self.latest_planning.problem_spec
        else:
            self.latest_planning = None
            self.current_problem_spec = None

        if report:
            self.current_problem_spec = report.problem_spec or self.current_problem_spec
            self.current_problem_pair = report.problem_pair
            self.current_feedback = report.feedback
            self.verification_notes = report.verification_notes or ""
        else:
            self.current_problem_pair = None
            self.current_feedback = None
            self.verification_notes = ""
        self.validation_report = None

        if search_results is not None:
            self.search_context = search_results
        else:
            self.search_context = []

    def _format_problem_spec(self) -> str:
        if self.current_problem_spec is not None:
            return self.current_problem_spec.model_dump_json(indent=2, exclude_none=True)
        return "N/A"

    def _get_problem_statement(self) -> str:
        if self.current_problem_pair is not None:
            return self.current_problem_pair.problem_text
        return "N/A"

    def _get_solution_outline(self) -> str:
        if self.current_problem_pair is not None:
            return self.current_problem_pair.solution_text
        return "N/A"

    def _format_verification_notes(self) -> str:
        return self.verification_notes or "N/A"

    def _format_search_context(self) -> str:
        if not self.search_context:
            return "No recent search results."
        return "\n---\n".join(self.search_context)

    def _format_feedback(self) -> str:
        if self.current_feedback is None:
            return "No evaluator feedback yet."
        suggestions = "\n".join(f"- {item}" for item in self.current_feedback.suggestions)
        if suggestions:
            return f"{self.current_feedback.message}\nSuggestions:\n{suggestions}"
        return self.current_feedback.message

    def _extract_validation_report(self, text: str) -> Optional[dict]:
        marker = "VALIDATION_REPORT:"
        idx = text.find(marker)
        if idx == -1:
            return None
        json_segment = text[idx + len(marker):].strip()
        if json_segment.startswith("```"):
            json_segment = json_segment.strip("`").strip()
        if "}" in json_segment:
            json_segment = json_segment[: json_segment.rfind("}") + 1]
        try:
            import json

            data = json.loads(json_segment)
            return data
        except Exception as exc:
            logger.warning(f"Failed to parse validation report JSON: {exc}")
            return None

    async def debug(
        self, input_code: str, error_message: str,
    ) -> str:
        trace_id = self.trace_id
        if trace_id is None:
            trace_id = gen_trace_id()
            self.trace_id = trace_id

        with trace(f"DeepEvolve_{self.problem_name}", trace_id=trace_id, disabled=False):
            debugger_input = DEBUGGER_TEMPLATE.format(
                # query=self.query,
                error_message=error_message,
                modified_code=input_code,
                idea=self.idea.model_dump(),
                language=self.language,
                problem_spec=self._format_problem_spec(),
                problem_statement=self._get_problem_statement(),
                solution_outline=self._get_solution_outline(),
                evaluator_feedback=self._format_feedback(),
            )
            result = await Runner.run(self.debugger, debugger_input)

            logger.info(f"Debugger error message:\n {error_message}")
            logger.info(f"Debugger changes:\n {result.final_output_as(str)}")

            diff_with_text = result.final_output_as(str)
            output_code = apply_diff(input_code, diff_with_text)
            
            try:
                output_code = format_str(output_code, mode=Mode())
            except Exception as e:
                logger.warning(f"Error when formatting code: {e}")
                pass
            return output_code

    async def run(
        self,
        new_idea: IdeaData,
        program: Program,
        inspirations: list[Program],
        trace_id: str = None,
        max_reflection_times: int = 1,
    ) -> str:
        """Run the full code improvement pipeline with research context."""
        if trace_id is None:
            trace_id = gen_trace_id()
        self.trace_id = trace_id
        self.language = program.language
        self.idea = new_idea
        # format new idea
        idea_evolution = program.evolution_history
        if len(idea_evolution) > 0:
            idea_evolution = (
                " -> ".join(
                    [
                        f"[{i}] {idea.description}"
                        for i, idea in enumerate(idea_evolution)
                    ]
                )
                + " -> "
                + new_idea.description
            )
        else:
            idea_evolution = "Initial idea -> " + new_idea.description

        # format inspirations
        inspiration_str = ""
        for idx in range(len(inspirations)):
            performance_str = format_metrics_safe(inspirations[idx].metrics)
            code_changes = parse_evolve_blocks(inspirations[idx].code)
            code_changes_str = ""
            for start_line, end_line, block_content in code_changes:
                code_changes_str += f"Line {start_line} to {end_line}: ```{self.language}\n{block_content}```\n"
            inspiration_str += INSPIRATION_TEMPLATE.format(
                inspiration_number=idx,
                idea=inspirations[idx].idea,
                performance=performance_str,
                code_changes=code_changes_str,
            )
        if inspiration_str == "":
            inspiration_str = "No prior inspirations."

        program_code = program.code
        last_input_list = []
        all_diff_text = []
        all_program_code = []
        
        with trace(f"DeepEvolve_{self.problem_name}", trace_id=trace_id, disabled=False):
            logger.info(f"Starting code development ...")
            for ref_idx in range(max_reflection_times + 1):
                if ref_idx > 0:
                    console.print(
                        f"[bold green] coding reflection: {ref_idx} / {max_reflection_times}[/bold green]"
                    )
                    
                current_performance = format_metrics_safe(program.metrics)
                problem_spec_json = self._format_problem_spec()
                problem_statement = self._get_problem_statement()
                solution_outline = self._get_solution_outline()
                verification_notes = self._format_verification_notes()
                search_context = self._format_search_context()
                evaluator_feedback = self._format_feedback()
                code_prompt = DIFF_CODE_TEMPLATE.format(
                    query=self.query,
                    problem=self.problem_description,
                    inspirations=inspiration_str,
                    current_idea=new_idea.description,
                    idea_evolution=idea_evolution,
                    problem_spec=problem_spec_json,
                    problem_statement=problem_statement,
                    solution_outline=solution_outline,
                    verification_notes=verification_notes,
                    search_context=search_context,
                    evaluator_feedback=evaluator_feedback,
                    pseudocode=new_idea.pseudocode,
                    implementation_notes=new_idea.implementation_notes,
                    language=self.language,
                    current_performance=current_performance,
                    current_program=program_code,
                )

                if ref_idx > 0:
                    code_prompt += f"\n\nGiven the previous diff: ```{self.language}\n{all_diff_text[-1]}```"
                    code_prompt += f"\n\nPlease review the code and reflect on the content below: {REFLECTION_CONTENT}"
                    code_prompt += (
                        f"\n\nPlease provide the new diff to improve the code."
                    )

                code_input = last_input_list + [
                    {"content": code_prompt, "role": "user"}
                ]

                result = await Runner.run(self.developer, input=code_input)
                last_input_list = result.to_input_list()
                developer_output = result.final_output_as(str)

                validation = self._extract_validation_report(developer_output)
                if validation is not None:
                    # Record validation, then request concrete diffs in the next round
                    self.validation_report = validation
                    logger.info("Developer supplied validation report. Requesting concrete diffs next.")
                    # Augment prompt for next iteration
                    last_input_list.append({
                        "role": "user",
                        "content": (
                            "Based on your VALIDATION_REPORT above, now return SEARCH/REPLACE diffs that IMPLEMENT your fixes. "
                            "Include at least one diff touching generator/verification to increase difficulty while keeping verification deterministic."
                        ),
                    })
                    continue

                diff_blocks = extract_diffs(developer_output)
                if not diff_blocks:
                    logger.warning(
                        "Developer output lacked valid SEARCH/REPLACE diff blocks. Requesting correction."
                    )
                    last_input_list.append(
                        {
                            "role": "user",
                            "content": (
                                "Your previous response did not include a valid SEARCH/REPLACE diff. "
                                "Please respond with one or more diff blocks using the exact format with '<<<<<<< SEARCH', '=======', '>>>>>>> REPLACE', and copy the current code verbatim into the SEARCH section."
                            ),
                        }
                    )
                    continue

                prev_program_code = program_code
                program_code_candidate = apply_diff(prev_program_code, developer_output)
                if program_code_candidate == prev_program_code:
                    logger.warning(
                        "Developer diff did not apply due to search mismatch or conflict markers. Requesting correction."
                    )
                    last_input_list.append(
                        {
                            "role": "user",
                            "content": (
                                "The diff you provided could not be applied to the current code. "
                                "Ensure the SEARCH block exactly matches the existing code and update only the necessary lines in the REPLACE block."
                            ),
                        }
                    )
                    continue
                program_code = program_code_candidate

                try:
                    program_code = format_str(program_code, mode=Mode())
                except Exception as e:
                    logger.warning(f"Error when formatting code: {e}")
                    pass

                all_diff_text.append(developer_output)
                all_program_code.append(program_code)

            logger.info(f"Completed code development with {max_reflection_times} reflection rounds.")
            return all_diff_text, all_program_code
