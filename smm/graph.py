"""Builds the linear LangGraph StateGraph and a convenience `run_pipeline` entry point.

START → data_extractor → account_scraper_and_prompt_modifier
      → execute_prompt_1 → … → execute_prompt_6 → END
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator

from langgraph.graph import END, START, StateGraph

from .config import Settings, logger
from .models import GraphState
from .nodes import (
    account_scraper_and_prompt_modifier,
    data_extractor,
    execute_prompt_1,
    execute_prompt_2,
    execute_prompt_3,
    execute_prompt_4,
    execute_prompt_5,
    execute_prompt_6,
)

STAGES: list[tuple[str, str, Callable]] = [
    ("data_extractor", "Reading input & fetching profiles", data_extractor),
    ("account_scraper_and_prompt_modifier", "Scraping posts & building context", account_scraper_and_prompt_modifier),
    ("execute_prompt_1", "Prompt 1 · Content DNA intake", execute_prompt_1),
    ("execute_prompt_2", "Prompt 2 · Content intelligence", execute_prompt_2),
    ("execute_prompt_3", "Prompt 3 · Performance learning", execute_prompt_3),
    ("execute_prompt_4", "Prompt 4 · Opportunity prioritization", execute_prompt_4),
    ("execute_prompt_5", "Prompt 5 · Script execution", execute_prompt_5),
    ("execute_prompt_6", "Prompt 6 · Viral script amplification", execute_prompt_6),
]


def build_graph():
    workflow = StateGraph(GraphState)
    for name, _label, fn in STAGES:
        workflow.add_node(name, fn)
    names = [name for name, _, _ in STAGES]
    workflow.add_edge(START, names[0])
    for a, b in zip(names, names[1:]):
        workflow.add_edge(a, b)
    workflow.add_edge(names[-1], END)
    return workflow.compile()


def _run_config(settings: Settings) -> dict:
    settings.validate()
    return {"configurable": {"settings": settings, "llm": settings.make_llm()}}


def stream_pipeline(initial_state: dict, settings: Settings | None = None) -> Iterator[tuple[str, dict]]:
    """Yield (node_name, state_update) after each stage — used by the Streamlit UI for progress."""
    settings = settings or Settings()
    app = build_graph()
    for chunk in app.stream(initial_state, config=_run_config(settings), stream_mode="updates"):
        for node, update in chunk.items():
            yield node, update or {}


def run_pipeline(initial_state: dict, settings: Settings | None = None) -> dict[str, Any]:
    """Run the whole graph and return the final state."""
    settings = settings or Settings()
    t0 = time.perf_counter()
    final_state = build_graph().invoke(initial_state, config=_run_config(settings))
    logger.info("[PIPELINE] finished in %.1fs", time.perf_counter() - t0)
    return final_state
