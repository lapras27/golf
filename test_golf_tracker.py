import tempfile
import unittest
from pathlib import Path
from sqlite3 import IntegrityError

from golf_tracker import (
    add_player,
    delete_round,
    get_all_scores,
    get_round_scores,
    initialize_database,
    list_players,
    list_rounds,
    save_round,
    summarize_players,
)


class GolfTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "scores.sqlite3"
        initialize_database(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_round_and_summarize_player(self):
        player_id = add_player("Aki", self.database)
        round_id = save_round(
            "2026-10-05",
            "秋のラウンド",
            "緑カントリー",
            [player_id],
            [4] * 18,
            {player_id: [5] * 18},
            self.database,
        )

        rounds = list_rounds(self.database)
        summaries = summarize_players(get_all_scores(self.database))

        self.assertEqual(rounds[0]["id"], round_id)
        self.assertEqual(rounds[0]["player_count"], 1)
        self.assertEqual(summaries[0]["average_score"], 90)
        self.assertEqual(summaries[0]["average_over_par"], 18)
        self.assertEqual(summaries[0]["level"], "中級者")

    def test_rejects_incomplete_scorecard(self):
        player_id = add_player("Aki", self.database)
        with self.assertRaisesRegex(ValueError, "18ホール"):
            save_round(
                "2026-10-05",
                "ラウンド",
                "ゴルフ場",
                [player_id],
                [4] * 18,
                {player_id: [5] * 17},
                self.database,
            )
        self.assertEqual(list_rounds(self.database), [])

    def test_saves_back_nine_and_includes_it_in_player_analysis(self):
        player_id = add_player("Aki", self.database)
        pars = [4, 4, 3, 4, 5, 4, 3, 4, 5]
        scores = [par + 1 for par in pars]
        round_id = save_round(
            "2026-10-05",
            "午後ハーフ",
            "緑カントリー",
            [player_id],
            pars,
            {player_id: scores},
            self.database,
            hole_numbers=range(10, 19),
        )

        self.assertEqual(list_rounds(self.database)[0]["hole_count"], 9)
        self.assertEqual(
            [row["hole"] for row in get_round_scores(round_id, self.database)],
            list(range(10, 19)),
        )
        summary = summarize_players(get_all_scores(self.database))[0]
        self.assertEqual(summary["round_count"], 1)
        self.assertEqual(summary["hole_count"], 9)
        self.assertEqual(summary["average_score"], 90)
        self.assertEqual(summary["average_over_par"], 18)
        self.assertEqual(len(summary["radar_metrics"]), 5)
        self.assertEqual(summary["radar_metrics"][1]["sample_count"], 2)
        self.assertEqual(summary["radar_metrics"][4]["score"], 100)

    def test_mixes_nine_and_eighteen_hole_rounds_by_hole_average(self):
        player_id = add_player("Aki", self.database)
        save_round(
            "2026-10-05",
            "ハーフ",
            "緑カントリー",
            [player_id],
            [4] * 9,
            {player_id: [5] * 9},
            self.database,
            hole_numbers=range(1, 10),
        )
        save_round(
            "2026-10-06",
            "通常ラウンド",
            "青カントリー",
            [player_id],
            [4] * 18,
            {player_id: [4] * 18},
            self.database,
        )

        summary = summarize_players(get_all_scores(self.database))[0]
        self.assertEqual(summary["round_count"], 2)
        self.assertEqual(summary["hole_count"], 27)
        self.assertAlmostEqual(summary["average_score"], 78)
        self.assertAlmostEqual(summary["average_over_par"], 6)

    def test_rejects_fractional_strokes_without_truncating(self):
        player_id = add_player("Aki", self.database)
        scores = [5] * 18
        scores[0] = 4.5
        with self.assertRaisesRegex(ValueError, "スコア"):
            save_round(
                "2026-10-05",
                "ラウンド",
                "ゴルフ場",
                [player_id],
                [4] * 18,
                {player_id: scores},
                self.database,
            )
        self.assertEqual(list_rounds(self.database), [])

    def test_player_names_are_case_insensitively_unique(self):
        add_player("Aki", self.database)
        with self.assertRaises(IntegrityError):
            add_player("aki", self.database)
        self.assertEqual(len(list_players(self.database)), 1)

    def test_deleting_round_removes_scores(self):
        player_id = add_player("Aki", self.database)
        round_id = save_round(
            "2026-10-05",
            "ラウンド",
            "ゴルフ場",
            [player_id],
            [4] * 18,
            {player_id: [4] * 18},
            self.database,
        )
        delete_round(round_id, self.database)
        self.assertEqual(get_all_scores(self.database), [])


if __name__ == "__main__":
    unittest.main()
