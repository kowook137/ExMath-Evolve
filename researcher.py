from __future__ import annotations

import asyncio
import logging
import json
from rich.console import Console
from datetime import datetime

from langchain_llm import LCAgent, LCRunner
from tracing_compat import gen_trace_id, trace, custom_span

from database import Program
from prompts import (
    PLANNER_INSTRUCTIONS,
    REFLECTION_INSTRUCTIONS,
    SEARCH_INSTRUCTIONS,
    WRITER_INSTRUCTIONS,
    USER_TEMPLATE,
    PAPER_READER_INSTRUCTIONS,
    REFLECTION_CONTENT_RESEARCH,
    INSPIRATION_TEMPLATE,
)
from utils.datatypes import (
    ReportData,
    IdeaData,
    EvaluationData,
    WebSearchPlan,
    WebSearchItem,
    ReflectionPlan,
    PlanningOutput,
    ProblemSpec,
    reasoning_models,
)
from utils.format import format_metrics_safe

logger = logging.getLogger(__name__)

console = Console()



class ResearcherAgent:
    def __init__(
        self,
        planner: str = "o3-mini",
        searcher: str = "gpt-4o",
        writer: str = "o3-mini",
        reasoning_effort: str = 'medium',
    ):
        self.planner_agent = LCAgent(
            name="Planner Agent",
            instructions=PLANNER_INSTRUCTIONS,
            model=planner,
            output_type=PlanningOutput,
        )
        self.reflection_agent = LCAgent(
            name="Reflection Agent",
            instructions=REFLECTION_INSTRUCTIONS,
            model=planner,
            output_type=ReflectionPlan,
        )
        self.search_agent = LCAgent(
            name="Search Agent",
            instructions=SEARCH_INSTRUCTIONS,
            model=searcher,
            output_type=str,
        )
        self.writer_agent = LCAgent(
            name="Writing Agent",
            instructions=WRITER_INSTRUCTIONS,
            model=writer,
            output_type=ReportData,
        )
        self.reader_agent = LCAgent(
            name="Paper Reader Agent",
            instructions=PAPER_READER_INSTRUCTIONS,
            model=searcher,
            output_type=IdeaData,
        )
        self.search_time_bias = False
        self.problem_name = 'NA'

    def update_topic(
        self, query: str, problem_name: str, problem_description: str, search_time_bias: bool = False
    ):
        self.query = query
        self.problem_name = problem_name
        self.problem_description = problem_description
        self.search_time_bias = search_time_bias

    async def read_paper(self, title: str, content: str, supplementary_info: str = None) -> IdeaData:
        query = f"title: {title} \ncontent: {content}"
        if supplementary_info is not None:
            query += f"\n supplementary_info: {supplementary_info}"
        result = await LCRunner.run(
            self.reader_agent,
            query,
        )
        return result.final_output_as(IdeaData)

    async def run(
        self,
        program: Program,
        inspirations: list[Program],
        trace_id: str = None,
        max_reflection_times: int = 1,
        max_generations: int = 10,
    ) -> tuple[list[PlanningOutput], list[list[str]], list[ReportData]]:
        """
        Execute the research process from planning to report generation.

        Args:
            query: The research question to investigate
            idea_evolution: Evolution history of the idea
            evolution_progress: Current evolution progress/research stage
            trace_id: Optional trace identifier for logging

        Returns:
            planning_outputs: List of planner blueprints paired with search plans
            search_results: Lists of summaries retrieved by the searcher for each cycle
            reports: Writer outputs containing the drafted problem, solution, and metadata
        """
        idea_evolution = program.evolution_history
        evolution_progress = (
            len(program.evolution_history) / max_generations * 100
        )
        evolution_progress = f"{evolution_progress:.2f}%"
        if len(idea_evolution) > 0:
            idea_evolution = " -> ".join(
                [f"[{i}] {idea.description}" for i, idea in enumerate(idea_evolution)]
            )
        else:
            idea_evolution = "Initial idea"

        inspiration_str = ""
        for idx in range(len(inspirations)):
            performance_str = format_metrics_safe(inspirations[idx].metrics)
            metadata = inspirations[idx].metadata or {}
            diff_meta = metadata.get("evaluation_feedback") or {}
            difficulty_feedback = diff_meta.get("message") or "N/A"
            validation_meta = metadata.get("developer_validation") or {}
            if validation_meta:
                summary = validation_meta.get("summary")
                conf = validation_meta.get("confidence")
                verdict = validation_meta.get("is_valid")
                validation_line = f"Validation: summary={summary}, verdict={verdict}, confidence={conf}"
                difficulty_feedback = f"{difficulty_feedback}\n{validation_line}"
            seed_flag = metadata.get("is_seed_inspiration")
            meta_tag = f"is_seed_inspiration={seed_flag}" if seed_flag is not None else ""
            idea_line = inspirations[idx].idea
            if meta_tag:
                idea_line = f"{idea_line} ({meta_tag})"
            code_changes = metadata.get("code_changes") or "N/A"
            inspiration_str += INSPIRATION_TEMPLATE.format(
                inspiration_number=idx,
                idea=idea_line,
                performance=performance_str,
                difficulty_feedback=difficulty_feedback,
                code_changes=code_changes,
            )
        if inspiration_str == "":
            inspiration_str = "No prior inspirations."

        evaluation_feedback = "No evaluation feedback recorded."
        if program.metadata:
            eval_meta = program.metadata.get("evaluation_feedback")
            notes = program.metadata.get("verification_notes")
            dev_validation = program.metadata.get("developer_validation")
            parts = []
            if eval_meta:
                message = eval_meta.get("message")
                suggestions = eval_meta.get("suggestions")
                if message:
                    parts.append(f"- Message: {message}")
                if suggestions:
                    parts.append(f"- Suggestions: {suggestions}")
            if notes:
                parts.append(f"- Verification notes: {notes}")
            if dev_validation:
                verdict = dev_validation.get("summary") or dev_validation.get("is_valid")
                confidence = dev_validation.get("confidence")
                parts.append(f"- Developer validation: {verdict} (confidence={confidence})")
                issues = dev_validation.get("issues") or []
                if issues:
                    parts.append(f"- Issues noticed: {issues[:2]}")
            if parts:
                evaluation_feedback = "\n".join(parts)

        if trace_id is None:
            trace_id = gen_trace_id()
        logger.info(f"Starting deep research with trace_id: {trace_id}")

        user_input = USER_TEMPLATE.format(
            query=self.query,
            problem=self.problem_description,
            starting_point=program.idea.description,
            idea_evolution=idea_evolution,
            evolution_progress=evolution_progress,
            inspirations=inspiration_str,
            evaluation_feedback=evaluation_feedback,
        )

        # console.print("[bold blue]User Input of the Researcher Agent[/bold blue]")
        # console.print(user_input)
        # console.print()

        last_input = None
        all_planning_outputs: list[PlanningOutput] = []
        all_search_results = []
        all_reports = []
        current_problem_spec: ProblemSpec | None = None
        metadata_query = self.query or ""
        max_metadata_len = 508
        if len(metadata_query) > max_metadata_len:
            metadata_query = metadata_query[:max_metadata_len - 3] + "..."
        with trace(
            f"DeepEvolve_{self.problem_name}",
            metadata={"query": metadata_query},
            trace_id=trace_id,
            disabled=False,
        ):
            logger.info(f"Performing Deep Research ...")
            for ref_idx in range(max_reflection_times + 1):

                if ref_idx == 0 or last_input is None:
                    planning_output = await self._plan_searches(user_input)
                    all_planning_outputs.append(planning_output)
                    current_problem_spec = planning_output.problem_spec
                    search_plan = planning_output.search_plan
                else:
                    reflection_result = await self._reflection(user_input, last_input)
                    if not isinstance(reflection_result, ReflectionPlan):
                        reflection_result = ReflectionPlan(
                            is_sufficient=True,
                            knowledge_gaps=[],
                            follow_up_queries=[],
                        )
                    if reflection_result.is_sufficient:
                        break
                    else:
                        console.print(
                            f"[bold red]Reflection {ref_idx}: current report is not sufficient because {reflection_result.knowledge_gaps}, generating follow-up queries[/bold red]"
                        )
                        search_plan = WebSearchPlan(
                            searches=reflection_result.follow_up_queries
                        )
                        # 계획 재활용: 기존 problem_spec 유지

                search_results = await self._perform_searches(search_plan)
                all_search_results.append(search_results)
                report_result, last_input = await self._write_report(
                    user_input,
                    search_results,
                    problem_spec=current_problem_spec,
                    last_input=last_input,
                )
                all_reports.append(report_result)

        logger.info("Research completed successfully")
        return all_planning_outputs, all_search_results, all_reports

    async def _plan_searches(self, user_input: str) -> PlanningOutput:
        logger.info(f"Starting search planning for query: {self.query} ...")

        if self.search_time_bias:
            today = datetime.now().strftime("%Y-%m-%d")
            user_input += f"\n*Important: Today's date is {today}. Prioritize recent search results.*\n"

        result = await LCRunner.run(
            self.planner_agent,
            user_input,
        )

        planning_output = result.final_output_as(PlanningOutput)
        if not isinstance(planning_output, PlanningOutput):
            # Fallback: planner returned plain text; wrap into a minimal PlanningOutput
            try:
                planning_output = PlanningOutput.model_validate_json(result.text)
            except Exception:
                planning_output = PlanningOutput(
                    problem_spec=ProblemSpec(
                        topic="",
                        subtopics=[],
                        objectives=[],
                        difficulty_target=None,
                        required_theorems=[],
                        pitfalls=[],
                        constraints=[],
                    ),
                    search_plan=WebSearchPlan(searches=[]),
                )
        logger.info(
            "Completed search planning: %d searches identified with blueprint %s",
            len(planning_output.search_plan.searches),
            planning_output.problem_spec.topic,
        )
        return planning_output

    async def _reflection(self, user_input: str, last_input: list) -> WebSearchPlan:
        new_content = f"""
        Given the following user input, please identify any issues or gaps in the research report:
        {user_input}

        Here are the reflection points you should check about the new idea:
        {REFLECTION_CONTENT_RESEARCH}

        If you think the new idea is good enough, do not ask any follow-up questions. Otherwise, write one or more follow-up queries that include relevant context for further investigation.
        """

        reflection_input = last_input + [{"role": "user", "content": new_content}]
        
        try:
            reflection_plan = await LCRunner.run(
                self.reflection_agent,
                reflection_input,
            )
            plan_obj = reflection_plan.final_output_as(ReflectionPlan)
            if not isinstance(plan_obj, ReflectionPlan):
                try:
                    plan_obj = ReflectionPlan.model_validate_json(reflection_plan.text)
                except Exception:
                    # Even if the model did not return a structured JSON payload,
                    # continue with a safe fallback so downstream code never
                    # receives a raw string.
                    plan_obj = ReflectionPlan(
                        is_sufficient=True,
                        knowledge_gaps=[],
                        follow_up_queries=[],
                    )
            if not isinstance(plan_obj, ReflectionPlan):
                plan_obj = ReflectionPlan(
                    is_sufficient=True,
                    knowledge_gaps=[],
                    follow_up_queries=[],
                )
            return plan_obj

        except Exception as e:
            console.print(f"[bold red]Error in reflection: {e}[/bold red]")
            console.print(f"[bold red]Reflection input: {reflection_input}[/bold red]")
            raise e
        
    async def _perform_searches(self, search_plan: WebSearchPlan) -> list[str]:
        with custom_span("Search the web"):
            logger.info(
                f"Starting web searches, total: {len(search_plan.searches)} ..."
            )
            num_completed = 0
            tasks = [
                asyncio.create_task(self._search(item, i + 1))
                for i, item in enumerate(search_plan.searches)
            ]
            results = []
            for task in asyncio.as_completed(tasks):
                result = await task
                if result is not None:
                    results.append(result)
                num_completed += 1
            logger.info(
                f"Completed {len(results)}/{len(search_plan.searches)} searches successfully"
            )
            return results

    async def _search(self, item: WebSearchItem, source_id: int) -> str | None:
        input = f"Search term: {item.query}\nReason for searching: {item.reason}"
        try:
            result = await LCRunner.run(
                self.search_agent,
                input,
            )
            return str(result.final_output_as(str))
        except Exception as e:
            logger.exception(
                "Web search failed (source_id=%s, query=%s): %s",
                source_id,
                item.query,
                e,
            )
            return None

    async def _write_report(
        self,
        user_input: str,
        search_results: list[str],
        problem_spec: ProblemSpec | None = None,
        last_input: list = None,
    ) -> ReportData:
        logger.info("Starting report writing ...")

        summaries_block = "\n\n---\n\n".join(search_results)
        spec_json = ""
        if problem_spec is not None:
            spec_json = problem_spec.model_dump_json(
                indent=2,
                exclude_none=True,
            )

        if last_input is not None:
            new_content = f"""
            Please review and reflect on the report and the new idea based on below reflection points:
            {REFLECTION_CONTENT_RESEARCH}

            and more search results on these reflection points:
            {summaries_block}
            {spec_json if spec_json else ""}
            
            You can revise the current idea, add new ones, or select a different top idea.
            Important: Edit only within the existing report. Keep its full structure and format unchanged.
            Do not add introductory phrases like "In reviewing the report and the proposed idea, several reflections arise..."
            Retain every detail; focus on strengthening the report, not generating a new report or a reflection document.
            """
            user_input = last_input + [{"content": new_content, "role": "user"}]
        else:
            if spec_json:
                user_input += f"\n\n## Planner Blueprint\n{spec_json}"
            user_input += f"\n\n## Search results\n{summaries_block}"

        result = await LCRunner.run(
            self.writer_agent,
            user_input,
        )

        report_obj = result.final_output_as(ReportData)
        # If the model wrapped JSON inside a string (e.g., markdown_report contains another JSON),
        # try to unwrap and re-parse before falling back to reformat retries.
        if not isinstance(report_obj, ReportData):
            raw_text = result.text or ""
            candidate = None
            try:
                obj = json.loads(raw_text)
                if isinstance(obj, dict):
                    candidate = obj
                elif isinstance(obj, str) and obj.strip().startswith("{"):
                    candidate = json.loads(obj)
            except Exception:
                candidate = None
            if candidate is None:
                # Maybe markdown_report itself is a JSON string
                try:
                    wrapped = json.loads(raw_text)
                    if isinstance(wrapped, dict) and isinstance(wrapped.get("markdown_report"), str):
                        inner = wrapped["markdown_report"]
                        if inner.strip().startswith("{"):
                            candidate = json.loads(inner)
                except Exception:
                    candidate = None
            if isinstance(candidate, dict):
                try:
                    report_obj = ReportData.model_validate(candidate)
                except Exception:
                    # At least keep the markdown_report content
                    report_obj = ReportData(markdown_report=str(candidate), idea=None)

        # If writer failed to produce structured JSON (or missing idea), attempt one reformat retry.
        needs_retry = not isinstance(report_obj, ReportData) or report_obj.idea is None
        if needs_retry:
            logger.warning("Writer returned unstructured report or missing idea; retrying JSON reformat.")
            reformat_prompt = """
The previous response was not valid JSON with all required fields.
Rewrite strictly as JSON (no code fences) with keys:
{
  "markdown_report": "...",
  "idea": {
    "description": "...",
    "motivation": "...",
    "implementation_notes": "...",
    "pseudocode": "...",
    "originality": {"score": int, "positive": "...", "negative": "..."},
    "future_potential": {"score": int, "positive": "...", "negative": "..."},
    "code_difficulty": {"score": int, "positive": "...", "negative": "..."},
    "target_difficulty": "..." 
  },
  "related_work": [...],
  "problem_spec": { "topic": "...", "subtopics": [...], "objectives": [...], "difficulty_target": "...", "required_theorems": [...], "pitfalls": [...], "constraints": [...] },
  "theorem_refs": [...],
  "problem_pair": {...},
  "verification_notes": "...",
  "feedback": {...}
}
Use the content below; do not invent new content, and do not leave any field null or missing.
If a field is absent in the text, extract/summarize from the report content to fill it.
Respond with JSON only.
"""
            retry_input = [
                {"role": "user", "content": reformat_prompt + "\n\n" + result.text}
            ]
            retry_result = await LCRunner.run(self.writer_agent, retry_input)
            retry_obj = retry_result.final_output_as(ReportData)
            if not isinstance(retry_obj, ReportData) or retry_obj.idea is None:
                # Final attempt: extract required fields from the markdown text itself.
                logger.warning("Reformat retry still missing idea; extracting structured fields from markdown.")
                extract_prompt = """
Parse the report text below and return JSON only (no code fences) with all required fields.
Fill every required field by extracting/summarizing from the text (do not invent unrelated content).
Schema:
{
  "markdown_report": "...",
  "idea": {
    "description": "...",
    "motivation": "...",
    "implementation_notes": "...",
    "pseudocode": "...",
    "originality": {"score": int, "positive": "...", "negative": "..."},
    "future_potential": {"score": int, "positive": "...", "negative": "..."},
    "code_difficulty": {"score": int, "positive": "...", "negative": "..."},
    "target_difficulty": "..."
  },
  "related_work": [...],
  "problem_spec": {...},
  "theorem_refs": [...],
  "problem_pair": {...},
  "verification_notes": "...",
  "feedback": {...}
}
Respond with JSON only.
"""
                extract_input = [
                    {"role": "user", "content": extract_prompt + "\n\n" + result.text}
                ]
                extract_result = await LCRunner.run(self.writer_agent, extract_input)
                extract_obj = extract_result.final_output_as(ReportData)
                if not isinstance(extract_obj, ReportData) or extract_obj.idea is None:
                    # Last-resort: extract IdeaData from the markdown and rebuild ReportData
                    logger.warning("Extraction pass failed; deriving idea from markdown via extractor agent.")
                    extractor_agent = LCAgent(
                        name="Idea Extractor",
                        instructions="""
From the report text, extract the idea fields and return JSON only with:
{
  "description": "...",
  "motivation": "...",
  "implementation_notes": "...",
  "pseudocode": "...",
  "originality": {"score": int, "positive": "...", "negative": "..."},
  "future_potential": {"score": int, "positive": "...", "negative": "..."},
  "code_difficulty": {"score": int, "positive": "...", "negative": "..."},
  "target_difficulty": "..."
}
Use only information present in the report text.
""",
                        model=self.search_agent.model,
                        output_type=IdeaData,
                    )
                    extractor_input = [
                        {"role": "user", "content": extract_prompt + "\n\n" + result.text}
                    ]
                    idea_result = await LCRunner.run(extractor_agent, extractor_input)
                    idea_obj = idea_result.final_output_as(IdeaData)
                    if not isinstance(idea_obj, IdeaData):
                        # Heuristic fallback: construct IdeaData from the existing markdown
                        logger.warning("Extractor agent failed; constructing IdeaData heuristically from report text.")
                        def _eval():
                            return EvaluationData(score=5, positive="Derived from report", negative="Not explicitly scored")
                        text = extract_result.text or result.text
                        paragraphs = [p.strip() for p in (text or "").split("\n") if p.strip()]
                        desc = paragraphs[0][:300] if paragraphs else "Idea derived from report text."
                        motivation = paragraphs[1][:300] if len(paragraphs) > 1 else desc
                        impl = paragraphs[2][:400] if len(paragraphs) > 2 else motivation
                        pseudo = ""
                        if "```" in text:
                            parts = text.split("```")
                            if len(parts) >= 3:
                                pseudo = parts[1][:800]
                        if not pseudo:
                            pseudo = impl
                        idea_obj = IdeaData(
                            description=desc,
                            motivation=motivation,
                            implementation_notes=impl,
                            pseudocode=pseudo,
                            originality=_eval(),
                            future_potential=_eval(),
                            code_difficulty=_eval(),
                            target_difficulty=None,
                        )
                    extract_obj = ReportData(
                        markdown_report=extract_result.text,
                        idea=idea_obj,
                    )
                result = extract_result
                report_obj = extract_obj
            else:
                result = retry_result
                report_obj = retry_obj

        logger.info("Completed report writing")
        return report_obj, result.to_input_list()
