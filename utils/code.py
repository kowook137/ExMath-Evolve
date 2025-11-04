import json
from pathlib import Path
import os
import logging
import shutil

import re
from typing import Dict, List, Optional, Tuple

from rapidfuzz.distance import Levenshtein

from utils.datatypes import ProblemPair

logger = logging.getLogger(__name__)

ANCHOR_START_PREFIX = "### >>> DEEPEVOLVE-BLOCK-START:"
ANCHOR_END_PREFIX = "### <<< DEEPEVOLVE-BLOCK-END"


def _extract_anchor_label(lines: List[str]) -> Optional[str]:
    """Return the label used in the DEEPEVOLVE block start marker, if present."""
    for line in lines:
        if ANCHOR_START_PREFIX in line:
            return line.split(ANCHOR_START_PREFIX, 1)[1].strip()
    return None


def _find_anchor_region(lines: List[str], label: str) -> Optional[Tuple[int, int]]:
    """Locate the start/end indices of the DEEPEVOLVE block with the given label."""
    start_idx = None
    for idx, line in enumerate(lines):
        if ANCHOR_START_PREFIX in line:
            candidate = line.split(ANCHOR_START_PREFIX, 1)[1].strip()
            if candidate == label:
                start_idx = idx
                break
    if start_idx is None:
        return None
    end_idx = None
    for j in range(start_idx + 1, len(lines)):
        if ANCHOR_END_PREFIX in lines[j]:
            end_idx = j
            break
    if end_idx is None:
        return None
    return start_idx, end_idx

def get_files_and_code(
    local_path, online_link, workspace_dir, code_extension=".py"
) -> Tuple[Dict[str, str], str]:
    """
    Get all program files from a directory or a single file path.

    Args:
        local_path: local path to the code
        online_link: online link to the code
        workspace_dir: Directory for outputs
        code_extension: File extension to look for (default: .py)

    Returns:
        A tuple of:
        - dict: {filename (relative): source code}
        - str: concatenated code with filename markers
    """
    if local_path is None and online_link is None:
        logger.error("No local path or online link provided")
        return {}, ""

    if local_path:
        path = Path(local_path)

    elif online_link:
        from git import Repo
        # online should be a github repo url like https://github.com/username/repo_name
        # download the github repo directly to the initial_code folder
        # ask user to confirm the download

        # Ask for user confirmation before downloading
        print(f"About to download repository from: {online_link}")
        confirmation = (
            input("Do you want to proceed with downloading this repository? (y/N): ")
            .strip()
            .lower()
        )

        if confirmation not in ["y", "yes"]:
            logger.info("Repository download cancelled by user")
            return {}, ""

        try:
            # Create seed directory if it doesn't exist
            seed_dir = os.path.join(workspace_dir, "initial_code")
            os.makedirs(seed_dir, exist_ok=True)

            # Create a temporary directory for cloning
            temp_dir = os.path.join(workspace_dir, "temp_clone")
            os.makedirs(temp_dir, exist_ok=True)

            # Extract repo name from URL
            repo_name = online_link.split("/")[-1]
            if repo_name.endswith(".git"):
                repo_name = repo_name[:-4]
            temp_repo_path = os.path.join(temp_dir, repo_name)

            # Clone the repository to temp dir
            if os.path.exists(temp_repo_path):
                shutil.rmtree(temp_repo_path)

            logger.info(f"Cloning repository from {online_link} to temporary location")
            Repo.clone_from(online_link, temp_repo_path)

            # Copy all contents from the temp repo to the seed directory
            for item in os.listdir(temp_repo_path):
                source = os.path.join(temp_repo_path, item)
                dest = os.path.join(seed_dir, item)

                if os.path.isdir(source):
                    if os.path.exists(dest):
                        shutil.rmtree(dest)
                    shutil.copytree(source, dest)
                else:
                    shutil.copy2(source, dest)

            # Clean up temp directory
            shutil.rmtree(temp_dir)

            logger.info(f"Copied repository contents directly to {seed_dir}")
            path = Path(seed_dir)

        except Exception as e:
            logger.error(f"Failed to clone repository: {e}")
            return {}, ""

    # Search for all code files in the path
    code_files = {}
    if path.is_file():
        if path.suffix == code_extension:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                code = f.read()
                code_files[path.name] = code
    elif path.is_dir():
        for file_path in path.glob(f"**/*{code_extension}"):
            if file_path.is_file() and not file_path.name.startswith("."):
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        relative_path = str(file_path.relative_to(path))
                        code_files[relative_path] = f.read()
                except Exception as e:
                    logger.warning(f"Could not read file {file_path}: {e}")

    # Create concatenated code with filename markers
    concatenated_code = "\n\n".join(
        f"# === {filename} ===\n{code}" for filename, code in code_files.items()
    )

    return code_files, concatenated_code


def save_code_to_files(concatenated_code: str, output_dir: str) -> Dict[str, str]:
    """
    Save concatenated code back to individual files based on filename markers.

    Args:
        concatenated_code: String containing code with filename markers
        output_dir: Directory to save the files to

    Returns:
        dict: {filename: file_path} mapping of saved files
    """
    os.makedirs(output_dir, exist_ok=True)

    # Remove Markdown code block markers like ```python and ```
    cleaned_code = re.sub(r"```[\w]*\n", "", concatenated_code)
    cleaned_code = re.sub(r"```", "", cleaned_code)

    # Match all sections of the form "# === filename ===\n<code...>"
    pattern = re.compile(r"# === (.+?) ===\n(.*?)(?=(?:# === .+? ===\n)|\Z)", re.DOTALL)
    matches = pattern.findall(cleaned_code)

    saved_files = {}

    for filename, code_content in matches:
        filename = filename.strip()
        if not filename:
            continue

        file_path = os.path.join(output_dir, filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code_content.lstrip())  # Remove leading whitespace if any
            saved_files[filename] = file_path
            logger.info(f"Saved file: {file_path}")
        except Exception as e:
            logger.error(f"Error saving {filename}: {e}")

    return saved_files


# from https://github.com/codelion/openevolve/blob/main/openevolve/utils/code_utils.py
"""
Utilities for code parsing, diffing, and manipulation
"""

def parse_evolve_blocks(code: str) -> List[Tuple[int, int, str]]:
    """
    Parse evolve blocks from code

    Args:
        code: Source code with evolve blocks

    Returns:
        List of tuples (start_line, end_line, block_content)
    """
    lines = code.split("\n")
    blocks = []

    in_block = False
    start_line = -1
    block_content = []

    for i, line in enumerate(lines):
        if "DEEPEVOLVE-BLOCK-START" in line:
            in_block = True
            start_line = i
            block_content = []
        elif "DEEPEVOLVE-BLOCK-END" in line and in_block:
            in_block = False
            blocks.append((start_line, i, "\n".join(block_content)))
        elif in_block:
            block_content.append(line)

    return blocks


def extract_diffs(diff_text: str) -> List[Tuple[str, str]]:
    """Extract SEARCH/REPLACE diff blocks with tolerant parsing.

    - 허용: 마커 주변 공백, 선택적 코드펜스(```language ... ```), CRLF/LF 차이
    - 형식: <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE
    """
    # 개행 정규화 및 코드펜스 제거
    text = diff_text.replace("\r\n", "\n").replace("\r", "\n")
    # ```python, ``` 등 코드펜스 라인을 제거 (블록 내부 텍스트만 보존)
    text = re.sub(r"^```[\w-]*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n```\s*$", "\n", text, flags=re.MULTILINE)

    # 마커 주변의 선택적 공백 허용
    pattern = re.compile(
        r"<<<<<<<\s*SEARCH\s*\n"  # 시작 마커
        r"(.*?)"                     # SEARCH 본문(탐욕X)
        r"\n?=======\s*\n"          # 구분자
        r"(.*?)"                     # REPLACE 본문(탐욕X)
        r"\n?>>>>>>>\s*REPLACE",     # 종료 마커
        re.DOTALL | re.MULTILINE,
    )
    blocks = pattern.findall(text)
    return [(a.rstrip("\n"), b.rstrip("\n")) for a, b in blocks]

def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _rstrip_lines(lines: List[str]) -> List[str]:
    return [ln.rstrip() for ln in lines]


def _strip_inline_comment(line: str) -> str:
    """
    Remove inline comments (`# ...`) from a single line while being mindful of basic
    string literals so that `#` characters inside quotes are preserved.
    """
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            return line[:idx]
    return line


def _normalize_no_comment(lines: List[str]) -> List[str]:
    """
    Normalize a list of lines by trimming trailing whitespace and stripping
    inline comments. Useful when developer SEARCH blocks omit trailing comments.
    """
    return [_strip_inline_comment(ln).rstrip() for ln in lines]


def apply_diff(original_code: str, diff_text: str) -> str:
    """
    Apply a diff to the original code

    Args:
        original_code: Original source code
        diff_text: Diff in the SEARCH/REPLACE format

    Returns:
        Modified code
    """
    # Normalize newlines to be robust to CRLF vs LF
    original_code = _normalize_newlines(original_code)
    diff_text = _normalize_newlines(diff_text)

    # Split into lines for easier processing
    original_lines = original_code.split("\n")
    result_lines = original_lines.copy()

    # Extract diff blocks
    diff_blocks = extract_diffs(diff_text)

    # Apply each diff block
    for search_text, replace_text in diff_blocks:
        search_lines = search_text.split("\n")
        replace_lines = replace_text.split("\n")

        # Right-strip lines to ignore trailing whitespace differences
        search_norm = _rstrip_lines(search_lines)
        search_no_comment = _normalize_no_comment(search_lines)

        matched = False

        # Anchor-based replacement using DEEPEVOLVE block markers (if available)
        anchor_label = _extract_anchor_label(search_lines)
        if anchor_label:
            anchor_region = _find_anchor_region(result_lines, anchor_label)
            if anchor_region is not None:
                start_idx, end_idx = anchor_region
                result_lines[start_idx : end_idx + 1] = replace_lines
                matched = True

        for i in range(len(result_lines) - len(search_lines) + 1):
            window_norm = _rstrip_lines(result_lines[i : i + len(search_lines)])
            if window_norm == search_norm:
                # Replace the matched section
                result_lines[i : i + len(search_lines)] = replace_lines
                matched = True
                break
        if not matched and search_lines:
            for i in range(len(result_lines) - len(search_lines) + 1):
                window_no_comment = _normalize_no_comment(
                    result_lines[i : i + len(search_lines)]
                )
                if window_no_comment == search_no_comment:
                    result_lines[i : i + len(search_lines)] = replace_lines
                    matched = True
                    break
        if not matched:
            # Fallback 1: exact string replace on the whole code snapshot
            original_text = "\n".join(result_lines)
            search_text_exact = "\n".join(search_lines)
            replace_text = "\n".join(replace_lines)
            if search_text_exact and search_text_exact in original_text:
                original_text = original_text.replace(search_text_exact, replace_text, 1)
                result_lines = original_text.split("\n")
                matched = True
            else:
                # Fallback 2: strip-trimmed string replace
                normalized_original = "\n".join(_rstrip_lines(result_lines))
                normalized_search = "\n".join(search_norm)
                normalized_replace = "\n".join(_rstrip_lines(replace_lines))
                if normalized_search and normalized_search in normalized_original:
                    normalized_original = normalized_original.replace(
                        normalized_search,
                        normalized_replace,
                        1,
                    )
                    result_lines = normalized_original.split("\n")
                    matched = True

        if not matched and search_lines:
            # Fuzzy window search to tolerate minor structural drift
            search_text_exact = "\n".join(search_lines)
            best_score = -1.0
            best_range: Optional[Tuple[int, int]] = None
            min_len = max(1, len(search_lines) - 2)
            max_len = min(len(result_lines), len(search_lines) + 2)
            for window_len in range(min_len, max_len + 1):
                for i in range(len(result_lines) - window_len + 1):
                    window_text = "\n".join(result_lines[i : i + window_len])
                    score = 1.0 - Levenshtein.normalized_distance(
                        search_text_exact, window_text
                    )
                    if score > best_score:
                        best_score = score
                        best_range = (i, i + window_len)
            if best_range is not None and best_score >= 0.80:
                start_idx, end_idx = best_range
                result_lines[start_idx:end_idx] = replace_lines
                matched = True

        if not matched and search_lines:
            first_nonempty = next((ln for ln in search_lines if ln.strip()), "")
            stripped = first_nonempty.strip()
            if stripped.startswith("def ") or stripped.startswith("class "):
                signature = stripped
                sig_idx = None
                for idx, line in enumerate(result_lines):
                    if line.strip() == signature:
                        sig_idx = idx
                        break
                if sig_idx is not None:
                    indent = len(result_lines[sig_idx]) - len(result_lines[sig_idx].lstrip())
                    end_idx = sig_idx + 1
                    while end_idx < len(result_lines):
                        line = result_lines[end_idx]
                        stripped_line = line.strip()
                        if stripped_line == "":
                            end_idx += 1
                            continue
                        curr_indent = len(line) - len(line.lstrip())
                        if curr_indent <= indent and not stripped_line.startswith("@"):
                            break
                        end_idx += 1
                    result_lines[sig_idx:end_idx] = replace_lines
                    matched = True

        if not matched:
            replace_norm = _rstrip_lines(replace_lines)
            current_norm = _rstrip_lines(result_lines)
            replace_text = "\n".join(replace_norm).strip()
            if replace_text and replace_text in "\n".join(current_norm):
                matched = True

        if not matched:
            try:
                first = search_lines[0] if search_lines else ""
                logger.warning(
                    "apply_diff: SEARCH 블록을 찾지 못해 적용 실패 (첫 줄 미리보기): %r",
                    first,
                )
            except Exception:
                pass

    result_code = "\n".join(result_lines)
    if any(marker in result_code for marker in ("<<<<<<<", "=======", ">>>>>>>")):
        logger.warning("apply_diff: conflict markers detected after applying diff; reverting to original code.")
        return original_code
    return result_code


def normalize_math_text(text: str) -> str:
    """
    Normalize mathematical text for robust comparisons.

    Args:
        text: Raw math text.

    Returns:
        Normalized string with trimmed whitespace and lowercase letters.
    """
    return re.sub(r"\s+", " ", text.strip().lower())


def compute_text_similarity(a: str, b: str) -> float:
    """
    Compute a similarity score between two strings using normalized Levenshtein distance.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Similarity in [0, 1], where 1 indicates identical text.
    """
    if not a or not b:
        return 0.0
    return 1.0 - Levenshtein.normalized_distance(a, b)


def save_problem_pair(problem: ProblemPair, output_dir: str) -> str:
    """
    Persist a ProblemPair to disk as JSON.

    Args:
        problem: ProblemPair instance to serialize.
        output_dir: Directory where the JSON file will be written.

    Returns:
        Path to the saved JSON file.
    """
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, f"{problem.id}.json")
    with open(file_path, "w", encoding="utf-8") as fp:
        json.dump(problem.model_dump(mode="json"), fp, ensure_ascii=False, indent=2)
    logger.info(f"Saved problem pair to {file_path}")
    return file_path


def load_problem_pair(file_path: str) -> ProblemPair:
    """
    Load a ProblemPair JSON from disk.

    Args:
        file_path: Path to the JSON file.

    Returns:
        ProblemPair instance.
    """
    with open(file_path, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    return ProblemPair(**data)


def ensure_problem_id(problem: ProblemPair, prefix: Optional[str] = None) -> ProblemPair:
    """
    Ensure that a ProblemPair has a unique identifier.

    Args:
        problem: ProblemPair potentially lacking an ID.
        prefix: Optional prefix for the identifier.

    Returns:
        ProblemPair with ID populated.
    """
    if problem.id:
        return problem

    base = prefix or "problem"
    from uuid import uuid4

    problem.id = f"{base}_{uuid4().hex[:12]}"
    return problem
