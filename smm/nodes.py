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
from .llm import DailyLimitError, invoke_prompt, invoke_prompt_ex, truncate
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


TOTAL_SCRIPTS = 20
MAX_CUTOFF_CONTINUATIONS = 3  # extra calls allowed to finish one reply that hit the token cap


def script_format(n: int) -> str:
    """1 post/day alternating Reel and Carousel: odd = Reel, even = Carousel (10 of each)."""
    return "REEL" if n % 2 else "CAROUSEL"


def _script_heading(n: int) -> str:
    return f"## 🎬 SCRIPT #{n} – {script_format(n)}"


def _script_title(n: int, text: str) -> str:
    m = re.search(r"(?im)^\W*title\W*:\s*(.+)$", text or "")
    title = (m.group(1) if m else "").strip().strip("*“”\"").strip()
    return f"#{n} {script_format(n)}: {title[:120] or '(untitled)'}"


def _generate_until_done(settings: Settings, llm: Any, system: str, human: str, label: str) -> str:
    """One LLM reply; if it hits the output-token cap, ask it to continue the same text."""
    text, cut = invoke_prompt_ex(settings, llm, system, human)
    for i in range(1, MAX_CUTOFF_CONTINUATIONS + 1):
        if not cut:
            break
        logger.info("[Execute Prompt 6] %s hit the output cap; continuing (%d/%d)", label, i, MAX_CUTOFF_CONTINUATIONS)
        request = (
            f"Your previous reply ({label}) was cut off by the output limit. Continue it EXACTLY where "
            "it stops: no preamble, no repetition, do not restart the script and do not start any "
            "other script. Finish every remaining required section (including the complete Viral "
            "Proof Block) and then stop.\n\n### Your reply so far (tail)\n" + text[-3000:]
        )
        chunk, cut = invoke_prompt_ex(settings, llm, system, request)
        if not chunk.strip():
            break
        text += chunk  # raw join: the cut can fall mid-word, so don't add or strip whitespace
    return text.strip()


def _ist_start_date() -> str:
    from datetime import timedelta, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    return (datetime.now(ist).date() + timedelta(days=1)).isoformat()


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


@log_stage("Execute Prompt 6")
def execute_prompt_6(state: GraphState, config: RunnableConfig) -> dict:
    """Viral script amplification engine.

    The pack is generated in pieces (scripts in small batches, then the summary) instead of
    one long reply, because output-token caps (Groq: 1500/call) cut a single reply off after
    about one script and "continue" requests made the model restart at Script #1.
    Each request names the exact script numbers and formats, so the pack always has
    20 scripts, 10 Reels + 10 Carousels, in order.
    """
    settings, llm = _runtime(config)
    system = extract_final_prompt(PROMPT_6_PATH)
    context = format_prior_context(
        settings,
        state,
        ["content_dna_intake", "prompt_1_result", "prompt_2_result", "prompt_3_result", "prompt_4_result", "prompt_5_result"],
        include_dna=False,
    )
    subject_username = (state.get("subject") or {}).get("username", "creator")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_date = _ist_start_date()
    batch = 1 if settings.enforce_tpm_limit else 5

    header = (
        f"## 📚 {TOTAL_SCRIPTS} Ready-to-Record Instagram Scripts\n\n"
        f"**Format mix:** {TOTAL_SCRIPTS // 2} Reels | {TOTAL_SCRIPTS // 2} Carousels "
        f"(alternating, 1 post/day starting {start_date})"
    )
    scripts: dict[int, str] = {}

    def assemble(summary: str = "") -> str:
        parts = [header, *(scripts[n] for n in sorted(scripts))] + ([summary] if summary else [])
        return "\n\n---\n\n".join(parts)

    def titles() -> list[str]:
        return [_script_title(n, scripts[n]) for n in sorted(scripts)]

    def request_for(nums: list[int]) -> tuple[str, str]:
        label = f"Script #{nums[0]}" if len(nums) == 1 else f"Scripts #{nums[0]}-#{nums[-1]}"
        plan = "\n".join(
            f"- Script #{n}: {script_format(n).title()} — heading `{_script_heading(n)}`, "
            f"Post {n} of {TOTAL_SCRIPTS}, upload day = {start_date} + {n - 1} day(s)"
            for n in nums
        )
        done = "\n".join(f"- {t}" for t in titles()) or "- (none yet)"
        # Task first: on Groq the human message is truncated from the END to fit the TPM budget.
        human = (
            f"TASK: write ONLY {label} of the {TOTAL_SCRIPTS}-script pack, nothing else.\n{plan}\n\n"
            "Rules:\n"
            "- Start each script with its exact heading above. Label the Format line Reel or Carousel to match.\n"
            "- Carousels need full slide-by-slide copy (Slide 1, Slide 2, ...).\n"
            "- Every script needs Recommended upload day, Recommended upload time (IST), Why this "
            "upload slot, and the complete Viral Proof Block.\n"
            "- Do NOT write a pack introduction, any other script, or the Script Pack Summary.\n\n"
            f"Already written — pick different topics/angles:\n{done}\n\n"
            f"{context}"
        )
        return label, human

    def split_scripts(text: str, nums: list[int]) -> dict[int, str]:
        """Map each requested script number to its section of the reply."""
        if len(nums) == 1:
            if not re.search(rf"(?i)script\s*#?\s*{nums[0]}\b", text[:300]):
                text = f"{_script_heading(nums[0])}\n\n{text}"
            return {nums[0]: text}
        found: dict[int, str] = {}
        for chunk in re.split(r"(?im)^(?=#{1,4}\s*🎬?\s*script\s*#?\s*\d+)", text):
            m = re.match(r"(?i)#{1,4}\s*🎬?\s*script\s*#?\s*(\d+)", chunk)
            if m and int(m.group(1)) in nums and int(m.group(1)) not in found:
                found[int(m.group(1))] = chunk.strip().rstrip("-").strip()
        return found

    outputs: dict = {}
    warning = ""
    try:
        for first in range(1, TOTAL_SCRIPTS + 1, batch):
            nums = list(range(first, min(first + batch, TOTAL_SCRIPTS + 1)))
            label, human = request_for(nums)
            scripts.update(split_scripts(_generate_until_done(settings, llm, system, human, label), nums))
            # A batch reply can skip or merge scripts; fill any gap one script at a time.
            for n in (n for n in nums if n not in scripts):
                logger.info("[Execute Prompt 6] Script #%d missing from batch reply; generating it alone", n)
                label, human = request_for([n])
                scripts.update(split_scripts(_generate_until_done(settings, llm, system, human, label), [n]))
            outputs = write_outputs(settings, subject_username, assemble(), stamp)

        summary_request = (
            f"TASK: all {TOTAL_SCRIPTS} scripts are written (listed below). Write ONLY the Script Pack "
            "Summary, starting with the heading `## Script Pack Summary`. Include Recording Priority "
            "Order, Format Mix Check (10 Reels + 10 Carousels), and the Next 20 Posts Upload Plan table "
            "with exactly 20 rows: | Post # | Day | Date (IST) | Time (IST) | Format | Script # | Topic | "
            f"Why this slot |. Post n = Script #n, one post per day starting {start_date}, using IST peak "
            "windows (Morning 08:00-09:30, Lunch 12:30-14:00, Evening 19:00-21:00, Late night 21:30-22:30). "
            "Do not rewrite any scripts.\n\nScripts:\n" + "\n".join(f"- {t}" for t in titles()) + f"\n\n{context}"
        )
        summary = _generate_until_done(settings, llm, system, summary_request, "Script Pack Summary")
    except DailyLimitError as err:
        # Keep the finished scripts instead of losing the whole run.
        logger.warning("[Execute Prompt 6] stopped early after %d scripts: %s", len(scripts), err)
        warning = f"Incomplete pack: {len(scripts)} of {TOTAL_SCRIPTS} scripts were written before the limit was hit. {err}"
        summary = ""
    result = assemble(summary)
    if warning:
        result = f"> ⚠️ **{warning}**\n\n{result}"
    outputs = write_outputs(settings, subject_username, result, stamp)
    return {"prompt_6_result": result, "pack_warning": warning, **outputs}
