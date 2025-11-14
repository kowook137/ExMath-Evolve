from __future__ import annotations

import asyncio
import logging
from rich.console import Console
from datetime import datetime

from agents import Agent, WebSearchTool, Runner
from agents.agent_output import AgentOutputSchema
from agents.tracing import gen_trace_id, trace, custom_span
from agents.model_settings import ModelSettings

from database import Program
from utils.datatypes import (
    ReportData,
    IdeaData,
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

INSPIRATION_TEMPLATE = """### Inspiration {inspiration_number}
- Research Idea : {idea}
- Performance: {performance}
- Difficulty feedback: {difficulty_feedback}
"""

PLANNER_INSTRUCTIONS = """You are the lead architect of a math-problem generation pipeline.

You will receive:
 - A target topic and history of previous problem-generation attempts
 - Inspirations from earlier problems (with metrics indicating whether LLMs solved them)
 - Feedback about which tricks are overused or too easy

Your job is to design how the next hard problem should be constructed. Decide:
 - Which mathematical areas, subtopics, or theorems should be fused
 - Whether to remix existing problems or engineer a new scaffold from scratch
 - What pitfalls, invariants, or constraints must be embedded so rote templates fail
 - Any explicit conditions on the final statement or solution (proof style, integer-only, bounds, etc.)

Output 5–10 search queries for the Searcher. For each query, add a short note stating:
 - Which theorem, lemma, or exemplar problem it should retrieve (graduate/advanced undergraduate level)
 - How the result will validate or stress-test the proposed construction
 - What parameters or edge cases must be checked before committing to the design

Return valid JSON with the following structure:
{
  "problem_spec": {
    "topic": "...",
    "subtopics": [...],
    "objectives": [...],
    "difficulty_target": "...",
    "required_theorems": [...],
    "pitfalls": [...],
    "constraints": [...]
  },
  "search_plan": {
    "searches": [
      {"reason": "...", "query": "..."},
      ...
    ]
  }
}
"""

REFLECTION_INSTRUCTIONS = """
You are an expert research assistant. You will receive a research report (in Markdown) and a newly proposed idea for that report's research problem. Your job is to identify any gaps or issues—such as missing details, logical flaws, or questionable evaluations of novelty, impact, or implementation difficulty.  

- If the report and idea contain all necessary information, do not generate any follow-up questions.  
- If you detect a knowledge gap or something that needs deeper exploration, generate one or more self-contained follow-up queries. Each query must include enough context so that a web search could answer it, For each query, give a short note explaining why you use the query and what you hope it will reveal.
- Focus on technical details, implementation specifics, and any emerging methods or references that were overlooked.  
- Use clear, direct language and avoid unnecessary jargon.  

"""

SEARCH_INSTRUCTIONS = (
    "You are an expert mathematical librarian. Given a search term from the planner, locate graduate- "
    "or research-level references (textbook chapters, competition archives, arXiv papers) describing "
    "advanced problems or theorems that match the request. Summarise in 2-3 concise paragraphs (<=300 "
    "words) the key statements, hypotheses, typical proof strategies, tricky parameter regimes, and known "
    "variants. Emphasise subtle constraints or edge cases that could be woven into a new problem. "
    "Avoid narrative fluff—deliver dense, technically precise summaries the writer can rely on."
)

WRITER_INSTRUCTIONS = """You are the lead author crafting a rigorous mathematics problem and its official solution. You will receive:
- The planner’s blueprint describing required topics, theorems, and pitfalls
- Searcher summaries of advanced references and exemplar problems
- Inspirations from prior iterations plus any feedback about LLM difficulty

Deliver a complete Problem–Solution pair suitable for an LLM-resistance benchmark.

1. **Frame the objective**
   - Summarise the targeted theorems, invariants, and constraints that must appear.
   - Note prohibited shortcuts or degenerate parameter choices the solver must not exploit.

2. **Draft the problem statement**
   - Use precise mathematical language with explicit assumptions.
   - Incorporate the mandated twists so the task resists rote template application.
   - Ensure the question culminates in a verifiable answer (numeric, algebraic, proof statement).

3. **Write the solution**
   - Give a step-by-step derivation referencing the required theorems.
   - Justify each inference and explain why alternate naive approaches fail.
   - Present the final answer in canonical form and restate the key conclusion.

4. **Verification guidance**
   - Describe how to confirm correctness (symbolic substitution, boundary checks, counterexample search).
   - Highlight edge cases the automated verifier must test.

Keep the exposition concise but rigorous. The downstream developer will convert your description into executable validation code, so favour clear structure and explicit reasoning over prose flourishes.

Return valid JSON with fields:
{
  "markdown_report": "...",
  "idea": {...},
  "related_work": [...],
  "problem_spec": {...},
  "theorem_refs": [...],
  "problem_pair": {...},
  "verification_notes": "...",
  "feedback": {...}
}
"""

# Olympiad-style transformation constraints (non-breaking extension):
# We keep the JSON output schema (ReportData) unchanged, but strengthen how the
# problem is constructed. These constraints should be applied when drafting
# problem_pair.problem_text / solution_text. Do NOT change the return format.
WRITER_INSTRUCTIONS += """

Additional Olympiad-style transformation constraints (apply when suitable):
- Target difficulty: challenging Olympiad (IMO shortlist level) while remaining machine-verifiable and bounded in runtime.
- Deep synthesis: the solution must critically depend on the interplay between a central theorem and a supporting concept/tool, in a way that feels integral to the problem (no superficial use).
- Disguised theorem: do not name the central theorem explicitly in the problem text; instead, implicitly require the idea. You may record the theorem name and source in `theorem_refs` for metadata.
- Multi-step reasoning: require at least 2–3 non-trivial intermediate steps/lemmas that logically connect setup → key lemmas → theorem application → final conclusion.
- Generalization/abstraction when appropriate: consider parameters (instead of fixed small numbers) to avoid rote patterns and increase conceptual challenge, but ensure verification remains deterministic.
- Single final answer: design the task so the final answer is a single integer. If the natural product is a construction, add a final integer quantity to compute (e.g., count, index, minimal value, unique parameter).
- Verification: include a concrete, deterministic verification plan and, where possible, machine-checkable tests in `verification_notes` (e.g., substitution ranges, equivalence checks, boundary cases). Do not leak the full solution in the problem text.
"""


USER_TEMPLATE = """
## User Query
{query}

## Research Problem
{problem}

## Starting Research Idea
{starting_point}

## Idea Evolution History
{idea_evolution}

## Research Progress
{evolution_progress}

## Previous Inspirations
{inspirations}

## Latest Evaluation Feedback
{evaluation_feedback}
"""

PAPER_READER_INSTRUCTIONS = """
You are a paper reader. You will be provided with a title of the idea with the content.

If the content is an online link, your task is to search the paper online and summarize the core ideas of the paper.

If the content is the description of the idea, your task is to read the description and summarize the core ideas of the idea.

You may be provided supplmentary information about the idea, such as the code, the implementation notes, the pseudocode, etc.
"""

REFLECTION_CONTENT = """
- Should we consider other ideas in the report or a totally new idea?
- Are the ratings for originality, future potential, and code difficulty accurate?
- Are there any logical inconsistencies or gaps in the methodology?
- Are any implementation steps or references missing?
- Is every step described clearly enough to reproduce results?
- Does the idea suffer from overfitting or shortcut learning?
- Are there any other issues you think are important about the new idea?
"""


class ResearcherAgent:
    def __init__(
        self,
        planner: str = "o3-mini",
        searcher: str = "gpt-4o",
        writer: str = "o3-mini",
        reasoning_effort: str = 'medium',
    ):
        self.planner_agent = Agent(
            name="Planner Agent",
            instructions=PLANNER_INSTRUCTIONS,
            model=planner,
            output_type=AgentOutputSchema(PlanningOutput, strict_json_schema=False),
            model_settings=ModelSettings(reasoning={'effort': reasoning_effort}) if planner in reasoning_models else ModelSettings(),
        )
        self.reflection_agent = Agent(
            name="Reflection Agent",
            instructions=REFLECTION_INSTRUCTIONS,
            model=planner,
            output_type=AgentOutputSchema(ReflectionPlan, strict_json_schema=False),
            model_settings=ModelSettings(reasoning={'effort': reasoning_effort}) if planner in reasoning_models else ModelSettings(),
        )
        self.search_agent = Agent(
            name="Search Agent",
            instructions=SEARCH_INSTRUCTIONS,
            tools=[WebSearchTool()],
            model=searcher,
            model_settings=ModelSettings(reasoning={'effort': reasoning_effort}, tool_choice="required") if searcher in reasoning_models else ModelSettings(tool_choice="required"),
        )
        self.writer_agent = Agent(
            name="Writing Agent",
            instructions=WRITER_INSTRUCTIONS,
            model=writer,
            output_type=AgentOutputSchema(ReportData, strict_json_schema=False),
            model_settings=ModelSettings(reasoning={'effort': reasoning_effort}) if writer in reasoning_models else ModelSettings(),
        )
        self.reader_agent = Agent(
            name="Paper Reader Agent",
            instructions=PAPER_READER_INSTRUCTIONS,
            tools=[WebSearchTool()],
            model=searcher,
            output_type=AgentOutputSchema(IdeaData, strict_json_schema=False),
            model_settings=ModelSettings(reasoning={'effort': reasoning_effort}) if searcher in reasoning_models else ModelSettings(),
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
        result = await Runner.run(
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
            inspiration_str += INSPIRATION_TEMPLATE.format(
                inspiration_number=idx,
                idea=inspirations[idx].idea,
                performance=performance_str,
                difficulty_feedback=difficulty_feedback,
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

        result = await Runner.run(
            self.planner_agent,
            user_input,
        )

        planning_output = result.final_output_as(PlanningOutput)
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
        {REFLECTION_CONTENT}

        If you think the new idea is good enough, do not ask any follow-up questions. Otherwise, write one or more follow-up queries that include relevant context for further investigation.
        """

        reflection_input = last_input + [{"role": "user", "content": new_content}]
        
        try:
            reflection_plan = await Runner.run(
            self.reflection_agent,
                reflection_input,
            )
            return reflection_plan.final_output_as(ReflectionPlan)

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
            result = await Runner.run(
                self.search_agent,
                input,
            )
            return str(result.final_output)
        except Exception:
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
            {REFLECTION_CONTENT}

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

        result = await Runner.run(
            self.writer_agent,
            user_input,
        )
        
        logger.info("Completed report writing")
        return result.final_output_as(ReportData), result.to_input_list()
