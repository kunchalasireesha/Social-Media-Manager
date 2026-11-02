"""Command-line entry point: python main.py [--input input.txt] [--provider openai|groq]"""

import argparse
from pathlib import Path

from smm import Settings, run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a 20-post Instagram script pack for a creator.")
    parser.add_argument("--input", default="input.txt", help="Free-text file describing subject, competitors and objective")
    parser.add_argument("--provider", choices=["openai", "groq"], help="LLM provider (default: LLM_PROVIDER env or openai)")
    parser.add_argument("--reels", type=int, default=10, help="Recent reels/posts to scrape per account")
    args = parser.parse_args()

    settings = Settings(reels_per_account=args.reels)
    if args.provider:
        settings.llm_provider = args.provider

    final_state = run_pipeline({"input_text": Path(args.input).read_text(encoding="utf-8")}, settings)

    print("\n=== PIPELINE COMPLETE ===")
    for key in ("output_md_path", "output_txt_path", "output_pdf_path"):
        print(f"{key}: {final_state.get(key)}")


if __name__ == "__main__":
    main()
