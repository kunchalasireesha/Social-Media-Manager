"""Instagram scraping via Apify, with a same-day scrape log to avoid paying twice."""

from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from apify_client import ApifyClient

from .config import Settings, logger
from .models import insta_profile

SCRAPE_LOG_FIELDS = ["date", "username", "role", "json_path", "scraped_at"]
ROLES = ("subject", "competitors", "important")


def _dataset_id(run) -> str:
    if isinstance(run, dict):
        return run["defaultDatasetId"]
    return getattr(run, "default_dataset_id", None) or run["defaultDatasetId"]


def normalize_username(username: str) -> str:
    return (username or "").lstrip("@").strip()


def _today_str() -> str:
    return date.today().isoformat()


def dna_json_path(settings: Settings, role: str, username: str) -> Path:
    return settings.dna_dir / role / f"{normalize_username(username)}.json"


# --- scrape log ---------------------------------------------------------------

def _ensure_scrape_log(settings: Settings) -> None:
    path = settings.scrape_log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=SCRAPE_LOG_FIELDS).writeheader()


def _read_scrape_log(settings: Settings) -> list[dict]:
    _ensure_scrape_log(settings)
    with settings.scrape_log_path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _to_log_path(settings: Settings, path: Path) -> str:
    """Store paths relative to data_dir when possible (portable), else absolute."""
    try:
        return Path(path).resolve().relative_to(settings.data_dir.resolve()).as_posix()
    except ValueError:
        return Path(path).resolve().as_posix()


def _from_log_path(settings: Settings, value: str) -> Path:
    p = Path(value.replace("\\", "/"))
    return p if p.is_absolute() else settings.data_dir / p


def record_scrape(settings: Settings, username: str, role: str, json_path: Path) -> None:
    """Append one row to the scrape log sheet for today's scrape."""
    _ensure_scrape_log(settings)
    now = datetime.now()
    row = {
        "date": now.date().isoformat(),
        "username": normalize_username(username),
        "role": role,
        "json_path": _to_log_path(settings, json_path),
        "scraped_at": now.isoformat(timespec="seconds"),
    }
    with settings.scrape_log_path.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=SCRAPE_LOG_FIELDS).writerow(row)
    logger.info("[scrape-log] recorded %s/%s for %s -> %s", role, row["username"], row["date"], row["json_path"])


def was_scraped_today(settings: Settings, username: str, role: str | None = None) -> Optional[Path]:
    """
    Return path to today's DNA JSON for this account if it already exists.
    Checks the log sheet first, then falls back to DNA file mtime for the same day.
    """
    username = normalize_username(username)
    today = _today_str()

    for row in reversed(_read_scrape_log(settings)):
        if row.get("date") != today:
            continue
        if normalize_username(row.get("username", "")) != username:
            continue
        if role and row.get("role") != role:
            continue
        raw = row.get("json_path") or ""
        if raw:
            path = _from_log_path(settings, raw)
            if path.is_file():
                return path

    for r in [role] if role else list(ROLES):
        path = dna_json_path(settings, r, username)
        if path.is_file():
            if datetime.fromtimestamp(path.stat().st_mtime).date().isoformat() == today:
                return path
    return None


# --- Apify ----------------------------------------------------------------------

def load_insta_profile_from_json(json_path: Path) -> insta_profile:
    """Rebuild insta_profile from a saved dna/<role>/{username}.json."""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    profile = data.get("profile") or {}
    post_urls = [
        post.get("url") or post.get("inputUrl")
        for post in data.get("posts") or []
        if post.get("url") or post.get("inputUrl")
    ]
    return insta_profile(
        username=profile.get("username") or Path(json_path).stem,
        full_name=profile.get("full_name", ""),
        followers=int(profile.get("followers") or 0),
        following=int(profile.get("following") or 0),
        posts_count=int(profile.get("posts_count") or 0),
        bio=profile.get("bio", ""),
        profile_picture=profile.get("profile_picture", ""),
        posts=post_urls,
    )


def _is_reel(post: dict) -> bool:
    """Best-effort Instagram reel/video detection from profile-scraper fields."""
    post_type = str(post.get("type") or "").lower()
    product = str(post.get("productType") or "").lower()
    return (
        post_type in {"video", "reel", "clips"}
        or product in {"clips", "reels", "reel"}
        or bool(post.get("videoUrl"))
        or post.get("videoViewCount") is not None
    )


def _select_reel_urls(latest_posts: list[dict], limit: int) -> list[str]:
    """Prefer reels/videos; if fewer than limit, fill with other latest posts."""
    with_url = [p for p in latest_posts if p.get("url")]
    reels = [p for p in with_url if _is_reel(p)]
    others = [p for p in with_url if not _is_reel(p)]
    selected = reels[:limit]
    if len(selected) < limit:
        selected.extend(others[: limit - len(selected)])
    return [p["url"] for p in selected]


def get_insta_profile(settings: Settings, username: str) -> insta_profile:
    username = normalize_username(username)
    cached_path = was_scraped_today(settings, username)
    if cached_path:
        logger.info("[Apify] skip profile scrape for %s — already scraped today (%s)", username, cached_path)
        return load_insta_profile_from_json(cached_path)

    client = ApifyClient(settings.apify_api_key)
    run = client.actor("apify/instagram-profile-scraper").call(run_input={"usernames": [username]})
    items = list(client.dataset(_dataset_id(run)).iterate_items())
    if not items:
        raise ValueError(f"No profile data found for username: {username}")

    data = items[0]
    limit = settings.reels_per_account
    post_urls = _select_reel_urls(data.get("latestPosts", []) or [], limit)
    logger.info("[Apify] %s: selected %d/%d reel/post URLs", username, len(post_urls), limit)

    return insta_profile(
        username=data.get("username", username),
        full_name=data.get("fullName", "") or "",
        followers=data.get("followersCount", 0) or 0,
        following=data.get("followsCount", 0) or 0,
        posts_count=data.get("postsCount", 0) or 0,
        bio=data.get("biography", "") or "",
        profile_picture=data.get("profilePicUrl", "") or "",
        posts=post_urls,
    )


def scrape_profile_reels(settings: Settings, profile: insta_profile, role: str) -> list[dict]:
    """Fetch recent posts/reels and persist under dna/<role>/{username}.json."""
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role!r}")

    limit = settings.reels_per_account
    output_path = dna_json_path(settings, role, profile.username)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cached_path = was_scraped_today(settings, profile.username, role=role)
    if cached_path and cached_path.is_file():
        logger.info("[Apify] skip reel scrape for %s/%s — already scraped today (%s)", role, profile.username, cached_path)
        data = json.loads(cached_path.read_text(encoding="utf-8"))
        if cached_path.resolve() != output_path.resolve():
            output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return (data.get("posts") or [])[:limit]

    urls = list(profile.posts or [])[:limit]
    items: list[dict] = []
    if urls:
        client = ApifyClient(settings.apify_api_key)
        run = client.actor("apify/instagram-scraper").call(
            run_input={"resultsType": "posts", "directUrls": urls, "resultsLimit": limit}
        )
        items = list(client.dataset(_dataset_id(run)).iterate_items())[:limit]

    logger.info("[Apify] scraped %d items for %s/%s (limit=%d)", len(items), role, profile.username, limit)

    output_data = {
        "profile": {
            "username": profile.username,
            "full_name": profile.full_name,
            "bio": profile.bio,
            "followers": profile.followers,
            "following": profile.following,
            "posts_count": profile.posts_count,
            "profile_picture": profile.profile_picture,
        },
        "posts": items,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "scrape_date": _today_str(),
    }
    output_path.write_text(json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8")
    record_scrape(settings, profile.username, role, output_path)
    return items


def reset_role_dir(settings: Settings, role: str, keep_usernames: set[str] | None = None) -> None:
    """Ensure dna/<role> exists; drop stale JSON but keep same-day scrapes we will reuse."""
    role_dir = settings.dna_dir / role
    role_dir.mkdir(parents=True, exist_ok=True)
    keep = {normalize_username(u) for u in (keep_usernames or set())}
    for path in role_dir.glob("*.json"):
        if path.stem in keep and was_scraped_today(settings, path.stem, role=role):
            logger.info("[dna] keeping same-day cache %s/%s", role, path.stem)
            continue
        os.remove(path)
