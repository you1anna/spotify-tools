"""Shared Spotify Web API helpers for tunes.py and reorder_by_reference.py.

Auth, playlist lookup, track fetching, and fuzzy title/artist matching used
by more than one script live here so each script only holds its own logic.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

SCRIPT_DIR = Path(__file__).resolve().parent

READ_SCOPE = "playlist-read-private playlist-read-collaborative"
WRITE_SCOPE = "playlist-modify-public playlist-modify-private"


def get_client(scope: str) -> spotipy.Spotify:
    """Authenticate with the given scope. If the cached token was granted a
    narrower scope, spotipy detects the mismatch and re-prompts in the
    browser automatically."""
    load_dotenv(SCRIPT_DIR / ".env")
    auth_manager = SpotifyOAuth(
        scope=scope,
        cache_path=str(SCRIPT_DIR / ".spotify-cache"),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def find_playlist(sp: spotipy.Spotify, name: str) -> dict:
    """Find a playlist by case-insensitive name. Prefers an exact match;
    falls back to substring match. Raises if none or multiple match."""
    target = name.strip().lower()
    exact_matches: list[dict] = []
    substring_matches: list[dict] = []
    offset = 0
    while True:
        page = sp.current_user_playlists(limit=50, offset=offset)
        for playlist in page["items"]:
            playlist_name = playlist["name"].strip().lower()
            if playlist_name == target:
                exact_matches.append(playlist)
            elif target in playlist_name:
                substring_matches.append(playlist)
        if page["next"] is None:
            break
        offset += 50

    matches = exact_matches or substring_matches
    if not matches:
        raise SystemExit(f'No playlist matching "{name}" found in your account.')
    if len(matches) > 1:
        names = ", ".join(f'"{m["name"]}"' for m in matches)
        raise SystemExit(f'Multiple playlists match "{name}": {names}. Be more specific.')
    return matches[0]


def get_track_from_item(item: dict) -> dict | None:
    """Spotify's response nests the track under "item" for some playlist
    types (e.g. legacy Starred) instead of the documented "track" key."""
    return item.get("track") or item.get("item")


def fetch_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict]:
    """Fetch a playlist's tracks in current order as plain uri/name/artists dicts."""
    items: list[dict] = []
    results = sp.playlist_items(playlist_id, additional_types=("track",), limit=100)
    while results:
        for item in results["items"]:
            track = get_track_from_item(item)
            if track is None or track.get("uri") is None:
                continue
            items.append(
                {
                    "uri": track["uri"],
                    "name": track["name"],
                    "artists": ", ".join(a["name"] for a in track["artists"]),
                }
            )
        results = sp.next(results) if results.get("next") else None
    return items


# --- Fuzzy title/artist matching, used to reconcile a reference track list
# against Spotify's metadata for the same tracks. ---

GENERIC_TITLE_SUFFIXES = (
    "original mix",
    "radio edit",
    "club mix",
    "extended mix",
    "vip mix",
    "instrumental mix",
)


def normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_title(s: str) -> str:
    """normalize(), plus stripping generic mix/edit descriptors that Spotify
    and DJ software apply inconsistently (kept remix credits, e.g. "Saoirse
    Remix", are left intact since they're genuinely distinguishing)."""
    n = normalize(s)
    for suffix in GENERIC_TITLE_SUFFIXES:
        n = n.replace(suffix, "")
    return re.sub(r"\s+", " ", n).strip()


def match_score(ref_title: str, ref_artist: str, track: dict) -> float:
    """Score how well a reference (title, artist) matches a Spotify track
    dict (must have "name" and "artists" keys). 0.0-1.0, weighted 70% title
    / 30% artist since titles are more reliable identifiers than artist
    strings (which vary in ordering/formatting across sources)."""
    ref_t, trk_t = normalize_title(ref_title), normalize_title(track["name"])
    title_ratio = difflib.SequenceMatcher(None, ref_t, trk_t).ratio()
    if len(ref_t) >= 4 and (ref_t in trk_t or trk_t in ref_t):
        title_ratio = max(title_ratio, 0.9)
    artist_ratio = difflib.SequenceMatcher(
        None, normalize(ref_artist), normalize(track["artists"])
    ).ratio()
    return 0.7 * title_ratio + 0.3 * artist_ratio
