#!/usr/bin/env python3
"""
sync_plex_descriptions.py

Walks every album in a Plex music library (podcasts organized as
Artist = Podcast Name, Album = Episode Title), and for any album that
doesn't already have a summary in Plex, pulls the description out of
the episode's MP3 ID3 comment tag and pushes it up as the album
summary.

Albums that already have a summary are left alone. Albums whose MP3
has no ID3 comment to pull from are skipped with a logged warning,
not guessed at.
"""

# Standard library imports.
import argparse
import json
import os
import sys

# Third-party imports.
# Install with: pip install plexapi mutagen
from mutagen.id3 import ID3, ID3NoHeaderError
from plexapi.server import PlexServer


def load_config(config_path):
    """
    Load and return the JSON config file with the Plex connection
    details and local filesystem settings.
    """
    with open(config_path, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def get_track_file_path(album, path_map):
    """
    Ask Plex what file it has on disk for this album's (single)
    track, and return that path -- remapped through path_map if the
    machine running this script sees the media under a different
    mount point than the Plex server does.
    """
    tracks = album.tracks()

    if not tracks:
        return None

    track = tracks[0]

    if not track.media or not track.media[0].parts:
        return None

    # This is the path exactly as the Plex server sees it, which may
    # or may not match how this script sees the same file on disk.
    plex_reported_path = track.media[0].parts[0].file

    if path_map:
        plex_prefix = path_map["plex_prefix"]
        local_prefix = path_map["local_prefix"]

        if plex_reported_path.startswith(plex_prefix):
            plex_reported_path = local_prefix + plex_reported_path[len(plex_prefix):]

    return plex_reported_path


def read_id3_description(mp3_path):
    """
    Read the episode description back out of an MP3's ID3 comment
    tags. podcast_grabber.py writes two COMM frames (one generic, one
    tagged "eng") with the same text, so we prefer the English one if
    it's there and fall back to whichever comment exists otherwise.
    """
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        # No ID3 tag at all on this file.
        return None

    comment_frames = tags.getall("COMM")

    if not comment_frames:
        return None

    english_frames = [frame for frame in comment_frames if frame.lang == "eng"]
    chosen_frame = english_frames[0] if english_frames else comment_frames[0]

    if not chosen_frame.text:
        return None

    description = str(chosen_frame.text[0]).strip()

    return description if description else None


def sync_descriptions(plex, library_name, path_map, lock_summary, dry_run):
    """
    Walk every album in the given library section and fill in a
    missing summary from the corresponding MP3's ID3 comment, where
    one is available.
    """
    section = plex.library.section(library_name)
    albums = section.albums()

    updated_count = 0
    already_had_summary_count = 0
    warning_count = 0

    for album in albums:
        podcast_name = album.parentTitle
        episode_title = album.title

        # Don't touch anything that already has a description --
        # this script only fills gaps, it doesn't overwrite existing
        # summaries (even ones Plex guessed at itself).
        if album.summary and album.summary.strip():
            already_had_summary_count += 1
            continue

        mp3_path = get_track_file_path(album, path_map)

        if not mp3_path or not os.path.exists(mp3_path):
            print(f"  [warn] Couldn't locate the audio file on disk for '{podcast_name} / {episode_title}'")
            warning_count += 1
            continue

        description = read_id3_description(mp3_path)

        if not description:
            print(f"  [warn] No ID3 comment found for '{podcast_name} / {episode_title}' -- skipping")
            warning_count += 1
            continue

        print(f"  [update] {podcast_name} / {episode_title}")

        if not dry_run:
            album.editSummary(description, locked=lock_summary)

        updated_count += 1

    print(
        f"\nDone. Updated {updated_count}, already had a summary "
        f"{already_had_summary_count}, warnings {warning_count}."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Fill in missing Plex album summaries from each episode's ID3 comment."
    )
    parser.add_argument(
        "--config",
        default="plex_config.json",
        help="Path to the JSON config file (default: plex_config.json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without actually changing anything in Plex.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Config file not found: {args.config}")
        sys.exit(1)

    config = load_config(args.config)

    plex_url = config["plex_url"]
    plex_token = config["plex_token"]
    library_name = config["library_name"]

    # Optional: only needed if this script runs somewhere that sees
    # the media under a different path than the Plex server does.
    # Leave both as empty strings in the config to skip remapping.
    path_map = config.get("path_map")
    if path_map and not (path_map.get("plex_prefix") and path_map.get("local_prefix")):
        path_map = None

    lock_summary = config.get("lock_summary", True)

    plex = PlexServer(plex_url, plex_token)

    sync_descriptions(
        plex=plex,
        library_name=library_name,
        path_map=path_map,
        lock_summary=lock_summary,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
