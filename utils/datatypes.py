from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

# Used in Researcher

reasoning_models = ["o4-mini", "o3-mini", "o1-mini", "o1", "o3", "o1-pro"]

class ResearchWork(BaseModel):
    title: str
    "The title of the research paper."

    link: str
    "The link to the research paper."

    contributions: list[str]
    "A list of contributions of the research paper."

    limitations: list[str]
    "A list of limitations of the research paper."


class EvaluationData(BaseModel):
    score: int
    "The score of the idea between 0 and 10. Higher is better."

    positive: str
    "A positive reason for the evaluation."

    negative: str
    "A negative reason for the evaluation."


class IdeaData(BaseModel):
    description: str
    "One or two sentences describing the new idea including (1) the problem the idea solves, (2) how the idea solves it, and (3) what makes the idea new."

    motivation: str
    "The motivation for the new idea on why it is different from existing methods and why it can improve the existing methods for the target problem."

    implementation_notes: str
    "Notes on how to implement the new idea (e.g. pseudocode, logic, etc.)."

    pseudocode: str
    "A pseudocode implementation of the new idea if available."

    originality: EvaluationData
    "Self-assessment of the originality of the new idea."

    future_potential: EvaluationData
    "Self-assessment of the future potential of the new idea."

    code_difficulty: EvaluationData
    "Self-assessment of the difficulty of implementing the new idea."

    target_difficulty: Optional[str] = None
    "Intended difficulty label for the generated content (e.g., undergraduate, graduate, olympiad)."

    required_theorems: Optional[List[str]] = None
    "Key theorems or lemmas the generated content should leverage."

    pitfalls: Optional[List[str]] = None
    "Common mistakes or traps that should appear in the generated content."


class ReportData(BaseModel):
    markdown_report: str 
    """The final report"""

    idea: IdeaData 
    """The new idea from the research report."""

    related_work: list[ResearchWork] 
    """A list of existing research works that are relevant to the query."""

    problem_spec: Optional[ProblemSpec] = None
    """Structured plan for the generated problem."""

    theorem_refs: List[TheoremRef] = Field(default_factory=list)
    """Referenced theorems the problem should leverage."""

    problem_pair: Optional[ProblemPair] = None
    """Draft of the generated problem and solution."""

    verification_notes: Optional[str] = None
    """Guidance for automated verification."""

    feedback: Optional[FeedbackBundle] = None
    """Feedback to upstream agents (e.g., planner) about difficulty or issues."""

class WebSearchItem(BaseModel):
    reason: str
    "Your reasoning for why this search is important to the query."

    query: str
    "The search term to use for the web search."


class WebSearchPlan(BaseModel):
    searches: list[WebSearchItem]
    """A list of web searches to perform to best answer the query."""


class ReflectionPlan(BaseModel):
    is_sufficient: bool
    "Whether the report is sufficient to answer the query."

    knowledge_gaps: list[str]
    "The information that the report lacks. If is_sufficient is true, this should be empty."

    follow_up_queries: list[WebSearchItem]
    "A list of follow-up queries to perform to best answer the query. If is_sufficient is true, this should be empty."


class PlanningOutput(BaseModel):
    problem_spec: ProblemSpec
    """Blueprint for the upcoming problem generation step."""

    search_plan: WebSearchPlan
    """Concrete search queries for the searcher agent."""


class TheoremRef(BaseModel):
    name: str
    "Human-readable name of the theorem or lemma."

    statement: str
    "Formal or informal statement of the theorem that will be cited."

    source: str
    "Reference to the source (paper, book, url, etc.)."

    notes: Optional[str] = None
    "Additional context on how the theorem will be used."


class ProblemSpec(BaseModel):
    topic: str
    "Primary mathematical topic for the generated problem (e.g., number theory)."

    subtopics: List[str] = Field(default_factory=list)
    "Subtopics or techniques that should appear."

    objectives: List[str] = Field(default_factory=list)
    "Learning or assessment objectives for the problem."

    difficulty_target: Optional[str] = None
    "Target difficulty label (e.g., advanced undergraduate, graduate qualifying)."

    required_theorems: List[str] = Field(default_factory=list)
    "Names of theorems or lemmas that ought to be incorporated."

    pitfalls: List[str] = Field(default_factory=list)
    "Misconceptions or traps to test the solver."

    constraints: List[str] = Field(default_factory=list)
    "Hard constraints that the problem must satisfy (e.g., integer solutions, no calculus)."


class VerificationReport(BaseModel):
    substitution_pass: bool
    "True if plugging the provided solution into the problem validates correctly."

    symbolic_pass: bool
    "True if symbolic/algebraic verification succeeds."

    counterexample_found: bool
    "True if a counterexample disproving the solution was found."

    notes: Optional[str] = None
    "Free-form notes from the verifier."

    extra_data: Dict[str, str] = Field(default_factory=dict)
    "Additional structured data (e.g., sample evaluations, residuals)."


class ProblemPair(BaseModel):
    id: str
    "Unique identifier for the generated problem."

    problem_text: str
    "Natural language description of the problem."

    solution_text: str
    "Detailed solution or answer explanation."

    tags: List[str] = Field(default_factory=list)
    "Topic tags for categorisation."

    prerequisites: List[str] = Field(default_factory=list)
    "Concepts a solver should know before attempting."

    theorem_refs: List[TheoremRef] = Field(default_factory=list)
    "Referenced theorems with statements and sources."

    verification: Optional[VerificationReport] = None
    "Outcome of automated verification checks."

    difficulty_estimate_author: Optional[int] = None
    "Self-assessed difficulty on a coarse ordinal scale."

    metadata: Dict[str, str] = Field(default_factory=dict)
    "Additional metadata such as curriculum week, variant, etc."


class EvalRecord(BaseModel):
    llm_model: str
    "Model identifier used for evaluation."

    prompt_style: str
    "Template or instructions provided to the evaluator model."

    temperature: float
    "Sampling temperature used during evaluation."

    llm_solved: bool
    "True if the evaluator model produced a correct solution."

    llm_score: float
    "Fine-grained score in [0,1] comparing the LLM answer to the reference solution."

    rationale_quality: Optional[float] = None
    "Optional qualitative score assessing reasoning quality."

    tokens_used: Optional[int] = None
    "Total tokens consumed during evaluation."

    attempts: int = 1
    "Number of model attempts."

    raw_response: Optional[str] = None
    "Raw text returned by the evaluator model."


class FeedbackBundle(BaseModel):
    message: str
    "Headline feedback for planners/writers."

    suggestions: List[str] = Field(default_factory=list)
    "Actionable improvement suggestions."

    evaluator: Optional[str] = None
    "Source of the feedback (e.g., debugger agent, human reviewer)."
