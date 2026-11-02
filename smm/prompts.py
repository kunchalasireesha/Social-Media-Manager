"""Loading the six system prompts and rendering Prompt 1's dynamic blocks."""

from __future__ import annotations

import re
from pathlib import Path

from .config import PROMPTS_DIR
from .models import DEFAULT_CONTENT_OBJECTIVE

PROMPT_1_PATH = PROMPTS_DIR / "Prompt 1- content DNA intake prompt.md"
PROMPT_2_PATH = PROMPTS_DIR / "instagram-content-intelligence-engine.md"
PROMPT_3_PATH = PROMPTS_DIR / "live-performance-learning-engine.md"
PROMPT_4_PATH = PROMPTS_DIR / "content-opportunity-prioritization-engine.md"
PROMPT_5_PATH = PROMPTS_DIR / "performance-script-execution-engine.md"
PROMPT_6_PATH = PROMPTS_DIR / "Prompt 6 — Viral Script Amplification Engine.docx.md"

INTAKE_START_MARKER = "<!-- CONTENT_DNA_INTAKE_START -->"
INTAKE_END_MARKER = "<!-- CONTENT_DNA_INTAKE_END -->"
OBJECTIVE_START_MARKER = "<!-- CONTENT_OBJECTIVE_START -->"
OBJECTIVE_END_MARKER = "<!-- CONTENT_OBJECTIVE_END -->"

_FINAL_PROMPT_MARKERS = [
    "## **Final Prompt — Copy and Paste This Exactly**",
    "3) ## **Final prompt**",
    "## **Final prompt**",
]
_PROMPT_6_SEPARATOR = "─────────────────────────────────────────────────────────"


def load_prompt(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def extract_final_prompt_text(text: str) -> str:
    """Return only the executable system prompt from a prompt markdown document."""
    start = -1
    for marker in _FINAL_PROMPT_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            start = idx + len(marker)
            break

    if start == -1:
        you_are = text.find("You are the")
        return text[you_are:].strip() if you_are != -1 else text.strip()

    lines = text[start:].lstrip("\n").splitlines()
    while lines and (not lines[0].strip() or set(lines[0].strip()) <= {"-", "─", "—", "=", "*"}):
        lines.pop(0)
    body = "\n".join(lines).strip()

    # Prompt 6 wraps the copy/paste block between long separators
    if _PROMPT_6_SEPARATOR in body:
        if body.startswith(_PROMPT_6_SEPARATOR):
            body = body[len(_PROMPT_6_SEPARATOR):].lstrip("\n")
        close_idx = body.find(_PROMPT_6_SEPARATOR)
        if close_idx != -1:
            body = body[:close_idx].rstrip()

    you_are = body.find("You are the")
    if you_are > 0:
        body = body[you_are:]
    return body.strip()


def extract_final_prompt(path: Path) -> str:
    return extract_final_prompt_text(load_prompt(path))


def _replace_block(text: str, start: str, end: str, content: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    new_text, count = pattern.subn(lambda _m: f"{start}\n{content}\n{end}", text)
    if count != 1:
        raise ValueError(f"Expected exactly one {start} … {end} block in Prompt 1, found {count}.")
    return new_text


def render_prompt_1(content_dna_intake: str, content_objective: str | None) -> str:
    """Fill Prompt 1's intake + objective blocks in memory (the file on disk stays a template)."""
    text = load_prompt(PROMPT_1_PATH)
    text = _replace_block(text, INTAKE_START_MARKER, INTAKE_END_MARKER, content_dna_intake)
    objective = (content_objective or "").strip() or DEFAULT_CONTENT_OBJECTIVE
    text = _replace_block(
        text, OBJECTIVE_START_MARKER, OBJECTIVE_END_MARKER, f"Content Objective:\n{objective}"
    )
    return extract_final_prompt_text(text)
