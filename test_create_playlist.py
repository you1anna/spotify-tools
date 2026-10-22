"""Offline tests for create_playlist.py and the shared catalog search.

Run: .venv/bin/python -m unittest -v
"""

from __future__ import annotations

import unittest

import spotipy

from create_playlist import ADD_THRESHOLD, parse_track_list, resolve, score_entry
from reorder_by_reference import find_missing_on_spotify
from spotify_common import match_unsplit


def item(uri: str, name: str, *artists: str) -> dict:
    return {"uri": uri, "name": name, "artists": [{"name": a} for a in artists]}


def track(name: str, artists: str) -> dict:
    return {"uri": "spotify:track:x", "name": name, "artists": artists}


class FakeSpotify:
    """Returns canned results keyed on the exact search query."""

    def __init__(self, results: dict[str, list[dict]], fail: set[str] = frozenset()):
        self.results = results
        self.fail = fail
        self.queries: list[str] = []

    def search(self, q: str, type: str, limit: int) -> dict:
        self.queries.append(q)
        if q in self.fail:
            raise spotipy.SpotifyException(500, -1, "server error")
        return {"tracks": {"items": self.results.get(q, [])}}


class ParseTrackListTest(unittest.TestCase):
    def test_separators_bullets_and_numbering(self):
        text = "1. Eric OS - Closer\n\n* Fabe – Ruins\n02) Kerri Chandler\tRain\n- Pinto Closer\n"
        entries = parse_track_list(text)
        self.assertEqual(
            [(e["line"], e["halves"]) for e in entries],
            [
                ("Eric OS - Closer", ("Eric OS", "Closer")),
                ("Fabe – Ruins", ("Fabe", "Ruins")),
                ("Kerri Chandler\tRain", ("Kerri Chandler", "Rain")),
                ("Pinto Closer", None),
            ],
        )

    def test_hyphenated_names_are_not_split(self):
        self.assertIsNone(parse_track_list("Jay-Z Ninety-Nine")[0]["halves"])

    def test_leading_number_without_punctuation_kept(self):
        self.assertEqual(parse_track_list("808 State Pacific")[0]["line"], "808 State Pacific")


class ScoringTest(unittest.TestCase):
    def test_either_orientation_matches(self):
        t = track("Closer", "Eric OS")
        for line in ("Eric OS - Closer", "Closer - Eric OS"):
            self.assertGreaterEqual(score_entry(parse_track_list(line)[0], t), ADD_THRESHOLD, line)

    def test_unsplit_line_matches_on_credited_artist(self):
        self.assertGreaterEqual(match_unsplit("Eric OS Closer", track("Closer", "Eric OS")), ADD_THRESHOLD)
        self.assertGreaterEqual(
            match_unsplit("Fabe Ruins", track("Ruins", "Fabe (Ger), Roman Flügel")), ADD_THRESHOLD
        )

    def test_same_title_different_artist_rejected(self):
        t = track("Closer", "Eric OS")
        self.assertLess(match_unsplit("Pinto Closer", t), ADD_THRESHOLD)
        self.assertLess(score_entry(parse_track_list("Pinto - Closer")[0], t), ADD_THRESHOLD)

    def test_unsplit_line_that_is_only_the_artist_scores_zero(self):
        self.assertEqual(match_unsplit("Eric OS", track("Closer", "Eric OS")), 0.0)


class ResolveTest(unittest.TestCase):
    def test_found_duplicates_not_found_and_failed(self):
        sp = FakeSpotify(
            {
                "Eric OS Closer": [item("spotify:track:pinto", "Closer", "Pinto"), item("spotify:track:eric", "Closer", "Eric OS")],
                "Closer Eric OS": [item("spotify:track:eric", "Closer", "Eric OS")],
                "Nobody Nothing": [item("spotify:track:other", "Something Else", "Someone")],
            },
            fail={"Broken Line"},
        )
        entries = parse_track_list("Eric OS - Closer\nCloser - Eric OS\nNobody - Nothing\nEmpty Result\nBroken - Line\n")
        found, duplicates, not_found, failed = resolve(sp, entries)

        self.assertEqual([r["track"]["uri"] for r in found], ["spotify:track:eric"])
        self.assertEqual([r["line"] for r in duplicates], ["Closer - Eric OS"])
        self.assertEqual([r["line"] for r in not_found], ["Nobody - Nothing", "Empty Result"])
        self.assertEqual(not_found[0]["track"]["name"], "Something Else")
        self.assertIsNone(not_found[1]["track"])
        self.assertEqual([r["line"] for r in failed], ["Broken - Line"])


class ReorderAddMissingTest(unittest.TestCase):
    """find_missing_on_spotify now goes through search_catalog; its behaviour
    must be unchanged."""

    def test_found_missing_and_skipped(self):
        sp = FakeSpotify(
            {"Closer Eric OS": [item("spotify:track:pinto", "Closer", "Pinto"), item("spotify:track:eric", "Closer", "Eric OS")]},
            fail={"Rain Kerri Chandler"},
        )
        rows = [
            {"pos": "1", "title": "Closer", "artist": "Eric OS"},
            {"pos": "2", "title": "Ruins", "artist": ""},
            {"pos": "3", "title": "Rain", "artist": "Kerri Chandler"},
            {"pos": "4", "title": "Gone", "artist": "Nobody"},
        ]
        found, missing = find_missing_on_spotify(sp, rows)
        self.assertEqual(len(found), 1)
        self.assertEqual(
            {k: found[0][k] for k in ("uri", "name", "artists")},
            {"uri": "spotify:track:eric", "name": "Closer", "artists": "Eric OS"},
        )
        self.assertIs(found[0]["row"], rows[0])
        self.assertEqual([r["pos"] for r in missing], ["2", "3", "4"])
        self.assertNotIn("Ruins ", sp.queries)


if __name__ == "__main__":
    unittest.main()
