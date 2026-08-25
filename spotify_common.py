"""Shared Spotify Web API helpers for tunes.py and reorder_by_reference.py.

Auth, playlist lookup, track fetching, reference-file parsing, and fuzzy
title/artist matching used by more than one script live here so each script
only holds its own logic.
"""

from __future__ import annotations

import codecs
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
    """Fetch a playlist's tracks in current order as plain
    uri/name/artists/added_at dicts.

    `added_at` lives on the playlist *item*, not the track, and is None for
    playlists that don't carry it — callers that sort or filter by recency
    must drop the Nones themselves (see sort_by_added_at)."""
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
                    "added_at": item.get("added_at"),
                }
            )
        results = sp.next(results) if results.get("next") else None
    return items


def sort_by_added_at(tracks: list[dict]) -> list[dict]:
    """Newest-first, dropping tracks with no added_at (they can't be ranked)."""
    dated = [t for t in tracks if t.get("added_at")]
    return sorted(dated, key=lambda t: t["added_at"], reverse=True)


# --- Reference file parsing (Rekordbox text playlist exports) ---


def read_reference_text(path: Path) -> str:
    """Decode a DJ-software text export.

    Rekordbox writes UTF-16 LE with a BOM, but exports that have been through
    an editor or another tool can come back as UTF-8 — hardcoding one encoding
    makes the other fail with an opaque UnicodeDecodeError, so sniff instead.
    """
    raw = path.read_bytes()
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if raw.startswith(bom):
            return raw.decode(encoding)
    # No BOM. Interleaved NUL bytes mean UTF-16; NUL is legal UTF-8, so this
    # check has to come before trying UTF-8 or a UTF-16 file decodes to mojibake.
    if b"\x00" in raw[:4096]:
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse_reference(path: Path) -> tuple[list[dict], list[str]]:
    """Parse a tab-separated Rekordbox export into rows of pos/title/artist.

    Returns (rows, malformed) — malformed holds raw lines with fewer than the
    three required columns rather than dropping them silently, since that's
    the signature of the corrupted-row problem noted in CLAUDE.md.
    """
    text = read_reference_text(path)
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise SystemExit(f"{path} is empty.")
    rows: list[dict] = []
    malformed: list[str] = []
    for line in lines[1:]:  # skip header
        cols = line.split("\t")
        if len(cols) < 3:
            malformed.append(line)
            continue
        rows.append({"pos": cols[0], "title": cols[1], "artist": cols[2]})
    if not rows:
        raise SystemExit(
            f"{path} has no parseable rows — expected tab-separated columns "
            f'"#", "Track Title", "Artist". Is it a Rekordbox text export?'
        )
    return rows, malformed


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

# Tokens too common to count as evidence that two artist strings refer to the
# same act ("The Mountain People" vs "The Others" share only "the").
ARTIST_STOPWORDS = frozenset({"the", "and", "feat", "ft", "vs", "with", "presents", "pres"})

# Below this artist similarity, a match is treated as unconfident no matter how
# well the titles agree, and capped beneath every caller's threshold.
ARTIST_FLOOR = 0.35
ARTIST_MISMATCH_CAP = 0.5


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


def artist_similarity(ref_artist: str, track_artists: str) -> float:
    """How much two artist strings agree, 0.0-1.0.

    Plain character similarity punishes the common case where one source
    credits a subset of the other ("Fabe" vs "Fabe (Ger), Roman Flügel"), so
    take the better of character similarity and token containment: if every
    meaningful token of the shorter credit appears in the longer, that's a
    match regardless of how much extra the longer one carries.
    """
    ref, trk = normalize(ref_artist), normalize(track_artists)
    if not ref or not trk:
        return 0.0
    char_ratio = difflib.SequenceMatcher(None, ref, trk).ratio()
    ref_tokens = {t for t in ref.split() if t not in ARTIST_STOPWORDS}
    trk_tokens = {t for t in trk.split() if t not in ARTIST_STOPWORDS}
    if not ref_tokens or not trk_tokens:
        return char_ratio
    containment = len(ref_tokens & trk_tokens) / min(len(ref_tokens), len(trk_tokens))
    return max(char_ratio, containment)


def match_score(ref_title: str, ref_artist: str, track: dict) -> float:
    """Score how well a reference (title, artist) matches a Spotify track
    dict (must have "name" and "artists" keys). 0.0-1.0, weighted 70% title
    / 30% artist since titles are more reliable identifiers than artist
    strings (which vary in ordering/formatting across sources).

    The weighting alone lets a title carry a match on its own: normalize_title
    strips "(Original Mix)", so Rekordbox's "closer (original mix)" by Pinto
    (NYC) scored an exact 1.0 title against Spotify's "Closer" by Eric OS and
    cleared threshold at 0.775 on a totally unrelated artist. Generic
    one-word titles are common enough in this genre that artist has to be a
    gate, not just a 30% contributor — so a score with a contradictory artist
    is capped below every caller's threshold.
    """
    ref_t, trk_t = normalize_title(ref_title), normalize_title(track["name"])
    title_ratio = difflib.SequenceMatcher(None, ref_t, trk_t).ratio()
    if len(ref_t) >= 4 and (ref_t in trk_t or trk_t in ref_t):
        title_ratio = max(title_ratio, 0.9)
    artist_ratio = artist_similarity(ref_artist, track["artists"])
    score = 0.7 * title_ratio + 0.3 * artist_ratio
    if artist_ratio < ARTIST_FLOOR:
        return min(score, ARTIST_MISMATCH_CAP)
    return score
