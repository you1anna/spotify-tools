#!/usr/bin/env python3
"""Export the N most-recently-added tracks of a Spotify playlist to markdown.

Usage:
    tunes.py <playlist-name> [--count N] [--out-dir PATH]
             [--reference FILE] [--match-threshold F]

With --reference, also cross-references those tracks against a Rekordbox text
export and writes a second markdown file listing the ones not already in it —
i.e. what still needs pulling for the set. Read-only either way.

Requires .env (see .env.example) with a Spotify Developer App's
SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET / SPOTIPY_REDIRECT_URI.
First run opens a browser to authorize; the token is cached in
.spotify-cache next to this script so later runs don't need to re-auth.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from spotify_common import (
    READ_SCOPE,
    fetch_playlist_tracks,
    find_playlist,
    get_client,
    match_score,
    normalize,
    normalize_title,
    parse_reference,
    sort_by_added_at,
)

DEFAULT_OUT_DIR = Path("/Volumes/Extreme SSD")

# Bar for "this track is already in Rekordbox". Higher than
# reorder_by_reference.py's 0.72: a false positive here silently drops a track
# off the to-buy list, which is worse than listing one you already own.
DEFAULT_MATCH_THRESHOLD = 0.78


def format_line(track: dict) -> str:
    return f"* {track['artists']} {track['name']}"


def track_key(track: dict) -> tuple[str, str]:
    """Identity for de-duping. Normalised title + artist rather than URI, so
    the same record appearing twice as different Spotify releases (single vs
    compilation vs remaster) collapses to one entry."""
    return (normalize_title(track["name"]), normalize(track["artists"]))


def dedupe(tracks: list[dict]) -> list[dict]:
    """Drop repeats, keeping the first occurrence (newest, given the sort)."""
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for track in tracks:
        key = track_key(track)
        if key in seen:
            continue
        seen.add(key)
        unique.append(track)
    return unique


def find_missing(tracks: list[dict], reference: list[dict], threshold: float) -> list[dict]:
    """Tracks with no confident match in the reference rows, each annotated
    with its closest near-miss so a borderline call can be eyeballed."""
    missing: list[dict] = []
    for track in tracks:
        best_score, best_row = 0.0, None
        for row in reference:
            score = match_score(row["title"], row["artist"], track)
            if score > best_score:
                best_score, best_row = score, row
        if best_score < threshold:
            missing.append({**track, "best_score": best_score, "best_row": best_row})
    return missing


def write_missing_report(
    out_path: Path,
    playlist_name: str,
    reference_path: Path,
    missing: list[dict],
    checked: int,
    reference_rows: int,
    duplicates_dropped: int,
    threshold: float,
) -> None:
    lines = [
        f"# {playlist_name} — not yet in Rekordbox",
        "",
        f"Checked the {checked} most recently added tracks against "
        f"`{reference_path}` ({reference_rows} rows). "
        f"Generated {datetime.now().isoformat(timespec='seconds')}.",
        "",
        f"**{len(missing)} missing** of {checked} checked "
        f"(fuzzy match, threshold {threshold:.2f}).",
    ]
    if duplicates_dropped:
        lines.append(f"{duplicates_dropped} duplicate track(s) collapsed before comparing.")
    lines.append("")

    if not missing:
        lines.append("Nothing missing — every track checked is already in the reference.")
    else:
        for track in missing:
            lines.append(f"* {track['artists']} — {track['name']}  _(added {track['added_at'][:10]})_")
        lines.extend(
            [
                "",
                "## Closest near-misses",
                "",
                "Sanity check for borderline calls — if a near-miss looks like the same "
                "record, it's a fuzzy-match false negative, not a genuinely missing track.",
                "",
            ]
        )
        for track in missing:
            row = track["best_row"]
            near = f'"{row["title"]}" — {row["artist"]}' if row else "(no reference rows)"
            lines.append(f"* {track['name']} → {near} _(score {track['best_score']:.2f})_")

    out_path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("playlist", help="Playlist name (case-insensitive)")
    parser.add_argument("--count", type=int, default=30, help="How many tracks (default 30)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory")
    parser.add_argument(
        "--reference",
        type=Path,
        help="Rekordbox text export to cross-reference against; also writes <slug>-missing.md",
    )
    parser.add_argument(
        "--match-threshold",
        type=float,
        default=DEFAULT_MATCH_THRESHOLD,
        help=f"Confidence bar for 'already in reference' (default {DEFAULT_MATCH_THRESHOLD})",
    )
    args = parser.parse_args()

    if not args.out_dir.exists():
        raise SystemExit(f"Output directory not found: {args.out_dir} (is the drive mounted?)")

    # Parse the reference before authenticating, so a bad path or unreadable
    # file fails immediately instead of after a browser round-trip.
    reference, malformed = ([], [])
    if args.reference:
        if not args.reference.exists():
            raise SystemExit(f"Reference file not found: {args.reference}")
        reference, malformed = parse_reference(args.reference)

    sp = get_client(READ_SCOPE)
    playlist = find_playlist(sp, args.playlist)
    tracks = sort_by_added_at(fetch_playlist_tracks(sp, playlist["id"]))
    subset = tracks[: args.count]

    if not subset:
        raise SystemExit(f'Playlist "{playlist["name"]}" has no tracks with an added_at date.')

    slug = playlist["name"].strip().lower().replace(" ", "-")
    out_path = args.out_dir / f"{slug}-tracks.md"

    newest = subset[0]["added_at"]
    oldest = subset[-1]["added_at"]
    lines = [
        f'# {playlist["name"]} — {len(subset)} most recently added tracks',
        "",
        f"Playlist: `{playlist['uri']}` ({len(tracks)} tracks total). "
        f"Generated {datetime.now().isoformat(timespec='seconds')}.",
        f"Date range in this export: {oldest} to {newest}.",
        "",
    ]
    lines.extend(format_line(track) for track in subset)
    out_path.write_text("\n".join(lines) + "\n")

    print(f"Wrote {len(subset)} tracks to {out_path}")
    print(f"Newest: {subset[0]['name']} ({newest})")
    print(f"Oldest in export: {subset[-1]['name']} ({oldest})")

    if not args.reference:
        return

    unique = dedupe(subset)
    duplicates_dropped = len(subset) - len(unique)
    missing = find_missing(unique, reference, args.match_threshold)
    missing_path = args.out_dir / f"{slug}-missing.md"
    write_missing_report(
        missing_path,
        playlist["name"],
        args.reference,
        missing,
        checked=len(unique),
        reference_rows=len(reference),
        duplicates_dropped=duplicates_dropped,
        threshold=args.match_threshold,
    )

    print(f"\nCross-referenced against {args.reference} ({len(reference)} rows)")
    if duplicates_dropped:
        print(f"Collapsed {duplicates_dropped} duplicate track(s) before comparing")
    if malformed:
        print(f"Skipped {len(malformed)} malformed reference row(s) (fewer than 3 columns)")
    print(f"Missing from reference: {len(missing)} of {len(unique)} checked")
    print(f"Wrote {missing_path}")


if __name__ == "__main__":
    main()
