#!/usr/bin/env python3
"""Reorder a Spotify playlist's tracks to match the order of a reference file.

Tracks in the playlist that aren't in the reference file are left in place,
appended after the matched/reordered tracks (nothing is removed). Reference
rows with no match in the playlist are reported and skipped, unless
--add-missing is used to search Spotify's catalog for them first.

Usage:
    reorder_by_reference.py <playlist-name> <reference-file> [--add-missing] [--apply] [--show-order]

Without --apply, prints a dry-run report only. Requires write scope
(playlist-modify-public/private) in addition to the read scope tunes.py
uses — first run under this scope will re-prompt for authorization.

Reference file format: tab-separated, header row + one row per track with
columns "#", "Track Title", "Artist", ... (extra columns are ignored). This is
the format Rekordbox exports as a text playlist. Encoding is sniffed (Rekordbox
writes UTF-16 LE; UTF-8 is accepted too).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import spotipy

from spotify_common import (
    READ_SCOPE,
    WRITE_SCOPE,
    fetch_playlist_tracks,
    find_playlist,
    get_client,
    match_score,
    parse_reference,
)

MATCH_THRESHOLD = 0.72
ADD_THRESHOLD = 0.82  # higher bar than reorder-matching: this writes new tracks, so fewer false positives


def find_missing_on_spotify(sp: spotipy.Spotify, unmatched_reference: list[dict]) -> tuple[list[dict], list[dict]]:
    """For each unmatched reference row, search Spotify's catalog for a confident
    match. Returns (found, still_missing) where each `found` entry has the
    original row plus a `track` dict (uri/name/artists) and `score`."""
    found: list[dict] = []
    still_missing: list[dict] = []
    for row in unmatched_reference:
        if not row["artist"].strip():
            # Artist field is empty/corrupted for these rows (source data
            # issue) — too risky to search on title alone, skip.
            still_missing.append(row)
            continue
        query = f'{row["title"]} {row["artist"]}'
        try:
            results = sp.search(q=query, type="track", limit=5)
        except Exception:
            still_missing.append(row)
            continue
        candidates = results.get("tracks", {}).get("items", [])
        best_track = None
        best_score = 0.0
        for track in candidates:
            candidate = {"name": track["name"], "artists": ", ".join(a["name"] for a in track["artists"])}
            score = match_score(row["title"], row["artist"], candidate)
            if score > best_score:
                best_score = score
                best_track = track
        if best_track is not None and best_score >= ADD_THRESHOLD:
            found.append(
                {
                    "row": row,
                    "score": best_score,
                    "uri": best_track["uri"],
                    "name": best_track["name"],
                    "artists": ", ".join(a["name"] for a in best_track["artists"]),
                }
            )
        else:
            still_missing.append(row)
    return found, still_missing


def build_target_order(reference: list[dict], playlist_tracks: list[dict]) -> tuple[list[int], list[dict]]:
    """Returns (target order as indices into playlist_tracks, unmatched reference rows)."""
    unmatched_indices = set(range(len(playlist_tracks)))
    target_order: list[int] = []
    unmatched_reference: list[dict] = []

    for row in reference:
        best_idx = None
        best_score = 0.0
        for idx in unmatched_indices:
            score = match_score(row["title"], row["artist"], playlist_tracks[idx])
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is not None and best_score >= MATCH_THRESHOLD:
            target_order.append(best_idx)
            unmatched_indices.discard(best_idx)
        else:
            unmatched_reference.append(row)

    # Tracks in the playlist not found in the reference file: keep at the end,
    # preserving their existing relative order.
    leftover = sorted(unmatched_indices)
    target_order.extend(leftover)
    return target_order, unmatched_reference


def compute_moves(target_order: list[int]) -> list[tuple[int, int]]:
    """Simulate moving items one at a time to reach target_order; returns
    (range_start, insert_before) pairs to feed to playlist_reorder_items,
    each moving a single item, in sequence.

    Spotify's insert_before is indexed against the playlist *before* the
    range is removed, not the item's final resting position. For a forward
    move (cur_pos < target_pos) that means insert_before = target_pos + 1:
    removing the item first shifts everything after it left by one, so
    "insert before pre-removal index target_pos + 1" lands the item at
    post-removal index target_pos. For a backward move, no shift applies
    and insert_before = target_pos directly. (In practice this algorithm
    always produces backward-or-equal moves since it fills target
    positions left to right, but the forward case is handled for
    correctness if that ever changes.)
    """
    current = list(range(len(target_order)))  # current[i] = original index at position i
    moves: list[tuple[int, int]] = []
    for target_pos, original_idx in enumerate(target_order):
        cur_pos = current.index(original_idx)
        if cur_pos == target_pos:
            continue
        insert_before = target_pos + 1 if cur_pos < target_pos else target_pos
        moves.append((cur_pos, insert_before))
        item = current.pop(cur_pos)
        current.insert(target_pos, item)
    return moves


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("playlist", help="Playlist name (case-insensitive)")
    parser.add_argument(
        "reference", type=Path, help="Rekordbox text export (tab-separated; encoding sniffed)"
    )
    parser.add_argument("--apply", action="store_true", help="Actually reorder (default: dry run)")
    parser.add_argument("--show-order", action="store_true", help="Print the full planned final order")
    parser.add_argument(
        "--add-missing",
        action="store_true",
        help="Search Spotify for reference rows not in the playlist and add confident matches",
    )
    args = parser.parse_args()

    reference, malformed = parse_reference(args.reference)
    if malformed:
        print(f"Skipped {len(malformed)} malformed reference row(s) (fewer than 3 columns):")
        for line in malformed:
            print(f"  ? {line[:100]}")
        print()

    scope = f"{READ_SCOPE} {WRITE_SCOPE}"
    sp = get_client(scope)
    playlist = find_playlist(sp, args.playlist)
    playlist_tracks = fetch_playlist_tracks(sp, playlist["id"])

    if args.add_missing:
        _, unmatched_reference = build_target_order(reference, playlist_tracks)
        to_add, still_missing = find_missing_on_spotify(sp, unmatched_reference)

        print(f"Found on Spotify but not in playlist ({len(to_add)}):")
        for entry in to_add:
            print(
                f'  + {entry["name"]} — {entry["artists"]}  '
                f'(matched "{entry["row"]["title"]}" — {entry["row"]["artist"]}, score {entry["score"]:.2f})'
            )
        if still_missing:
            print(f"\nStill not found / skipped ({len(still_missing)}):")
            for row in still_missing:
                reason = "empty artist field in source" if not row["artist"].strip() else "no confident match"
                print(f'  - {row["title"]} — {row["artist"]}  ({reason})')

        if args.apply and to_add:
            print(f"\nAdding {len(to_add)} tracks to the playlist...")
            uris = [entry["uri"] for entry in to_add]
            for i in range(0, len(uris), 100):
                sp.playlist_add_items(playlist["id"], uris[i : i + 100])
            playlist_tracks = fetch_playlist_tracks(sp, playlist["id"])
        elif to_add:
            print("\nDry run only — pass --apply to actually add these and reorder.")

    target_order, unmatched_reference = build_target_order(reference, playlist_tracks)
    moves = compute_moves(target_order)

    print(f'Playlist: "{playlist["name"]}" ({len(playlist_tracks)} tracks)')
    print(f"Reference: {args.reference} ({len(reference)} rows)")
    print(f"Matched: {len(reference) - len(unmatched_reference)} / {len(reference)}")
    print(f"Moves required: {len(moves)}")

    if unmatched_reference:
        print(f"\nReference rows with no match in playlist (skipped, {len(unmatched_reference)}):")
        for row in unmatched_reference:
            print(f'  - {row["title"]} — {row["artist"]}')

    matched_count = len(reference) - len(unmatched_reference)
    trailing = target_order[matched_count:]
    if trailing:
        print(f"\nPlaylist tracks not in reference file (kept, appended at end, {len(trailing)}):")
        for idx in trailing:
            t = playlist_tracks[idx]
            print(f'  - {t["name"]} — {t["artists"]}')

    if args.show_order:
        print(f"\nFull planned order ({len(target_order)} tracks):")
        for pos, idx in enumerate(target_order, start=1):
            t = playlist_tracks[idx]
            tag = "" if pos <= matched_count else "  [unmatched, kept in place]"
            print(f'  {pos:>3}. {t["name"]} — {t["artists"]}{tag}')

    if not args.apply:
        print("\nDry run only — pass --apply to write this order to Spotify.")
        return

    print(f"\nApplying {len(moves)} moves...")
    snapshot_id = None
    for range_start, insert_before in moves:
        kwargs = {"range_length": 1}
        if snapshot_id:
            kwargs["snapshot_id"] = snapshot_id
        result = sp.playlist_reorder_items(playlist["id"], range_start, insert_before, **kwargs)
        snapshot_id = result.get("snapshot_id")
    print("Done.")


if __name__ == "__main__":
    main()
