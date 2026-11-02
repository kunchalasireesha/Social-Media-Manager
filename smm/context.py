"""Turn scraped dna/ JSON into compact LLM context: DNA bundle, performance brief, intake."""

from __future__ import annotations

import json
from typing import Any

from .config import Settings
from .llm import truncate
from .scraper import ROLES

MAX_CAPTION_CHARS = 280


def load_role_profiles(settings: Settings, role: str) -> list[dict]:
    loaded = []
    for path in sorted((settings.dna_dir / role).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        profile = data.get("profile", {}) or {}
        username = profile.get("username") or path.stem
        loaded.append({"username": username, "profile": profile, "posts": data.get("posts", [])})
    return loaded


def _slim_profile(profile: dict) -> dict:
    """Keep only fields needed for DNA analysis; drop bulky URLs."""
    return {
        "username": profile.get("username") or "",
        "full_name": profile.get("full_name") or "",
        "bio": (profile.get("bio") or "")[:300],
        "followers": profile.get("followers"),
        "following": profile.get("following"),
        "posts_count": profile.get("posts_count"),
    }


def _slim_posts(posts: list[dict], limit: int) -> list[dict]:
    return [
        {
            "url": post.get("url") or post.get("displayUrl") or "",
            "caption": (post.get("caption") or "")[:MAX_CAPTION_CHARS],
            "hashtags": (post.get("hashtags") or [])[:10],
            "likesCount": post.get("likesCount"),
            "commentsCount": post.get("commentsCount"),
            "videoViewCount": post.get("videoViewCount"),
            "videoPlayCount": post.get("videoPlayCount"),
            "timestamp": post.get("timestamp"),
            "type": post.get("type") or post.get("productType"),
        }
        for post in posts[:limit]
    ]


def build_dna_bundle(settings: Settings) -> str:
    """Serialize scraped dna/ JSON for LLM context (no filesystem access needed)."""
    bundle = {
        role: [
            {
                "username": f["username"],
                "profile": _slim_profile(f["profile"]),
                "posts": _slim_posts(f["posts"], settings.reels_per_account),
            }
            for f in load_role_profiles(settings, role)
        ]
        for role in ROLES
    }
    # Compact JSON saves ~30% tokens vs indent=2
    text = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    if settings.max_dna_chars is not None and len(text) > settings.max_dna_chars:
        text = text[: settings.max_dna_chars] + "\n... [dna bundle truncated]"
    return text


def post_engagement_score(post: dict) -> float:
    likes = post.get("likesCount") or 0
    comments = post.get("commentsCount") or 0
    views = post.get("videoViewCount") or post.get("videoPlayCount") or 0
    try:
        return float(likes) + 2.0 * float(comments) + 0.001 * float(views)
    except (TypeError, ValueError):
        return 0.0


def build_performance_data(settings: Settings, top_n: int = 15) -> str:
    """Build Prompt 3 performance brief from scraped engagement metrics.

    Uses public scrape metrics (likes, comments, views, plays) - not private
    Instagram Insights (saves/reach/profile visits).
    """
    sections = [
        "LIVE PERFORMANCE DATA (from scraped Instagram posts)",
        "Source note: Public Apify/scrape metrics only. Do not invent Insights fields",
        "(saves, reach, profile visits, watch time) that are not listed below.",
        "",
    ]
    any_posts = False
    for role in ROLES:
        profiles = load_role_profiles(settings, role)
        if not profiles:
            continue
        sections.append(f"## Role: {role}")
        for f in profiles:
            username = f.get("username") or "unknown"
            profile = f.get("profile") or {}
            posts = list(f.get("posts") or [])
            sections.append(f"### @{username}")
            if not posts:
                sections += ["- No posts available in scrape.", ""]
                continue
            any_posts = True
            ranked = sorted(posts, key=post_engagement_score, reverse=True)[:top_n]
            sections.append(f"- followers: {profile.get('followers')} | posts_count: {profile.get('posts_count')}")
            sections.append(f"- top {len(ranked)} posts by engagement proxy (likes + 2*comments + views/plays):")
            for idx, post in enumerate(ranked, 1):
                caption = (post.get("caption") or "").replace("\n", " ").strip()
                if len(caption) > 160:
                    caption = caption[:157] + "..."
                ptype = post.get("type") or post.get("productType") or "unknown"
                sections.append(
                    f"  {idx}. type={ptype} | likes={post.get('likesCount')} | "
                    f"comments={post.get('commentsCount')} | views={post.get('videoViewCount')} | "
                    f"plays={post.get('videoPlayCount')} | ts={post.get('timestamp')}"
                )
                sections.append(f"     url: {post.get('url') or post.get('displayUrl') or ''}")
                if caption:
                    sections.append(f"     caption: {caption}")
            sections.append("")
    if not any_posts:
        return ""
    return truncate("\n".join(sections).strip(), settings.max_dna_chars)


def infer_niche_summary(llm: Any, profile: dict, posts: list[dict]) -> tuple[str, bool]:
    bio = (profile.get("bio") or "").strip()
    if bio:
        return bio, False

    captions = [p.get("caption", "") for p in posts if p.get("caption")]
    hashtags = sorted({h for p in posts for h in p.get("hashtags", [])})
    if not captions and not hashtags:
        return "", False

    context = "\n".join(captions[:10]) + "\n\nHashtags: " + ", ".join(hashtags[:20])
    response = llm.invoke(
        "Based only on the following Instagram captions and hashtags, write a "
        "faithful 1-2 line summary of this creator's niche, topic area, and "
        "audience. Do not invent details not supported by the text:\n\n" + context
    )
    return str(response.content).strip(), True


def build_content_dna_intake(settings: Settings, llm: Any) -> str:
    subject_files = load_role_profiles(settings, "subject")
    competitor_files = load_role_profiles(settings, "competitors")
    important_files = load_role_profiles(settings, "important")

    missing_fields: list[str] = []
    notes: list[str] = []

    if not subject_files:
        subject_username, niche_summary = "No Account created yet", ""
        missing_fields.append("Creator Account Username / Niche Summary (dna/subject/ is empty)")
    else:
        if len(subject_files) > 1:
            notes.append(f"Multiple files found in dna/subject/ ({[f['username'] for f in subject_files]}); using the first one.")
        subject = subject_files[0]
        subject_username = subject["username"]
        niche_summary, inferred = infer_niche_summary(llm, subject["profile"], subject["posts"])
        if inferred:
            notes.append("Niche Summary inferred from post captions/hashtags (no bio available).")
        if not niche_summary:
            missing_fields.append("Niche Summary")

    competitor_usernames = list(dict.fromkeys(f["username"] for f in competitor_files))
    important_usernames = list(dict.fromkeys(f["username"] for f in important_files))

    overlap = sorted(set(competitor_usernames) & set(important_usernames))
    if overlap:
        notes.append(f"Usernames found in both competitors and important accounts (Needs verification): {', '.join(overlap)}")
    if not competitor_usernames:
        missing_fields.append("Competitor Account usernames (dna/competitors/ is empty)")
    if not important_usernames:
        notes.append("No important accounts provided - continuing without them.")

    return "\n".join(
        [
            "CONTENT DNA INTAKE",
            f"Creator Account Username: {subject_username}",
            f"Niche Summary: {niche_summary}",
            f"Competitor Account usernames: {', '.join(competitor_usernames) or 'None'}",
            f"Any Important Account usernames: {', '.join(important_usernames) or 'None'}",
            "Extra Notes: NA",
            f"Missing or Not Provided Fields: {', '.join(missing_fields) or 'None'}",
            f"Data Quality Notes: {', '.join(notes) or 'None'}",
        ]
    )


def format_prior_context(settings: Settings, state: dict, include_keys: list[str], include_dna: bool = True) -> str:
    sections = []
    if include_dna and state.get("dna_bundle"):
        sections.append(f"### dna_bundle\n{state['dna_bundle']}")
    for key in include_keys:
        value = state.get(key)
        if value:
            sections.append(f"### {key}\n{truncate(str(value), settings.max_prior_chars)}")
    if not sections:
        return "No prior pipeline outputs were available. Proceed using only the system prompt."
    return (
        "Prior pipeline outputs follow. Use them as authoritative inputs for this stage.\n"
        "If Important Accounts are None/empty, continue without them — do not invent any.\n\n"
        + "\n\n".join(sections)
    )
