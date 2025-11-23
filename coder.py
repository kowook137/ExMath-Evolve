from __future__ import annotations

import logging
from typing import List, Optional

from rich.console import Console

from black import format_str, Mode

from database import Program
from prompts import (
    CODER_INSTRUCTIONS,
    DEBUGGER_INSTRUCTIONS,
    DIFF_CODE_TEMPLATE,
    INSPIRATION_TEMPLATE,
    REFLECTION_CONTENT,
)
from langchain_llm import LCAgent, LCRunner
from tracing_compat import gen_trace_id, trace
from utils.code import apply_diff, parse_evolve_blocks, extract_diffs
from utils.datatypes import (
    IdeaData,
    ProblemPair,
    ProblemSpec,
    PlanningOutput,
    ReportData,
    FeedbackBundle,
    EvaluationData,
    reasoning_models,
)
from utils.format import format_metrics_safe

logger = logging.getLogger(__name__)

console = Console()



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
        self.developer = LCAgent(
            name="Code development agent",
            instructions=CODER_INSTRUCTIONS,
            model=developer,
            output_type=str,
        )
        
        self.debugger = LCAgent(
            name="Code debugging agent",
            instructions=DEBUGGER_INSTRUCTIONS,
            model=debugger,
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
                error_message=error_message,
                modified_code=input_code,
                idea=self.idea.model_dump(),
                language=self.language,
                problem_spec=self._format_problem_spec(),
                problem_statement=self._get_problem_statement(),
                solution_outline=self._get_solution_outline(),
                evaluator_feedback=self._format_feedback(),
            )
            result = await LCRunner.run(self.debugger, debugger_input)

            logger.info(f"Debugger error message:\n {error_message}")
            logger.info(f"Debugger changes:\n {result.final_output_as(str)}")

            diff_with_text = result.final_output_as(str)
            output_code = apply_diff(input_code, diff_with_text)
            
            try:
                output_code = format_str(output_code, mode=Mode())
            except Exception as e:
                logger.warning(f"Error when formatting code: {e}")
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
        if new_idea is None:
            logger.warning("No idea provided; using placeholder idea to continue pipeline.")
            default_eval = EvaluationData(score=5, positive="placeholder", negative="placeholder")
            new_idea = IdeaData(
                description="Placeholder idea (upstream report missing idea field)",
                motivation="Autofilled because writer agent returned no idea.",
                implementation_notes="",
                pseudocode="",
                originality=default_eval,
                future_potential=default_eval,
                code_difficulty=default_eval,
                target_difficulty=None,
            )
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
            meta = inspirations[idx].metadata or {}
            seed_flag = meta.get("is_seed_inspiration")
            meta_line = f"is_seed_inspiration={seed_flag}" if seed_flag is not None else "is_seed_inspiration=False"
            code_changes = parse_evolve_blocks(inspirations[idx].code)
            code_changes_str = ""
            for start_line, end_line, block_content in code_changes:
                code_changes_str += f"Line {start_line} to {end_line}: ```{self.language}\n{block_content}```\n"
            inspiration_str += INSPIRATION_TEMPLATE.format(
                inspiration_number=idx,
                idea=f"{inspirations[idx].idea} ({meta_line})",
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

                if ref_idx > 0 and all_diff_text:
                    code_prompt += f"\n\nGiven the previous diff: ```{self.language}\n{all_diff_text[-1]}```"
                    code_prompt += f"\n\nPlease review the code and reflect on the content below: {REFLECTION_CONTENT}"
                    code_prompt += (
                        f"\n\nPlease provide the new diff to improve the code."
                    )

                code_input = last_input_list + [
                    {"content": code_prompt, "role": "user"}
                ]

                result = await LCRunner.run(self.developer, input=code_input)
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
                    logger.error("Developer output lacked valid SEARCH/REPLACE diff blocks. Stopping.")
                    raise ValueError("Developer did not return required SEARCH/REPLACE diff blocks.")

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
