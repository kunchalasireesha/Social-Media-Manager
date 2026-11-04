"""Runtime settings and LLM factory.

Settings are passed explicitly into the graph (via LangGraph's `configurable`)
instead of living in globals, so the CLI and the Streamlit app can run with
different keys / folders without leaking state between users.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("smm")

# Groq free/on_demand TPM for openai/gpt-oss-120b is 8000 (input + completion).
GROQ_TPM_LIMIT = 8000
GROQ_MAX_COMPLETION_TOKENS = 1500
GROQ_TOKEN_SAFETY_MARGIN = 300


@dataclass
class Settings:
    """Everything a pipeline run needs from the outside world."""

    apify_api_key: str = field(default_factory=lambda: os.getenv("APIFY_API_KEY", ""))
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openai"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.6-luna"))
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    temperature: float = 0.7
    reels_per_account: int = 10

    # data_dir holds the shared scrape log (same-day cache across runs).
    # run_dir holds this run's dna/ and output/ folders.
    data_dir: Path = PROJECT_ROOT
    run_dir: Path = PROJECT_ROOT

    def __post_init__(self) -> None:
        self.llm_provider = (self.llm_provider or "openai").lower()
        self.data_dir = Path(self.data_dir)
        self.run_dir = Path(self.run_dir)
        if self.llm_provider not in {"openai", "groq"}:
            raise ValueError(f"Unsupported llm_provider={self.llm_provider!r}; use 'openai' or 'groq'")

    # --- derived -----------------------------------------------------------
    @property
    def enforce_tpm_limit(self) -> bool:
        return self.llm_provider == "groq"

    @property
    def max_completion_tokens(self) -> int | None:
        return GROQ_MAX_COMPLETION_TOKENS if self.enforce_tpm_limit else None

    @property
    def max_input_tokens(self) -> int | None:
        if not self.enforce_tpm_limit:
            return None
        return GROQ_TPM_LIMIT - GROQ_MAX_COMPLETION_TOKENS - GROQ_TOKEN_SAFETY_MARGIN

    @property
    def max_prior_chars(self) -> int | None:
        return 2500 if self.enforce_tpm_limit else None

    @property
    def max_dna_chars(self) -> int | None:
        return 9000 if self.enforce_tpm_limit else None

    @property
    def dna_dir(self) -> Path:
        return self.run_dir / "dna"

    @property
    def output_dir(self) -> Path:
        return self.run_dir / "output"

    @property
    def scrape_log_path(self) -> Path:
        return self.data_dir / "logs" / "scrape_log.csv"

    def validate(self) -> None:
        missing = []
        if not self.apify_api_key:
            missing.append("APIFY_API_KEY")
        if self.llm_provider == "openai" and not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if self.llm_provider == "groq" and not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if missing:
            raise ValueError(
                f"Missing API key(s): {', '.join(missing)}. Add them to .env or pass them in Settings."
            )

    def make_llm(self) -> Any:
        if self.llm_provider == "groq":
            from langchain_groq import ChatGroq

            extra: dict[str, Any] = {}
            if "gpt-oss" in self.groq_model:
                # Hidden reasoning tokens count against max_tokens; keep them small so the
                # 1500-token cap is spent on visible script text.
                extra["reasoning_effort"] = "low"
            return ChatGroq(
                model_name=self.groq_model,
                temperature=self.temperature,
                max_tokens=self.max_completion_tokens,
                api_key=self.groq_api_key,
                **extra,
            )
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model_name=self.openai_model,
            temperature=self.temperature,
            api_key=self.openai_api_key,
        )
