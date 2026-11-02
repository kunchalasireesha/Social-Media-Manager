"""Streamlit UI for the SMM pipeline.

Run locally:   streamlit run streamlit_app.py
Deploy:        Streamlit Community Cloud → main file: streamlit_app.py
Secrets (.streamlit/secrets.toml or the Cloud "Secrets" box):
    APIFY_API_KEY = "..."
    OPENAI_API_KEY = "..."
    APP_PASSWORD = "..."     # visitors must enter this to use your keys
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

from smm import Settings, stream_pipeline
from smm.config import PROJECT_ROOT
from smm.graph import STAGES
from smm.models import DEFAULT_CONTENT_OBJECTIVE, profile_metadata, smm_metadata

DATA_DIR = PROJECT_ROOT / ".smm_data"

st.set_page_config(page_title="SMM · AI Instagram Strategist", page_icon="🎬", layout="wide")


def secret(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except Exception:  # no secrets file locally
        value = ""
    return str(value or os.getenv(name, ""))


def parse_accounts(text: str) -> list[profile_metadata]:
    names = [n.strip().lstrip("@") for n in text.replace(",", "\n").splitlines()]
    return [profile_metadata(username=n, description="") for n in names if n]


# --- Sidebar: credentials ---------------------------------------------------------------
with st.sidebar:
    st.header("🔑 Access")
    app_password = secret("APP_PASSWORD")
    server_keys_available = bool(secret("APIFY_API_KEY") and (secret("OPENAI_API_KEY") or secret("GROQ_API_KEY")))

    use_server_keys = False
    if server_keys_available and app_password:
        entered = st.text_input("App password", type="password", help="Ask the owner for access")
        use_server_keys = entered == app_password
        if entered and not use_server_keys:
            st.error("Wrong password")
    elif server_keys_available and not app_password:
        use_server_keys = True  # local dev with .env

    if use_server_keys:
        st.success("Using the app's API keys")
        provider = st.selectbox("LLM provider", ["openai", "groq"], index=0 if secret("OPENAI_API_KEY") else 1)
        apify_key, openai_key, groq_key = secret("APIFY_API_KEY"), secret("OPENAI_API_KEY"), secret("GROQ_API_KEY")
    else:
        st.caption("No password? Bring your own keys — they're used only for this session and never stored.")
        provider = st.selectbox("LLM provider", ["openai", "groq"])
        apify_key = st.text_input("Apify API key", type="password")
        openai_key = st.text_input("OpenAI API key", type="password") if provider == "openai" else ""
        groq_key = st.text_input("Groq API key", type="password") if provider == "groq" else ""

    reels = st.slider("Reels to analyse per account", 3, 20, 10, help="More = better analysis, higher Apify cost")
    st.divider()
    st.caption("Each run scrapes Instagram via Apify and makes ~8+ LLM calls. Expect 3–10 minutes.")

# --- Main: inputs -------------------------------------------------------------------------
st.title("🎬 AI Instagram Content Strategist")
st.write(
    "Analyses a creator and their competitors on Instagram, learns what performs, "
    "and writes **20 ready-to-record scripts** (10 Reels + 10 Carousels) with an IST upload calendar."
)

with st.form("inputs"):
    col1, col2 = st.columns(2)
    with col1:
        subject = st.text_input("Creator's Instagram username", placeholder="unplugged.raagam")
        competitors = st.text_area("Competitor usernames (one per line)", placeholder="paadu_bro_official\npaata_shala")
    with col2:
        important = st.text_area(
            "Reference accounts to emulate (optional)",
            help="Accounts whose look/feel the creator wants to copy — different from competitors",
        )
        objective = st.text_area(
            "Content objective",
            placeholder="Type: Channel growth + promote our live event on Aug 8\nDescription: Grow followers and drive registrations.",
        )
    submitted = st.form_submit_button("Generate script pack", type="primary")

if submitted:
    errors = []
    if not subject.strip():
        errors.append("Enter the creator's username.")
    if not parse_accounts(competitors):
        errors.append("Enter at least one competitor.")
    if not apify_key or not (openai_key if provider == "openai" else groq_key):
        errors.append("API keys are missing — enter the app password or your own keys in the sidebar.")
    for e in errors:
        st.error(e)

    if not errors:
        run_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        settings = Settings(
            apify_api_key=apify_key,
            llm_provider=provider,
            openai_api_key=openai_key,
            groq_api_key=groq_key,
            reels_per_account=reels,
            data_dir=DATA_DIR,
            run_dir=DATA_DIR / "runs" / run_id,
        )
        metadata = smm_metadata(
            subject=profile_metadata(username=subject.strip().lstrip("@"), description=""),
            competitors=parse_accounts(competitors),
            important_accounts=parse_accounts(important),
            content_objective=objective.strip() or DEFAULT_CONTENT_OBJECTIVE,
        )

        labels = {name: label for name, label, _ in STAGES}
        progress = st.progress(0.0, text="Starting…")
        final: dict = {}
        try:
            with st.status("Running pipeline…", expanded=True) as status:
                for i, (node, update) in enumerate(stream_pipeline({"metadata": metadata.model_dump()}, settings), 1):
                    final.update(update)
                    st.write(f"✅ {labels.get(node, node)}")
                    progress.progress(i / len(STAGES), text=labels.get(node, node))
                status.update(label="Done!", state="complete", expanded=False)
            st.session_state["result"] = final
        except Exception as exc:  # surface a readable error instead of a stack trace
            st.error(f"Pipeline failed: {exc}")

result = st.session_state.get("result")
if result and result.get("output_md_path"):
    md_path = Path(result["output_md_path"])
    pdf_path = Path(result.get("output_pdf_path", ""))
    st.subheader("📄 Your script pack")
    c1, c2 = st.columns(2)
    c1.download_button("Download Markdown", md_path.read_bytes(), file_name=md_path.name, mime="text/markdown")
    if pdf_path.is_file():
        c2.download_button("Download PDF", pdf_path.read_bytes(), file_name=pdf_path.name, mime="application/pdf")

    tabs = st.tabs(["Scripts", "1 · DNA", "2 · Intelligence", "3 · Performance", "4 · Priorities", "5 · Drafts"])
    tabs[0].markdown(md_path.read_text(encoding="utf-8"))
    for tab, key in zip(tabs[1:], ["prompt_1_result", "prompt_2_result", "prompt_3_result", "prompt_4_result", "prompt_5_result"]):
        tab.markdown(result.get(key) or "_No output_")
