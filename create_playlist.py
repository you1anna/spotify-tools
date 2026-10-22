#!/usr/bin/env python3
"""Create a new Spotify playlist from a plain-text track list.

Each line is searched for in Spotify's catalog; confident matches go into a
new playlist in list order, and lines with no confident match are reported
with their closest candidate so they can be checked by hand.

Usage:
    create_playlist.py <new-playlist-name> <track-list-file> [--public] [--description TEXT] [--apply]

Without --apply, prints a dry-run report only. Refuses to run if a playlist
with the same name already exists, so a re-run can't create a duplicate.

Track list format: one track per line, blank lines ignored. Accepted forms:
    Artist - Title          (also en/em dash, or a tab, as the separator)
    Title - Artist          (orientation is detected per candidate)
    * Artist Title          (no separator, this repo's own list format)
Leading bullets ("*", "-", "•") and list numbering ("1.", "01)") are stripped.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import spotipy

from spotify_common import (
    READ_SCOPE,
    WRITE_SCOPE,
    get_client,
    iter_user_playlists,
    match_score,
    match_unsplit,
    read_reference_text,
    search_catalog,
)

ADD_THRESHOLD = 0.82  # same bar as reorder_by_reference --add-missing: this writes new content

SEPARATOR = re.compile(r"\t+|\s+[-–—]\s+")
LEADER = re.compile(r"^\s*(?:[*•-]\s+|\d+[.)]\s+)")


def parse_track_list(text: str) -> list[dict]:
    """Split each line into the raw text plus (a, b) halves when a separator is
    present. Which half is the artist is left to scoring, since lists in the
    wild use both orders."""
    entries: list[dict] = []
    for raw in text.splitlines():
        line = LEADER.sub("", raw).strip()
        if not line:
            continue
        parts = SEPARATOR.split(line, maxsplit=1)
        if len(parts) == 2 and all(p.strip() for p in parts):
            entries.append({"line": line, "halves": (parts[0].strip(), parts[1].strip())})
        else:
            entries.append({"line": line, "halves": None})
    return entries


def score_entry(entry: dict, candidate: dict) -> float:
    if entry["halves"] is None:
        return match_unsplit(entry["line"], candidate)
    a, b = entry["halves"]
    return max(match_score(b, a, candidate), match_score(a, b, candidate))


def search_query(entry: dict) -> str:
    return " ".join(entry["halves"]) if entry["halves"] else entry["line"]


def resolve(sp: spotipy.Spotify, entries: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Returns (found, duplicates, not_found, failed). `found` keeps list order;
    `duplicates` are lines that resolved to a track already found earlier."""
    found: list[dict] = []
    duplicates: list[dict] = []
    not_found: list[dict] = []
    failed: list[dict] = []
    seen: dict[str, dict] = {}
    for entry in entries:
        try:
            track, score = search_catalog(sp, search_query(entry), lambda c: score_entry(entry, c))
        except spotipy.SpotifyException as exc:
            failed.append({**entry, "error": str(exc).splitlines()[0]})
            continue
        result = {**entry, "track": track, "score": score}
        if track is None or score < ADD_THRESHOLD:
            not_found.append(result)
        elif track["uri"] in seen:
            duplicates.append({**result, "first": seen[track["uri"]]})
        else:
            seen[track["uri"]] = result
            found.append(result)
    return found, duplicates, not_found, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="Name for the new playlist")
    parser.add_argument("tracks", type=Path, help="Plain-text track list, one track per line")
    parser.add_argument("--public", action="store_true", help="Make the playlist public (default: private)")
    parser.add_argument("--description", default="", help="Playlist description")
    parser.add_argument("--apply", action="store_true", help="Actually create the playlist (default: dry run)")
    args = parser.parse_args()

    entries = parse_track_list(read_reference_text(args.tracks))
    if not entries:
        raise SystemExit(f"{args.tracks} has no track lines.")

    sp = get_client(f"{READ_SCOPE} {WRITE_SCOPE}")
    target = args.name.strip().lower()
    if any(p["name"].strip().lower() == target for p in iter_user_playlists(sp)):
        raise SystemExit(f'A playlist named "{args.name}" already exists. Pick another name.')

    found, duplicates, not_found, failed = resolve(sp, entries)

    print(f"Track list: {args.tracks} ({len(entries)} lines)")
    print(f"\nFound on Spotify ({len(found)}):")
    for r in found:
        t = r["track"]
        print(f'  + {t["artists"]} {t["name"]}  (from "{r["line"]}", score {r["score"]:.2f})')

    if duplicates:
        print(f"\nDuplicates of an earlier line, added once ({len(duplicates)}):")
        for r in duplicates:
            print(f'  = "{r["line"]}"  (same track as "{r["first"]["line"]}")')

    if not_found:
        print(f"\nNo confident match, not added ({len(not_found)}):")
        for r in not_found:
            if r["track"] is None:
                print(f'  - "{r["line"]}"  (no search results)')
            else:
                t = r["track"]
                print(f'  - "{r["line"]}"  (closest: {t["artists"]} {t["name"]}, score {r["score"]:.2f})')

    if failed:
        print(f"\nSearch failed, not added ({len(failed)}):")
        for r in failed:
            print(f'  ! "{r["line"]}"  ({r["error"]})')

    if not found:
        print("\nNothing to add, no playlist created.")
        return
    if not args.apply:
        visibility = "public" if args.public else "private"
        print(f'\nDry run only. Pass --apply to create {visibility} playlist "{args.name}" with {len(found)} tracks.')
        return

    playlist = sp.current_user_playlist_create(args.name, public=args.public, description=args.description)
    uris = [r["track"]["uri"] for r in found]
    for i in range(0, len(uris), 100):
        sp.playlist_add_items(playlist["id"], uris[i : i + 100])
    print(f'\nCreated "{playlist["name"]}" with {len(uris)} tracks: {playlist["external_urls"]["spotify"]}')


if __name__ == "__main__":
    main()
