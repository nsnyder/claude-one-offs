#!/usr/bin/env python3
"""
podcast_grabber.py

A cron-able script that checks one or more RSS feeds, downloads new
episodes, and tags the resulting MP3 files.

Each episode gets its own folder, laid out like this:

    {Podcast Name}/{Episode Title}/01 {Episode Title}.mp3
    {Podcast Name}/{Episode Title}/01 poster.jpg

Run it by hand to test, then point cron at it. It is safe to run
repeatedly (e.g. every 30 minutes) because it keeps a small state
file per feed and only downloads episodes it hasn't seen before.
"""

# Standard library imports.
import argparse
import json
import os
import re
import sys
from datetime import datetime

# Third-party imports.
# Install with: pip install feedparser requests mutagen
import feedparser
import requests
from mutagen.id3 import (
    ID3,
    ID3NoHeaderError,
    APIC,
    COMM,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
    TSO2,
    TSOP,
)


# ---------------------------------------------------------------------------
# Hard-coded sort-order map.
# ---------------------------------------------------------------------------
# The user asked for this to just be a hard-coded lookup rather than
# something auto-generated, since sort order ("Last, First ...") isn't
# something that can be reliably derived from a podcast's display name.
#
# Add one entry per podcast here. The key must match the "name" field
# used for that feed in config.json exactly.
#
# Both the "Album Artist Sort Order" and "Artist Sort Order" ID3 fields
# use this same value, matching the screenshot the user provided.
SORT_ORDER_MAP = {
    "Norah Jones Is Playing Along": "Jones, Norah Is Playing Along",
}


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------

def sanitize_filename(name):
    """
    Strip characters that aren't safe in Windows (or POSIX) file and
    folder names, and trim the trailing dots/spaces Windows dislikes.
    """
    # Remove characters that are illegal in Windows paths.
    cleaned = re.sub(r'[<>:"/\\|?*]', "", name)
    # Collapse any run of whitespace into a single space.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Windows doesn't allow file/folder names to end in a dot or space.
    cleaned = cleaned.rstrip(". ")
    return cleaned


def load_config(config_path):
    """
    Load and return the JSON config file describing which feeds to
    check and where their downloads should go.
    """
    with open(config_path, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def load_state(state_path):
    """
    Load the small JSON file that tracks which episode GUIDs have
    already been downloaded for a given feed. Returns an empty state
    if the file doesn't exist yet (i.e. first run for that feed).
    """
    if not os.path.exists(state_path):
        return {"downloaded_guids": []}

    with open(state_path, "r", encoding="utf-8") as state_file:
        return json.load(state_file)


def save_state(state_path, state):
    """
    Write the state file back out, creating its parent folder if
    needed.
    """
    os.makedirs(os.path.dirname(state_path), exist_ok=True)

    with open(state_path, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2)


def get_episode_guid(entry):
    """
    Get a stable, unique identifier for a feed entry so we can tell
    whether we've already downloaded it. Falls back to the enclosure
    URL if the feed doesn't provide a proper guid/id.
    """
    if getattr(entry, "id", None):
        return entry.id

    if entry.get("enclosures"):
        return entry["enclosures"][0]["href"]

    # Last resort: use the title, which isn't truly unique but is
    # better than crashing.
    return entry.get("title", "")


def get_episode_audio_url(entry):
    """
    Pull the MP3 (or other audio) enclosure URL out of a feed entry.
    Returns None if the entry has no audio enclosure at all.
    """
    for enclosure in entry.get("enclosures", []):
        enclosure_type = enclosure.get("type", "")
        if "audio" in enclosure_type or enclosure.get("href", "").endswith(".mp3"):
            return enclosure["href"]

    return None


def get_episode_image_url(entry, fallback_feed_image_url):
    """
    Pull the per-episode artwork URL out of a feed entry, falling
    back to the podcast's overall cover image if the episode doesn't
    have its own.
    """
    # feedparser usually exposes an <itunes:image href="..."> on the
    # item as entry.image.href.
    entry_image = entry.get("image")
    if entry_image and entry_image.get("href"):
        return entry_image["href"]

    # Some feedparser versions surface it under this name instead, so
    # check it too before giving up on per-episode art.
    itunes_image = entry.get("itunes_image")
    if itunes_image and itunes_image.get("href"):
        return itunes_image["href"]

    return fallback_feed_image_url


def get_episode_date(entry):
    """
    Turn a feed entry's published date into a plain "YYYY-MM-DD"
    string for the ID3 date tag, matching the screenshot's format.
    Falls back to today's date if the feed doesn't provide one.
    """
    if getattr(entry, "published_parsed", None):
        return datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d")

    return datetime.now().strftime("%Y-%m-%d")


def download_file(url, dest_path, user_agent):
    """
    Stream a URL down to disk in chunks, so large audio files don't
    have to be held in memory all at once.
    """
    headers = {"User-Agent": user_agent}

    with requests.get(url, headers=headers, stream=True, timeout=60) as response:
        response.raise_for_status()

        with open(dest_path, "wb") as out_file:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    out_file.write(chunk)


def get_sort_order(podcast_name):
    """
    Look up the hard-coded sort order for a podcast name. Warns and
    falls back to the plain name if nothing's been mapped yet, so a
    missing entry doesn't crash the whole run.
    """
    if podcast_name in SORT_ORDER_MAP:
        return SORT_ORDER_MAP[podcast_name]

    print(
        f"  [warn] No sort order mapped for '{podcast_name}' in "
        f"SORT_ORDER_MAP -- using the plain name instead."
    )
    return podcast_name


# ---------------------------------------------------------------------------
# Tagging.
# ---------------------------------------------------------------------------

def tag_mp3(mp3_path, podcast_name, episode_title, episode_date, description, genre, cover_path):
    """
    Write ID3 tags onto the downloaded MP3, matching the layout from
    the screenshot:

        Title                     -> Episode Title
        Artist                    -> Podcast Name
        Album                     -> Episode Title
        Track Number              -> 1
        Date                      -> episode's publish date
        Album Artist              -> Podcast Name
        Album Artist Sort Order   -> hard-coded lookup
        Artist Sort Order         -> hard-coded lookup (same value)
        Comment / Comment [ENG]   -> episode description
        Genre                     -> "Podcast" (or whatever the feed config says)
    """
    sort_order = get_sort_order(podcast_name)

    # Start from an existing ID3 tag if there is one, otherwise create
    # a fresh, empty tag block.
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()

    # Wipe any existing frames so re-tagging a file doesn't leave
    # stale data behind from a previous run.
    tags.delete()
    tags.clear()

    # encoding=3 tells mutagen to store the text as UTF-8, which is
    # the safest choice for episode titles/descriptions that might
    # contain non-ASCII characters.
    tags.add(TIT2(encoding=3, text=episode_title))
    tags.add(TPE1(encoding=3, text=podcast_name))
    tags.add(TALB(encoding=3, text=episode_title))
    tags.add(TRCK(encoding=3, text="1"))
    tags.add(TDRC(encoding=3, text=episode_date))
    tags.add(TPE2(encoding=3, text=podcast_name))
    tags.add(TSO2(encoding=3, text=sort_order))
    tags.add(TSOP(encoding=3, text=sort_order))
    tags.add(TCON(encoding=3, text=genre))

    # The screenshot shows both a plain "Comment" field and an
    # English-tagged "Comment [ENG]" field. We write two COMM frames
    # with different language codes so both show up the same way.
    tags.add(COMM(encoding=3, lang="XXX", desc="", text=description))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=description))

    # Embed the episode artwork into the file itself, in addition to
    # the separate poster.jpg saved alongside it.
    if cover_path and os.path.exists(cover_path):
        with open(cover_path, "rb") as cover_file:
            cover_bytes = cover_file.read()

        mime_type = "image/png" if cover_path.lower().endswith(".png") else "image/jpeg"

        tags.add(
            APIC(
                encoding=3,
                mime=mime_type,
                type=3,  # "3" is the ID3 code for front cover art.
                desc="Cover",
                data=cover_bytes,
            )
        )

    tags.save(mp3_path, v2_version=3)


# ---------------------------------------------------------------------------
# Main per-feed logic.
# ---------------------------------------------------------------------------

def process_feed(feed_config, user_agent, dry_run):
    """
    Check a single feed for new episodes and download/tag any that
    haven't been seen before.
    """
    podcast_name = feed_config["name"]
    rss_url = feed_config["rss_url"]
    output_dir = feed_config["output_dir"]
    genre = feed_config.get("genre", "Podcast")
    check_latest = feed_config.get("check_latest", 5)

    print(f"Checking feed: {podcast_name}")

    parsed_feed = feedparser.parse(rss_url)

    # The podcast's overall artwork, used as a fallback when an
    # individual episode doesn't have its own image.
    fallback_image_url = parsed_feed.feed.get("image", {}).get("href")

    safe_podcast_name = sanitize_filename(podcast_name)
    podcast_dir = os.path.join(output_dir, safe_podcast_name)

    # Keep the state file tucked away in a hidden subfolder so it
    # doesn't clutter the same directory as the downloaded episodes.
    state_path = os.path.join(podcast_dir, ".state", "downloaded.json")
    state = load_state(state_path)
    downloaded_guids = set(state["downloaded_guids"])

    # Only look at the newest handful of entries each run. This keeps
    # a normal cron run cheap, and stops a brand-new feed from trying
    # to pull down its entire back catalog on the first run.
    entries_to_check = parsed_feed.entries[:check_latest]

    for entry in entries_to_check:
        guid = get_episode_guid(entry)

        if guid in downloaded_guids:
            continue

        episode_title = entry.get("title", "Untitled Episode")
        audio_url = get_episode_audio_url(entry)

        if not audio_url:
            print(f"  [skip] '{episode_title}' has no audio enclosure.")
            continue

        print(f"  [new] {episode_title}")

        if dry_run:
            # Don't touch the disk or the state file during a dry run.
            continue

        safe_episode_title = sanitize_filename(episode_title)
        episode_dir = os.path.join(podcast_dir, safe_episode_title)
        os.makedirs(episode_dir, exist_ok=True)

        mp3_path = os.path.join(episode_dir, f"01 {safe_episode_title}.mp3")
        image_path = os.path.join(episode_dir, "01 poster.jpg")

        # Download the audio.
        download_file(audio_url, mp3_path, user_agent)

        # Download the artwork, if we found a usable URL for it.
        image_url = get_episode_image_url(entry, fallback_image_url)
        if image_url:
            try:
                download_file(image_url, image_path, user_agent)
            except requests.RequestException as image_error:
                print(f"  [warn] Couldn't download artwork: {image_error}")
                image_path = None
        else:
            image_path = None

        # Grab the description, preferring the plain-text summary if
        # one is available.
        description = entry.get("summary", "")

        tag_mp3(
            mp3_path=mp3_path,
            podcast_name=podcast_name,
            episode_title=episode_title,
            episode_date=get_episode_date(entry),
            description=description,
            genre=genre,
            cover_path=image_path,
        )

        # Only mark the episode as downloaded once everything above
        # succeeded, so a crash mid-download means it gets retried
        # next run instead of being silently skipped forever.
        downloaded_guids.add(guid)
        state["downloaded_guids"] = list(downloaded_guids)
        save_state(state_path, state)


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Check RSS feeds and download/tag new podcast episodes."
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the JSON config file (default: config.json).",
    )
    parser.add_argument(
        "--feed",
        default=None,
        help="Only process the feed with this exact 'name' from the config.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without actually downloading anything.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Config file not found: {args.config}")
        sys.exit(1)

    config = load_config(args.config)

    # A generic-looking user agent is polite and avoids some hosts
    # rejecting requests that don't look like they came from a browser
    # or podcast app.
    user_agent = config.get(
        "user_agent",
        "podcast-grabber/1.0 (+https://github.com/)",
    )

    feeds = config.get("feeds", [])

    if args.feed:
        feeds = [feed for feed in feeds if feed["name"] == args.feed]
        if not feeds:
            print(f"No feed named '{args.feed}' found in {args.config}")
            sys.exit(1)

    for feed_config in feeds:
        try:
            process_feed(feed_config, user_agent, args.dry_run)
        except Exception as feed_error:
            # One feed failing (bad URL, network hiccup, etc.) shouldn't
            # stop the rest of the feeds in the config from being checked.
            print(f"  [error] Problem processing '{feed_config.get('name')}': {feed_error}")


if __name__ == "__main__":
    main()
