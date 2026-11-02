# SMM — AI Instagram Content Strategist

Give it a creator's Instagram handle, a few competitor handles and a content objective. It scrapes their recent public posts and runs a six-step chain of LLM prompts. You get back **20 ready-to-record scripts** (10 Reels and 10 Carousels), each with an IST upload slot, plus a "Next 20 Posts Upload Plan". The output comes as Markdown, plain text and PDF.

It runs from the command line or as a **Streamlit web app**.

## How it works

![Architecture](reference/smm.png)

The pipeline is a linear [LangGraph](https://github.com/langchain-ai/langgraph) `StateGraph`. Each node reads from and writes to one shared `GraphState`.

```
START → data_extractor → account_scraper_and_prompt_modifier
      → execute_prompt_1 → … → execute_prompt_6 → END
```

| Stage | What it does |
|---|---|
| `data_extractor` | Parses free-text input into a Pydantic `smm_metadata` model using LLM structured output. The web app builds this model directly and skips this step. It then fetches profile details with the Apify `instagram-profile-scraper`. |
| `account_scraper_and_prompt_modifier` | Fetches up to N recent posts per account with the Apify `instagram-scraper`. It then builds a compact "DNA bundle" and a performance brief that ranks posts by `likes + 2×comments + 0.001×views`. |
| Prompt 1 – Content DNA intake | Profiles the creator's niche, voice and audience. |
| Prompt 2 – Content intelligence | Analyses the patterns used by competitors and reference accounts. |
| Prompt 3 – Performance learning | Works out what actually drives engagement. |
| Prompt 4 – Opportunity prioritization | Produces exactly 20 ideas: 10 Reels and 10 Carousels. |
| Prompt 5 – Script execution | Writes the full scripts. |
| Prompt 6 – Viral amplification | Polishes hooks and CTAs and adds the upload schedule. A completeness check continues generation until all 20 scripts and the upload plan are present. |

The system prompts live in `prompts/`.

**Cost-saving features**
- **Same-day scrape cache.** `logs/scrape_log.csv` records each scrape. Rerunning the same accounts on the same day reuses the saved JSON instead of calling Apify again.
- **Groq token budgeting.** In Groq mode every call is trimmed to fit an 8,000 tokens-per-minute limit, and rate-limit errors are retried.

## Project layout

```
smm/
  config.py      Settings (keys, provider, model, folders) + LLM factory
  models.py      Pydantic models and GraphState
  scraper.py     Apify calls + same-day scrape cache
  context.py     DNA bundle, performance brief, prior-context formatting
  prompts.py     Prompt loading / Prompt 1 template rendering
  llm.py         invoke_prompt() with Groq budgeting + retries
  nodes.py       The 8 graph nodes + Prompt 6 completeness loop
  graph.py       build_graph(), run_pipeline(), stream_pipeline()
  md_to_pdf.py   Markdown → PDF (xhtml2pdf, reportlab fallback)
main.py          CLI
streamlit_app.py Web app
smm.ipynb        Notebook demo
prompts/         System prompts 1–6
output/          Generated script packs (one sample run included)
```

## Setup (macOS)

You need Python 3.12 and [uv](https://docs.astral.sh/uv/). To install uv, run `brew install uv`.

```bash
git clone https://github.com/kunchalasireesha/Social-Media-Manager.git
cd Social-Media-Manager
uv sync                 # creates .venv and installs dependencies
```

If you'd rather use plain pip:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### API keys

Create a `.env` file in the project root. It is gitignored, so it never gets committed.

```bash
APIFY_API_KEY=...
OPENAI_API_KEY=...          # needed when LLM_PROVIDER=openai (default)
GROQ_API_KEY=...            # needed when LLM_PROVIDER=groq

# Optional
LLM_PROVIDER=openai         # openai | groq
OPENAI_MODEL=gpt-5.6-luna
GROQ_MODEL=openai/gpt-oss-120b
```

## Usage

### Command line

Describe the subject, competitors, reference accounts and objective in `input.txt` (see the example file), then run:

```bash
uv run python main.py                              # uses input.txt and LLM_PROVIDER
uv run python main.py --provider groq --reels 5    # Groq, 5 posts per account
uv run python main.py --input my_brief.txt
```

The results are written to `output/<timestamp>_<username>_viral_scripts.{md,txt,pdf}`. Scraped posts are saved to `dna/<role>/<username>.json`.

### Web app

```bash
uv run streamlit run streamlit_app.py
```

Enter the handles and objective in the form, then click **Generate script pack**. The page shows each stage as it runs. When the run finishes you can download the Markdown or PDF, or open each prompt's output in its own tab.

To get API keys into the app, pick one of these:
- Type your own keys into the sidebar.
- Create `.streamlit/secrets.toml` (gitignored) with your keys and an `APP_PASSWORD`. Anyone who enters the password can run the app using those keys.

### Python

```python
from smm import Settings, run_pipeline

settings = Settings(llm_provider="openai", reels_per_account=10)
state = run_pipeline({"input_text": open("input.txt").read()}, settings)
print(state["output_md_path"])
```

## Deploying to Streamlit Community Cloud

1. Push this repo to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io), click **Create app**, and pick this repo with branch `main` and main file `streamlit_app.py`.
3. Under **Advanced settings**, choose Python **3.12** and paste your secrets:
   ```toml
   APIFY_API_KEY = "..."
   OPENAI_API_KEY = "..."
   APP_PASSWORD = "choose-a-strong-password"
   ```
4. Click **Deploy**.

Keep in mind that the Cloud filesystem is temporary. Run folders (`.smm_data/`) and the scrape cache are wiped whenever the app restarts, so download your results.

## Cost notes

Each full run makes paid API calls:
- **Apify:** one profile-scraper call plus one post-scraper call per account. Cost grows with the number of accounts × `--reels`. The same-day cache avoids paying twice for the same accounts.
- **LLM:** at least 7 calls (input parsing, then Prompts 1–6). Prompt 6 may add up to 10 continuation calls if the output comes back incomplete. Prompts 5 and 6 produce long outputs and account for most of the token cost.

Test with a small `--reels` value first. If you share the deployed app, protect it with `APP_PASSWORD` so strangers can't spend your credits.

## Limitations

- Only public metrics are used (likes, comments, views). Reach, saves and shares are not available.
- The completeness check on the final output uses regex heuristics, so it can be fooled by unusual formatting.
- The graph runs in a straight line, and accounts are scraped one after another.
- There is no automated test suite yet.
