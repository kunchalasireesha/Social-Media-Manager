"""The eight LangGraph nodes. Each takes the shared state and returns a partial update."""

from __future__ import annotations

import re
import time
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig

from .config import Settings, logger
from .context import (
    build_content_dna_intake,
    build_dna_bundle,
    build_performance_data,
    format_prior_context,
)
from .llm import invoke_prompt, truncate
from .models import DEFAULT_CONTENT_OBJECTIVE, GraphState, insta_profile, profile_metadata, smm_metadata
from .prompts import (
    PROMPT_2_PATH,
    PROMPT_3_PATH,
    PROMPT_4_PATH,
    PROMPT_5_PATH,
    PROMPT_6_PATH,
    extract_final_prompt,
    render_prompt_1,
)
from .scraper import get_insta_profile, reset_role_dir, scrape_profile_reels


def _runtime(config: RunnableConfig) -> tuple[Settings, Any]:
    conf = (config or {}).get("configurable", {})
    return conf["settings"], conf["llm"]


def log_stage(stage_name: str) -> Callable:
    """Decorator that logs when a graph stage starts and ends (with duration)."""

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(state: dict, config: RunnableConfig):
            logger.info("[%s] STARTED", stage_name)
            t0 = time.perf_counter()
            try:
                result = fn(state, config)
            except Exception:
                logger.exception("[%s] FAILED after %.2fs", stage_name, time.perf_counter() - t0)
                raise
            logger.info("[%s] ENDED (duration: %.2fs)", stage_name, time.perf_counter() - t0)
            return result

        return wrapper

    return decorator


# --- Stage 1 + 2: data ------------------------------------------------------------

def parse_input_text(llm: Any, content: str) -> smm_metadata:
    """Use structured output to turn free-form input.txt into smm_metadata."""
    return llm.with_structured_output(smm_metadata).invoke(
        "Extract the Instagram profile metadata (subject, competitors, and "
        "important reference accounts, including username, description/bio, "
        "and reel URLs) and the creator's content objective from the following document. "
        "Important reference accounts are ones the creator explicitly wants their content to "
        "look/feel exactly like, as opposed to competitors which are just market parallels. "
        "If important_accounts is missing or empty, return an empty list — do not invent accounts. "
        f"Copy the content objective verbatim; if absent use '{DEFAULT_CONTENT_OBJECTIVE}':\n\n{content}"
    )


@log_stage("Data Extractor")
def data_extractor(state: GraphState, config: RunnableConfig) -> dict:
    """Parse the input (unless already structured) and fetch profiles via Apify."""
    settings, llm = _runtime(config)
    if state.get("metadata"):
        metadata = smm_metadata.model_validate(state["metadata"])
    else:
        metadata = parse_input_text(llm, state.get("input_text") or "")

    subject = get_insta_profile(settings, metadata.subject.username)
    competitors = [get_insta_profile(settings, c.username) for c in metadata.competitors]
    important = [get_insta_profile(settings, a.username) for a in metadata.important_accounts]

    logger.info("[Data Extractor] subject=%s competitors=%d important=%d", subject.username, len(competitors), len(important))
    return {
        "metadata": metadata.model_dump(),
        "content_objective": metadata.content_objective,
        "subject": subject.model_dump(),
        "competitors": [c.model_dump() for c in competitors],
        "important_accounts": [a.model_dump() for a in important],
    }


@log_stage("Account Scraper and Prompt Modifier")
def account_scraper_and_prompt_modifier(state: GraphState, config: RunnableConfig) -> dict:
    """Scrape posts/reels, sync usernames into metadata, and build LLM context."""
    settings, llm = _runtime(config)
    subject = insta_profile.model_validate(state["subject"])
    competitors = [insta_profile.model_validate(c) for c in state.get("competitors", [])]
    important = [insta_profile.model_validate(a) for a in state.get("important_accounts", [])]

    # Reset role folders so stale JSON isn't reused, but keep same-day scrapes we still need.
    reset_role_dir(settings, "subject", {subject.username})
    reset_role_dir(settings, "competitors", {c.username for c in competitors})
    reset_role_dir(settings, "important", {a.username for a in important})

    scrape_profile_reels(settings, subject, "subject")
    for c in competitors:
        scrape_profile_reels(settings, c, "competitors")
    for a in important:
        scrape_profile_reels(settings, a, "important")

    metadata = smm_metadata.model_validate(state.get("metadata")) if state.get("metadata") else smm_metadata(
        subject=profile_metadata(username=subject.username, description=subject.bio), competitors=[]
    )
    metadata.subject.username = subject.username
    if subject.bio:
        metadata.subject.description = subject.bio
    metadata.competitors = [profile_metadata(username=c.username, description=c.bio or "") for c in competitors]
    metadata.important_accounts = [profile_metadata(username=a.username, description=a.bio or "") for a in important]

    content_dna_intake = build_content_dna_intake(settings, llm)
    performance_data = build_performance_data(settings)
    logger.info("[Account Scraper] content DNA intake:\n%s", content_dna_intake)
    logger.info("[Account Scraper] performance_data built (%d chars)", len(performance_data or ""))

    return {
        "metadata": metadata.model_dump(),
        "content_dna_intake": content_dna_intake,
        "dna_bundle": build_dna_bundle(settings),
        "performance_data": performance_data,
    }


# --- Stages 3-7: prompt chain --------------------------------------------------------

@log_stage("Execute Prompt 1")
def execute_prompt_1(state: GraphState, config: RunnableConfig) -> dict:
    """DNA intake — Prompt 1 with intake + objective rendered into the system prompt."""
    settings, llm = _runtime(config)
    system = render_prompt_1(state.get("content_dna_intake", ""), state.get("content_objective"))
    human = (
        "Execute the Content DNA Intake.\n"
        "You cannot read the filesystem — use ONLY the intake block and dna_bundle below.\n"
        "If Important Account usernames are None/empty, leave that field empty. Do not invent accounts.\n"
        "Preserve the Content Objective exactly as written in the system prompt.\n\n"
        f"### content_dna_intake\n{state.get('content_dna_intake', '')}\n\n"
        f"### dna_bundle\n{state.get('dna_bundle', '{}')}"
    )
    return {"prompt_1_result": invoke_prompt(settings, llm, system, human)}


@log_stage("Execute Prompt 2")
def execute_prompt_2(state: GraphState, config: RunnableConfig) -> dict:
    """Instagram content intelligence engine."""
    settings, llm = _runtime(config)
    human = format_prior_context(settings, state, ["content_dna_intake", "prompt_1_result"])
    human += (
        "\n\nProduce evidence-backed Instagram content intelligence from the dna_bundle "
        "and prior intake. If important accounts are empty, analyze subject + competitors only."
    )
    return {"prompt_2_result": invoke_prompt(settings, llm, extract_final_prompt(PROMPT_2_PATH), human)}


@log_stage("Execute Prompt 3")
def execute_prompt_3(state: GraphState, config: RunnableConfig) -> dict:
    """Live performance learning from scraped engagement metrics."""
    settings, llm = _runtime(config)
    performance_data = (state.get("performance_data") or "").strip()
    if not performance_data:
        raise ValueError("performance_data is empty — no posts were scraped, so Prompt 3 has nothing to learn from.")
    human = format_prior_context(settings, state, ["content_dna_intake", "prompt_1_result", "prompt_2_result"])
    human += (
        f"\n\n### performance_data\n{truncate(performance_data, settings.max_prior_chars)}\n\n"
        "Treat the metrics above as live post-performance evidence. "
        "They are public scrape metrics (likes/comments/views/plays), not private Insights. "
        "Do not invent saves, reach, or other unavailable fields."
    )
    return {"prompt_3_result": invoke_prompt(settings, llm, extract_final_prompt(PROMPT_3_PATH), human)}


@log_stage("Execute Prompt 4")
def execute_prompt_4(state: GraphState, config: RunnableConfig) -> dict:
    """Content opportunity prioritization engine."""
    settings, llm = _runtime(config)
    human = format_prior_context(
        settings, state, ["content_dna_intake", "prompt_1_result", "prompt_2_result", "prompt_3_result"]
    )
    human += (
        "\n\nUse Prompt 3 performance learnings together with Prompt 1 + Prompt 2. "
        "Do not invent metrics beyond the provided performance_data. "
        "HARD REQUIREMENT: rank exactly 20 opportunities with exactly 10 Reel formats "
        "and exactly 10 Carousel or comparison-carousel formats. Equal mix is mandatory. "
        "Label format explicitly on every ranked opportunity."
    )
    return {"prompt_4_result": invoke_prompt(settings, llm, extract_final_prompt(PROMPT_4_PATH), human)}


@log_stage("Execute Prompt 5")
def execute_prompt_5(state: GraphState, config: RunnableConfig) -> dict:
    """Performance script execution engine."""
    settings, llm = _runtime(config)
    human = format_prior_context(
        settings,
        state,
        ["content_dna_intake", "prompt_1_result", "prompt_2_result", "prompt_3_result", "prompt_4_result"],
        include_dna=False,
    )
    human += (
        "\n\nHARD REQUIREMENT: Preserve Prompt 4's equal mix (10 Reel + 10 Carousel). "
        "For every carousel or comparison-carousel priority, produce a full slide-by-slide "
        "carousel script. Do not convert carousel priorities into reels. "
        "Include a recommended upload time (IST) for each script."
    )
    return {"prompt_5_result": invoke_prompt(settings, llm, extract_final_prompt(PROMPT_5_PATH), human)}


# --- Stage 8: final script pack + completeness loop -------------------------------------

def count_scripts(text: str) -> int:
    # Matches "Script 1", "SCRIPT #1", "### 🎬 SCRIPT #12 – REEL", "**Script 3**" ...
    return len(re.findall(r"(?im)^(?:#{1,4}\s*)?(?:\*{0,2})?(?:🎬\s*)?script\s*#?\s*\d+", text or ""))


def _format_lines(text: str) -> list[str]:
    return re.findall(r"(?im)^\s*[-*]?\s*\*{0,2}format\*{0,2}\s*:\s*(.+)$", text or "")


def count_carousels(text: str) -> int:
    return sum(1 for f in _format_lines(text) if re.search(r"(?i)carousel", f))


def count_reels(text: str) -> int:
    return sum(1 for f in _format_lines(text) if re.search(r"(?i)reel", f) and not re.search(r"(?i)carousel", f))


def has_upload_plan(text: str) -> bool:
    return bool(re.search(r"(?i)next\s*20\s*posts\s*upload\s*plan|upload\s*plan", text or ""))


def looks_complete(text: str) -> bool:
    return (
        count_scripts(text) >= 20
        and bool(re.search(r"(?i)script pack summary|recording priority order", text or ""))
        and count_carousels(text) >= 10
        and count_reels(text) >= 10
        and has_upload_plan(text)
    )


def _continuation_request(result: str) -> str:
    scripts, reels, carousels, plan = count_scripts(result), count_reels(result), count_carousels(result), has_upload_plan(result)
    tail = f"### Draft so far (tail)\n{(result or '')[-6000:]}"
    if scripts >= 20 and (carousels < 10 or reels < 10):
        return (
            "The pack has enough total scripts but the 10/10 Reel/Carousel mix is incomplete. "
            f"Current counts: ~{reels} reels, ~{carousels} carousels. "
            "Add ONLY scripts in the missing format until you have exactly 10 of each. "
            "Carousels must be slide-by-slide with Format labeled. Every new script must include "
            "Recommended upload day, Recommended upload time (IST), and Why this upload slot. "
            "Then update the Next 20 Posts Upload Plan.\n\n" + tail
        )
    if scripts >= 20 and not plan:
        return (
            "Scripts and format mix look complete, but the Next 20 Posts Upload Plan is missing. "
            "Add ONLY the Script Pack Summary section titled Next 20 Posts Upload Plan with a "
            "table covering all 20 posts: Post #, Day, Date (IST), Time (IST), Format, Script #, "
            "Topic, Why this slot. Use IST peak windows and 1 post/day alternating Reel/Carousel.\n\n" + tail
        )
    return (
        "Continue the Prompt 6 script pack exactly where this draft ended. "
        "Do not restart from Script 1. Do not repeat completed scripts. "
        f"You currently have {scripts} scripts (~{reels} reels, ~{carousels} carousels); "
        f"the next script is Script #{scripts + 1}. "
        "Keep writing until you have exactly 20 complete scripts with Viral Proof Blocks, "
        "exactly 10 Reel and 10 Carousel scripts, every script with Recommended upload "
        "day/time (IST) and Why this upload slot, then finish with the Script Pack Summary "
        "including the Next 20 Posts Upload Plan.\n\n" + tail
    )


def write_outputs(settings: Settings, subject_username: str, body: str, stamp: str) -> dict:
    """Write the script pack as .txt, .md and .pdf; returns the paths."""
    from .md_to_pdf import markdown_to_pdf

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    base = settings.output_dir / f"{stamp}_{subject_username}_viral_scripts"
    txt_path, md_path, pdf_path = (Path(f"{base}{ext}") for ext in (".txt", ".md", ".pdf"))
    body = (body or "").strip()

    txt = f"Viral Script Amplification Output\n\nCreator: @{subject_username}\n\n{'=' * 60}\n\n{body}\n"
    md = f"# Viral Script Amplification Output\n\n**Creator:** @{subject_username}\n\n---\n\n{body}\n"
    txt_path.write_text(txt, encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    try:
        markdown_to_pdf(md, pdf_path)
    except Exception:
        logger.exception("[Execute Prompt 6] failed to write PDF -> %s (txt/md were still saved)", pdf_path)
    logger.info(
        "[Execute Prompt 6] wrote %s (%d chars, %d scripts, ~%d reels, ~%d carousels)",
        md_path, len(body), count_scripts(body), count_reels(body), count_carousels(body),
    )
    return {
        "final_document": txt,
        "output_txt_path": str(txt_path),
        "output_md_path": str(md_path),
        "output_pdf_path": str(pdf_path),
    }


MAX_CONTINUATIONS = 10


@log_stage("Execute Prompt 6")
def execute_prompt_6(state: GraphState, config: RunnableConfig) -> dict:
    """Viral script amplification engine; loops until the 20-script pack is complete."""
    settings, llm = _runtime(config)
    system = extract_final_prompt(PROMPT_6_PATH)
    human = format_prior_context(
        settings,
        state,
        ["content_dna_intake", "prompt_1_result", "prompt_2_result", "prompt_3_result", "prompt_4_result", "prompt_5_result"],
        include_dna=False,
    )
    human += (
        "\n\nProduce exactly 20 complete ready-to-record scripts with Viral Proof Blocks. "
        "Use Prompt 3 performance learnings together with Prompt 2 research, Prompt 4 "
        "priorities, and Prompt 5 scripts. "
        "HARD REQUIREMENT: exactly 10 Reel scripts and exactly 10 Carousel/comparison-carousel "
        "scripts with Format labeled accordingly; carousels must be full slide-by-slide. "
        "HARD REQUIREMENT: every script must include Recommended upload day, Recommended "
        "upload time (IST), and Why this upload slot. "
        "HARD REQUIREMENT: end with a Next 20 Posts Upload Plan table covering all 20 posts "
        "using IST peak windows (Morning 08:00-09:30, Lunch 12:30-14:00, Evening 19:00-21:00, "
        "Late night 21:30-22:30), default 1 post/day alternating Reel and Carousel."
    )

    subject_username = (state.get("subject") or {}).get("username", "creator")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    result = invoke_prompt(settings, llm, system, human)
    outputs = write_outputs(settings, subject_username, result, stamp)

    # Output token limits can cut the pack short; ask the model to continue until complete.
    for cont in range(1, MAX_CONTINUATIONS + 1):
        if looks_complete(result):
            break
        logger.info(
            "[Execute Prompt 6] incomplete (%d scripts, ~%d reels, ~%d carousels, upload_plan=%s); continuation %d/%d",
            count_scripts(result), count_reels(result), count_carousels(result), has_upload_plan(result), cont, MAX_CONTINUATIONS,
        )
        request = _continuation_request(result)
        if not settings.enforce_tpm_limit:
            # With a large context window, keep the research in view so continuations stay on-strategy.
            request = human + "\n\n---\n\n" + request
        chunk = invoke_prompt(settings, llm, system, request)
        if not (chunk or "").strip():
            break
        result = f"{result.rstrip()}\n\n{chunk.strip()}".strip()
        outputs = write_outputs(settings, subject_username, result, stamp)

    return {"prompt_6_result": result, **outputs}
