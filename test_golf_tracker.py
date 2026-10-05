import tempfile
import unittest
from pathlib import Path
from sqlite3 import IntegrityError

from golf_tracker import (
    add_player,
    build_ai_comparison_data,
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

    def test_ai_comparison_data_compares_only_selected_players(self):
        summaries = [
            {
                "player": "Aki",
                "round_count": 2,
                "hole_count": 27,
                "average_score": 90.2,
                "average_over_par": 18.2,
                "best_score": 84,
                "improvement": 3.4,
                "strongest_hole": (3, 0.5),
                "weakest_hole": (8, 2.0),
                "radar_metrics": [
                    {
                        "label": "Par 3",
                        "score": 62.5,
                        "detail": "平均 +0.50打/ホール",
                        "sample_count": 4,
                    },
                    {
                        "label": "Par 4",
                        "score": 45,
                        "detail": "平均 +1.20打/ホール",
                        "sample_count": 10,
                    },
                ],
            },
            {
                "player": "Mina",
                "round_count": 1,
                "hole_count": 18,
                "average_score": 96.7,
                "average_over_par": 24.7,
                "best_score": 96.7,
                "improvement": -1.2,
                "strongest_hole": (5, 0.0),
                "weakest_hole": (7, 2.5),
                "radar_metrics": [
                    {
                        "label": "Par 3",
                        "score": 50,
                        "detail": "平均 +1.00打/ホール",
                        "sample_count": 2,
                    },
                    {
                        "label": "Par 4",
                        "score": 37.5,
                        "detail": "平均 +1.50打/ホール",
                        "sample_count": 8,
                    },
                ],
            },
            {
                "player": "Unselected",
                "round_count": 1,
                "hole_count": 18,
                "average_score": 80,
                "average_over_par": 8,
                "best_score": 80,
                "improvement": 4,
                "strongest_hole": (1, -1),
                "weakest_hole": (2, 1),
                "radar_metrics": [],
            },
        ]

        report_data = build_ai_comparison_data(summaries[:2])

        self.assertEqual(
            [player["player"] for player in report_data["players"]],
            ["Aki", "Mina"],
        )
        self.assertEqual(len(report_data["pairwise_comparisons"]), 1)
        comparison = report_data["pairwise_comparisons"][0]
        self.assertEqual(comparison["first_minus_second_18_hole_average"], -6.5)
        self.assertEqual(comparison["first_minus_second_average_over_par"], -6.5)
        self.assertEqual(comparison["first_minus_second_improvement"], 4.6)
        self.assertEqual(report_data["players"][0]["radar_metrics"]["Par 3"]["score"], 62.5)
        self.assertEqual(
            comparison["first_minus_second_radar_scores"]["Par 3"], 12.5
        )

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
