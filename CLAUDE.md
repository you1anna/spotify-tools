# spotify-tools

Scripts against the real Spotify Web API (via `spotipy`), because the `claude.ai Spotify` MCP
connector only exposes an AI-curated/generative view of playlists — no `added_at`, no reliable
ordering, no write access.

See [README.md](README.md) for setup and usage. This file is repo-specific conventions and
gotchas for Claude Code sessions working in this repo.

## Layout
- `spotify_common.py` — shared auth, playlist lookup, track fetching, fuzzy title/artist matching.
  Both scripts below import from it; don't duplicate this logic back into a script.
- `tunes.py` — export a playlist's most-recently-added tracks to markdown (read-only).
- `reorder_by_reference.py` — reorder a playlist to match an external reference track list, and
  optionally add tracks that are missing (read+write).

## Conventions
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
