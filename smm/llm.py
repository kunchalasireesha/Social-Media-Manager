"""LLM invocation with Groq free-tier token budgeting and rate-limit retries."""

from __future__ import annotations

import time
from typing import Any

from .config import Settings, logger


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars/4). Good enough for TPM budgeting."""
    return max(1, (len(text or "") + 3) // 4)


def truncate(text: str, limit: int | None) -> str:
    text = text or ""
    if limit is None or len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated]"


def _fit_to_input_budget(settings: Settings, system_prompt: str, human: str) -> str:
    """Truncate the human message so system+human stay under the input budget (Groq only)."""
    budget = settings.max_input_tokens
    if not settings.enforce_tpm_limit or budget is None:
        return human

    sys_tokens = estimate_tokens(system_prompt)
    human_budget = budget - sys_tokens
    if human_budget < 200:
        logger.warning("System prompt alone is ~%d tokens; human context will be heavily truncated", sys_tokens)
        human_budget = 200

    human_char_budget = human_budget * 4
    if len(human or "") > human_char_budget:
        human = (human or "")[:human_char_budget] + "\n... [truncated to fit Groq TPM budget]"
        logger.warning("Truncated human message to ~%d tokens (system=~%d, budget=%d)", human_budget, sys_tokens, budget)
    return human


def _content_to_text(content: Any) -> str:
    if isinstance(content, list):
        content = "".join(
            part.get("text", str(part)) if isinstance(part, dict) else str(part) for part in content
        )
    return str(content).strip()


def invoke_prompt(settings: Settings, llm: Any, system_prompt: str, human: str) -> str:
    human = _fit_to_input_budget(settings, system_prompt, human)
    estimated = estimate_tokens(system_prompt) + estimate_tokens(human)
    logger.info(
        "LLM invoke ~%d input tokens (provider=%s, input_limit=%s, max_completion=%s)",
        estimated,
        settings.llm_provider,
        settings.max_input_tokens,
        settings.max_completion_tokens,
    )

    # Groq: retry when a prior call in the same minute already consumed TPM.
    max_attempts = 4 if settings.enforce_tpm_limit else 1
    for attempt in range(max_attempts):
        try:
            response = llm.invoke([("system", system_prompt), ("human", human)])
            return _content_to_text(response.content)
        except Exception as err:
            msg = str(err).lower()
            is_tpm = "rate_limit" in msg or "tokens per minute" in msg or "request too large" in msg
            if not settings.enforce_tpm_limit or not is_tpm or attempt == max_attempts - 1:
                raise
            wait_s = 15 * (attempt + 1)
            logger.warning("Groq TPM/rate limit hit; waiting %ds then retrying (%d/%d)", wait_s, attempt + 1, max_attempts - 1)
            time.sleep(wait_s)
    raise RuntimeError("unreachable")
