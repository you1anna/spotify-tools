# spotify-tools

Scripts against the real Spotify Web API (via `spotipy`), because the `claude.ai Spotify` MCP
connector only exposes an AI-curated/generative view of playlists — no `added_at`, no reliable
ordering, no write access.

See [README.md](README.md) for setup and usage. This repository is public. Before a commit, obey the "Public repository"
rules in [AGENTS.md](AGENTS.md#public-repository). This file is repo-specific conventions and
gotchas for Claude Code sessions working in this repo.

## Layout
- `spotify_common.py` — shared auth, playlist lookup, track fetching, reference-file parsing,
  fuzzy title/artist matching, catalog search. The scripts below import from it; don't duplicate this logic back
  into a script.
- `tunes.py` — export a playlist's most-recently-added tracks to markdown, optionally
  cross-referencing them against a Rekordbox export to list what's not in Rekordbox yet
  (`--reference`). Read-only.
- `reorder_by_reference.py` — reorder a playlist to match an external reference track list, and
  optionally add tracks that are missing (read+write).
- `create_playlist.py` — create a new playlist from a plain-text track list, adding only confident
  catalog matches (read+write). Tests: `test_create_playlist.py` (offline, fake client).

## Conventions
- **Track lines are `* <artist> <title>` — a plain space, never a dash or em dash.** These lists
  get pasted straight into search boxes (Soulseek etc.), and a separator has to be hand-deleted
  every time. `tunes.py`'s `format_line()` is the single place this is rendered; every report it
  writes goes through it, so the exported and missing lists can't drift apart. Don't reintroduce a
  separator for readability.
- Read-only scripts request `READ_SCOPE` only; anything that writes to a playlist must request
  `WRITE_SCOPE` too. Scope is additive and cached in `.spotify-cache` — requesting a broader scope
  than what's cached triggers a one-time re-auth browser prompt automatically, no manual cache
  clearing needed.
- Every write action (reorder, add-to-playlist) defaults to a dry run; require an explicit
  `--apply` flag. Print the full plan before writing.
- Never commit `.env` or `.spotify-cache` (both gitignored) — they hold live credentials/tokens.

## Gotchas (hard-won, don't re-discover these)
- `playlist_items` nests the track object under `"item"` for some playlist types (e.g. the legacy
  "Starred" playlist) instead of the documented `"track"` key — use `get_track_from_item()` from
  `spotify_common.py`, which checks both.
- Don't pass a restrictive `fields=` param to `playlist_items`: it broke silently against the
  `"item"` key above and returned zero tracks with no error.
- `current_user_playlists` playlist objects for "Starred" lack a `tracks.total` field that normal
  playlists have — compute track count from the fetched items instead.
- **`playlist_reorder_items`'s `insert_before` is indexed against the playlist *before* the moved
  range is removed**, not the item's final resting position. Moving an item forward (to a higher
  index) needs `insert_before = target_index + 1` to land it correctly; moving backward needs
  `insert_before = target_index` directly. See the docstring on `compute_moves()` in
  `reorder_by_reference.py`.
- A live/shared playlist can be edited concurrently (another device, the Spotify app, a
  collaborator) while a script runs. `reorder_by_reference.py`'s dry-run always re-fetches fresh
  state, so it self-corrects on the next run — but don't assume a single dry-run snapshot stays
  valid if you pause before applying.
- DJ-software text exports (e.g. Rekordbox) can have corrupted rows: an unbalanced parenthesis in
  the title can swallow the tab delimiter and leave the Artist column empty, or a garbled Artist
  field (e.g. a mislabeled compilation credit) that won't fuzzy-match even when the track is
  genuinely present. `reorder_by_reference.py` reports these separately (empty-artist vs
  no-confident-match) rather than silently dropping them — worth a manual look, not a script fix.
- **Artist is a gate in `match_score`, not just the 30% term.** `normalize_title` strips
  "(Original Mix)", so Rekordbox's `closer (original mix)` by Pinto (NYC) scored an exact 1.0
  title against Spotify's `Closer` by Eric OS and cleared threshold at 0.775 — two unrelated
  records. One-word titles are common enough in this genre that title can't be allowed to carry a
  match alone, hence `ARTIST_FLOOR` (0.35) capping the score at `ARTIST_MISMATCH_CAP` (0.5),
  below every caller's threshold. Don't "simplify" this back to a pure weighted sum.
- `artist_similarity` takes the **max of character similarity and token containment**, so a
  credit that's a subset of the other ("Fabe" vs "Fabe (Ger), Roman Flügel") still matches —
  raw `SequenceMatcher` scores that pair at 0.24 and would trip the artist floor. Stopwords
  ("the", "feat", …) are excluded from containment so `The Mountain People` vs `The Others`
  doesn't match on "the" alone.
- `fetch_playlist_tracks` returns `added_at` (it lives on the playlist *item*, not the track, and
  can be None). Use it + `sort_by_added_at` rather than writing another paginating fetch —
  `tunes.py` used to carry a near-duplicate one purely because the shared helper dropped the field.
- Don't hardcode `encoding="utf-16"` when reading a reference file — use `read_reference_text`,
  which sniffs BOM then NUL-interleaving. NUL is *legal UTF-8*, so a naive "try UTF-8 first"
  fallback decodes a UTF-16 file to mojibake instead of raising.
- `parse_reference` returns `(rows, malformed)`, not a bare list — malformed rows are surfaced,
  not silently skipped. Both callers print the count.
- Unsplit track lines (`* <artist> <title>`) can't be divided into artist and title on their
  own, so `match_unsplit` takes the boundary from each search candidate's credited artists. A
  candidate whose artist isn't at the start or end of the line scores 0, which keeps the same
  artist gate as `match_score`.
