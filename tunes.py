#!/usr/bin/env python3
"""Export the N most-recently-added tracks of a Spotify playlist to markdown.

Usage:
    tunes.py <playlist-name> [--count N] [--out-dir PATH]

Requires .env (see .env.example) with a Spotify Developer App's
SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET / SPOTIPY_REDIRECT_URI.
First run opens a browser to authorize; the token is cached in
.spotify-cache next to this script so later runs don't need to re-auth.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from spotify_common import READ_SCOPE, find_playlist, get_client, get_track_from_item

DEFAULT_OUT_DIR = Path("/Volumes/Extreme SSD")


def fetch_tracks_by_added_at(sp, playlist_id: str) -> list[dict]:
    items: list[dict] = []
    results = sp.playlist_items(playlist_id, additional_types=("track",), limit=100)
    while results:
        for item in results["items"]:
            track = get_track_from_item(item)
            if track is None or item.get("added_at") is None:
                continue
            item["track"] = track
            items.append(item)
        results = sp.next(results) if results.get("next") else None
    items.sort(key=lambda i: i["added_at"], reverse=True)
    return items


def format_line(item: dict) -> str:
    track = item["track"]
    artists = ", ".join(a["name"] for a in track["artists"])
    return f"* {artists} {track['name']}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("playlist", help="Playlist name (case-insensitive)")
    parser.add_argument("--count", type=int, default=30, help="How many tracks (default 30)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory")
    args = parser.parse_args()

    if not args.out_dir.exists():
        raise SystemExit(f"Output directory not found: {args.out_dir} (is the drive mounted?)")

    sp = get_client(READ_SCOPE)
    playlist = find_playlist(sp, args.playlist)
    items = fetch_tracks_by_added_at(sp, playlist["id"])
    subset = items[: args.count]

    if not subset:
        raise SystemExit(f'Playlist "{playlist["name"]}" has no tracks with an added_at date.')

    slug = playlist["name"].strip().lower().replace(" ", "-")
    out_path = args.out_dir / f"{slug}-tracks.md"

    newest = subset[0]["added_at"]
    oldest = subset[-1]["added_at"]
    lines = [
        f'# {playlist["name"]} — {len(subset)} most recently added tracks',
        "",
        f"Playlist: `{playlist['uri']}` ({len(items)} tracks total). "
        f"Generated {datetime.now().isoformat(timespec='seconds')}.",
        f"Date range in this export: {oldest} to {newest}.",
        "",
    ]
    lines.extend(format_line(item) for item in subset)
    out_path.write_text("\n".join(lines) + "\n")

    print(f"Wrote {len(subset)} tracks to {out_path}")
    print(f"Newest: {subset[0]['track']['name']} ({newest})")
    print(f"Oldest in export: {subset[-1]['track']['name']} ({oldest})")


if __name__ == "__main__":
    main()
