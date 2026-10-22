# spotify-tools

Small scripts against the real Spotify Web API (via [spotipy](https://spotipy.readthedocs.io/)),
for things the `claude.ai Spotify` connector can't do: real `added_at` ordering, and writing to
playlists (reordering, adding tracks).

## Setup

1. `.venv` already created (Python 3.12). Install deps: `.venv/bin/pip install -r requirements.txt`.
2. Create a free app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard),
   add redirect URI `http://127.0.0.1:8888/callback` (must be a loopback IP, not `localhost`).
3. Copy `.env.example` to `.env`, fill in the client ID/secret. Never commit `.env` or
   `.spotify-cache`.
4. First run of any script opens a browser to authorize. The token is cached in `.spotify-cache`
   next to the scripts, scoped to whatever that script requested — if a later script needs a
   broader scope (e.g. write access), it re-prompts automatically.

## Scripts

### `tunes.py` — export recently-added tracks

```
.venv/bin/python tunes.py <playlist-name> [--count 30] [--out-dir "/Volumes/Extreme SSD"]
                          [--reference FILE] [--match-threshold 0.78]
```

Writes `<playlist-slug>-tracks.md` to the given directory (default: Extreme SSD), overwritten each
run, listing the N most recently added tracks by real `added_at` timestamp — something the
Spotify app's UI doesn't surface directly for arbitrary playlists. Invoked via `/tunes
<playlist-name>` as a Claude Code skill. Read-only.

**`--reference FILE`** additionally cross-references those N tracks against a Rekordbox text export
(same format as `reorder_by_reference.py` uses) and writes `<playlist-slug>-missing.md` — the
tracks that aren't in Rekordbox yet, i.e. what still needs pulling for the set.

- Only the N exported tracks are checked, not the whole playlist — the question it answers is
  "what's new since I last built this crate?", not "what do I not own?".
- Duplicates are collapsed first, keyed on normalized title+artist rather than Spotify URI, so the
  same record appearing as both a single and a compilation cut counts once.
- Threshold is 0.78, higher than the reorder path's 0.72: a false positive here silently drops a
  track off the to-buy list, which is worse than listing one you already own.
- The report ends with a **closest near-misses** section — each missing track's best reference
  match and score, so a borderline call is visible rather than buried. Scores just under threshold
  are worth an eyeball.

```
.venv/bin/python tunes.py "S26" --reference "/Volumes/Extreme SSD/S26.txt"
```

### `reorder_by_reference.py` — reorder a playlist to match an external track list

```
.venv/bin/python reorder_by_reference.py <playlist-name> <reference-file> [--add-missing] [--apply] [--show-order]
```

Use case: you have a track list from somewhere else (DJ software export, a session's tracklist,
etc.) in a specific order, and want the Spotify playlist to match that order — without deleting
anything that's in the playlist but not in the reference list.

- **Reference file format**: tab-separated, header row + one row per track with columns `#`,
  `Track Title`, `Artist`, ... (extra columns ignored). This is what Rekordbox exports as a text
  playlist. Encoding is sniffed — Rekordbox writes UTF-16 LE, but a file that's been through an
  editor may come back as UTF-8 and is handled too.
- **Matching**: fuzzy title+artist matching (70/30 weighted), normalizing away generic descriptors
  like "(Original Mix)"/"(Radio Edit)" that Spotify and DJ software apply inconsistently, while
  keeping genuine remix credits intact. Artist acts as a **gate**, not just a 30% contributor:
  below 0.35 artist similarity the score is capped beneath every threshold, because stripping
  "(Original Mix)" can make two unrelated records with the same one-word title score a perfect
  1.0 on title alone. Threshold 0.72 for reorder-matching (conservative — a missed match just gets
  skipped, no harm done).
- **Reordering**: matched tracks are moved to the front of the playlist in reference-file order;
  everything else in the playlist stays, appended after, in its existing relative order. Nothing
  is ever removed.
- **`--add-missing`**: before reordering, searches Spotify's catalog for reference rows that
  didn't match anything already in the playlist, and adds confident matches (threshold 0.82 —
  higher than reorder-matching, since this writes new content rather than just skipping). Rows
  with an empty/corrupted Artist field in the source file are flagged and skipped rather than
  guessed at.
- **Default is a dry run** — always run without `--apply` first and read the report. Add
  `--show-order` to see the full final track order before committing. Pass `--apply` to actually
  write to Spotify.

Example workflow:
```
# 1. See what would happen
.venv/bin/python reorder_by_reference.py "S26" ~/Desktop/S26.txt --add-missing --show-order

# 2. Do it
.venv/bin/python reorder_by_reference.py "S26" ~/Desktop/S26.txt --add-missing --apply
```

### `create_playlist.py`: new playlist from a track list

```
.venv/bin/python create_playlist.py <new-playlist-name> <track-list-file> [--public] [--description TEXT] [--apply]
```

Searches Spotify for each line of a plain-text list and creates a new playlist (private by
default) holding the confident matches, in list order.

- **Line formats**: `Artist - Title` (en/em dash or tab also work), `Title - Artist`, or this repo's
  own `* Artist Title` with no separator. Bullets and `1.` / `01)` numbering are stripped.
- **Matching** uses the same scoring and artist gate as above, threshold 0.82. Lines without a
  confident match are listed with their closest candidate and score; repeated tracks are added once.
- Refuses to run if a playlist with that name already exists. **Default is a dry run**; pass
  `--apply` to create it.

```
.venv/bin/python create_playlist.py "Friday picks" ~/Desktop/picks.txt
.venv/bin/python create_playlist.py "Friday picks" ~/Desktop/picks.txt --apply
```

Tests (offline): `.venv/bin/python -m unittest -v`

## `spotify_common.py`

Shared by the scripts above: OAuth client setup, playlist lookup by name, track fetching, catalog search, and the
fuzzy matching used to reconcile external track lists against Spotify's metadata. Not a
standalone tool — import from it if you're writing a third script here.

## Gotchas

See [CLAUDE.md](CLAUDE.md) for hard-won implementation gotchas (Spotify API quirks, DJ-export data
corruption patterns) — worth reading before modifying the matching or reorder logic.
