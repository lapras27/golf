import os
import sqlite3
import statistics
from collections import defaultdict
from contextlib import contextmanager
from datetime import date
from pathlib import Path


DEFAULT_DATABASE_PATH = Path("data") / "golf_scores.sqlite3"


def _as_integer(value, message):
    try:
        converted = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(message) from error
    if isinstance(value, bool) or converted != value:
        raise ValueError(message)
    return converted


def database_path():
    return Path(os.getenv("GOLF_DB_PATH", str(DEFAULT_DATABASE_PATH)))


@contextmanager
def connect(database=None):
    path = Path(database) if database is not None else database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(database=None):
    with connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY,
                played_on TEXT NOT NULL,
                title TEXT NOT NULL,
                course TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS scores (
                round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
                player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                hole INTEGER NOT NULL CHECK (hole BETWEEN 1 AND 18),
                par INTEGER NOT NULL CHECK (par BETWEEN 3 AND 6),
                strokes INTEGER NOT NULL CHECK (strokes BETWEEN 1 AND 30),
                PRIMARY KEY (round_id, player_id, hole)
            );
            """
        )


def list_players(database=None):
    with connect(database) as connection:
        rows = connection.execute(
            "SELECT id, name FROM players ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(row) for row in rows]


def add_player(name, database=None):
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("プレイヤー名を入力してください。")
    with connect(database) as connection:
        cursor = connection.execute(
            "INSERT INTO players (name) VALUES (?)", (clean_name,)
        )
        return cursor.lastrowid


def save_round(
    played_on,
    title,
    course,
    player_ids,
    pars,
    player_scores,
    database=None,
    hole_numbers=None,
):
    clean_title = title.strip()
    clean_course = course.strip()
    if not clean_title or not clean_course:
        raise ValueError("ラウンド名とゴルフ場を入力してください。")
    selected_holes = (
        list(range(1, 19))
        if hole_numbers is None
        else [
            _as_integer(hole, "ホール番号は1〜18で指定してください。")
            for hole in hole_numbers
        ]
    )
    if len(selected_holes) not in (9, 18) or len(set(selected_holes)) != len(
        selected_holes
    ):
        raise ValueError("ラウンドは9ホールまたは18ホールで記録してください。")
    if any(not 1 <= hole <= 18 for hole in selected_holes):
        raise ValueError("ホール番号は1〜18で指定してください。")
    if len(selected_holes) == 18 and selected_holes != list(range(1, 19)):
        raise ValueError("18ホールは1番から18番まで指定してください。")
    if len(selected_holes) == 9 and selected_holes not in (
        list(range(1, 10)),
        list(range(10, 19)),
    ):
        raise ValueError("ハーフラウンドは前半9ホールまたは後半9ホールを指定してください。")
    if len(pars) != len(selected_holes):
        raise ValueError("各ホールのパーは3〜6で入力してください。")
    validated_pars = [
        _as_integer(par, "各ホールのパーは3〜6で入力してください。")
        for par in pars
    ]
    if any(not 3 <= par <= 6 for par in validated_pars):
        raise ValueError("各ホールのパーは3〜6で入力してください。")
    if not player_ids or len(set(player_ids)) != len(player_ids):
        raise ValueError("参加プレイヤーを1人以上選択してください。")
    if set(player_scores) != set(player_ids):
        raise ValueError("参加者全員のスコアを入力してください。")
    validated_scores = {}
    for scores in player_scores.values():
        if len(scores) != len(selected_holes):
            raise ValueError(f"{len(selected_holes)}ホール分のスコアを入力してください。")
    for player_id, scores in player_scores.items():
        parsed_scores = [
            _as_integer(score, "各ホールのスコアは1〜30で入力してください。")
            for score in scores
        ]
        if any(not 1 <= score <= 30 for score in parsed_scores):
            raise ValueError("各ホールのスコアは1〜30で入力してください。")
        validated_scores[player_id] = parsed_scores

    with connect(database) as connection:
        round_cursor = connection.execute(
            "INSERT INTO rounds (played_on, title, course) VALUES (?, ?, ?)",
            (date.fromisoformat(str(played_on)).isoformat(), clean_title, clean_course),
        )
        round_id = round_cursor.lastrowid
        known_players = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM players WHERE id IN ({})".format(
                    ",".join("?" for _ in player_ids)
                ),
                tuple(player_ids),
            )
        }
        if known_players != set(player_ids):
            raise ValueError("選択したプレイヤーが見つかりません。")
        connection.executemany(
            """
            INSERT INTO scores (round_id, player_id, hole, par, strokes)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (round_id, player_id, hole, par, strokes)
                for player_id in player_ids
                for hole, par, strokes in zip(
                    selected_holes, validated_pars, validated_scores[player_id]
                )
            ],
        )
    return round_id


def list_rounds(database=None):
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT r.id, r.played_on, r.title, r.course,
                   COUNT(DISTINCT s.player_id) AS player_count,
                   COUNT(DISTINCT s.hole) AS hole_count
            FROM rounds r
            LEFT JOIN scores s ON s.round_id = r.id
            GROUP BY r.id
            ORDER BY r.played_on DESC, r.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_round_scores(round_id, database=None):
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT r.played_on, r.title, r.course, p.name AS player,
                   s.hole, s.par, s.strokes
            FROM scores s
            JOIN rounds r ON r.id = s.round_id
            JOIN players p ON p.id = s.player_id
            WHERE r.id = ?
            ORDER BY p.name COLLATE NOCASE, s.hole
            """,
            (round_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_scores(database=None):
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT r.id AS round_id, r.played_on, r.title, r.course,
                   p.id AS player_id, p.name AS player,
                   s.hole, s.par, s.strokes
            FROM scores s
            JOIN rounds r ON r.id = s.round_id
            JOIN players p ON p.id = s.player_id
            ORDER BY r.played_on, r.id, p.name COLLATE NOCASE, s.hole
            """
        ).fetchall()
    return [dict(row) for row in rows]


def delete_round(round_id, database=None):
    with connect(database) as connection:
        connection.execute("DELETE FROM rounds WHERE id = ?", (round_id,))


def summarize_players(score_rows):
    rounds_by_player = defaultdict(dict)
    for row in score_rows:
        key = (row["player_id"], row["player"])
        rounds_by_player[key][
            row["round_id"],
            row["played_on"],
            row["title"],
            row["course"],
            row["hole"],
        ] = row

    summaries = []
    for (player_id, name), round_holes in rounds_by_player.items():
        rounds = defaultdict(list)
        for (round_id, played_on, title, course, hole), row in round_holes.items():
            rounds[round_id].append(
                {
                    "played_on": played_on,
                    "title": title,
                    "course": course,
                    "hole": hole,
                    "par": row["par"],
                    "strokes": row["strokes"],
                }
            )

        completed = []
        for holes in rounds.values():
            holes.sort(key=lambda item: item["hole"])
            if len(holes) in (9, 18):
                hole_count = len(holes)
                total = sum(item["strokes"] for item in holes)
                completed.append(
                    {
                        "played_on": holes[0]["played_on"],
                        "title": holes[0]["title"],
                        "course": holes[0]["course"],
                        "total": total,
                        "hole_count": hole_count,
                        "equivalent_total": total / hole_count * 18,
                        "par": sum(item["par"] for item in holes),
                        "by_hole": holes,
                    }
                )
        completed.sort(key=lambda item: item["played_on"])
        if not completed:
            continue

        deltas = [
            hole["strokes"] - hole["par"]
            for round_ in completed
            for hole in round_["by_hole"]
        ]
        average_over_par_per_hole = sum(deltas) / len(deltas)
        average_strokes_per_hole = sum(
            hole["strokes"]
            for round_ in completed
            for hole in round_["by_hole"]
        ) / len(deltas)
        hole_deltas_by_number = defaultdict(list)
        deltas_by_par = {3: [], 4: [], 5: []}
        for round_ in completed:
            for hole in round_["by_hole"]:
                delta = hole["strokes"] - hole["par"]
                hole_deltas_by_number[hole["hole"]].append(delta)
                if hole["par"] in deltas_by_par:
                    deltas_by_par[hole["par"]].append(delta)
        hole_deltas = [
            (hole_number, sum(values) / len(values))
            for hole_number, values in hole_deltas_by_number.items()
        ]
        average_over_par = average_over_par_per_hole * 18
        if average_over_par < 0:
            level = "上級者（アンダーパー平均）"
        elif average_over_par < 18:
            level = "上級者"
        elif average_over_par < 36:
            level = "中級者"
        else:
            level = "初心者"

        recent = completed[-3:]
        earliest = completed[:3]
        recent_average = sum(item["equivalent_total"] for item in recent) / len(recent)
        early_average = sum(item["equivalent_total"] for item in earliest) / len(earliest)
        radar_metrics = [
            {
                "label": "総合スコア",
                "score": max(0, min(100, 75 - 25 * average_over_par_per_hole)),
                "detail": f"平均 {average_over_par_per_hole:+.2f}打/ホール",
                "sample_count": len(deltas),
            }
        ]
        for par in (3, 4, 5):
            par_deltas = deltas_by_par[par]
            average_delta = sum(par_deltas) / len(par_deltas) if par_deltas else None
            radar_metrics.append(
                {
                    "label": f"Par {par}",
                    "score": (
                        max(0, min(100, 75 - 25 * average_delta))
                        if average_delta is not None
                        else None
                    ),
                    "detail": (
                        f"平均 {average_delta:+.2f}打/ホール"
                        if average_delta is not None
                        else "記録なし"
                    ),
                    "sample_count": len(par_deltas),
                }
            )
        scoring_spread = statistics.pstdev(deltas)
        radar_metrics.append(
            {
                "label": "安定性",
                "score": max(0, min(100, 100 - 25 * scoring_spread)),
                "detail": f"対パー差のばらつき {scoring_spread:.2f}打",
                "sample_count": len(deltas),
            }
        )
        summaries.append(
            {
                "player_id": player_id,
                "player": name,
                "round_count": len(completed),
                "hole_count": len(deltas),
                "average_score": average_strokes_per_hole * 18,
                "average_over_par": average_over_par,
                "best_score": min(item["equivalent_total"] for item in completed),
                "recent_average": recent_average,
                "early_average": early_average,
                "improvement": early_average - recent_average,
                "strongest_hole": min(hole_deltas, key=lambda item: item[1]),
                "weakest_hole": max(hole_deltas, key=lambda item: item[1]),
                "radar_metrics": radar_metrics,
                "level": level,
                "rounds": completed,
            }
        )
    return sorted(summaries, key=lambda item: item["average_score"])


def build_local_report(summaries):
    if not summaries:
        return "分析できる9ホールまたは18ホールのスコアがまだありません。"
    lines = []
    for item in summaries:
        trend = (
            f"初期平均より直近平均が{item['improvement']:.1f}打改善"
            if item["improvement"] > 0
            else f"初期平均より直近平均が{abs(item['improvement']):.1f}打増加"
            if item["improvement"] < 0
            else "初期と直近の平均は同じ"
        )
        lines.append(
            f"**{item['player']}**：18ホール換算平均 {item['average_score']:.1f}打 "
            f"(平均 {item['average_over_par']:+.1f}), {item['level']}。"
            f"{trend}。得意は{item['strongest_hole'][0]}番、"
            f"課題は{item['weakest_hole'][0]}番です。"
        )
    if len(summaries) > 1:
        top = summaries[0]
        bottom = summaries[-1]
        lines.append(
            f"登録スコアの平均では **{top['player']}** が最少 "
            f"({top['average_score']:.1f}打)、**{bottom['player']}** が"
            f"{bottom['average_score']:.1f}打です。コースやパーの違いも考慮して参考にしてください。"
        )
    return "\n\n".join(lines)


def build_ai_comparison_data(summaries):
    players = [
        {
            "player": item["player"],
            "round_count": item["round_count"],
            "recorded_holes": item["hole_count"],
            "average_score_18_hole_equivalent": round(item["average_score"], 1),
            "average_over_par_18_hole_equivalent": round(
                item["average_over_par"], 1
            ),
            "best_score_18_hole_equivalent": round(item["best_score"], 1),
            "early_to_recent_improvement": round(item["improvement"], 1),
            "strongest_hole": item["strongest_hole"][0],
            "strongest_hole_average_over_par": round(
                item["strongest_hole"][1], 2
            ),
            "weakest_hole": item["weakest_hole"][0],
            "weakest_hole_average_over_par": round(
                item["weakest_hole"][1], 2
            ),
            "radar_metrics": {
                metric["label"]: {
                    "score": (
                        round(metric["score"], 1)
                        if metric["score"] is not None
                        else None
                    ),
                    "detail": metric["detail"],
                    "sample_count": metric["sample_count"],
                }
                for metric in item["radar_metrics"]
            },
        }
        for item in summaries
    ]
    comparisons = []
    for index, first in enumerate(players):
        for second in players[index + 1 :]:
            comparisons.append(
                {
                    "players": [first["player"], second["player"]],
                    "first_minus_second_18_hole_average": round(
                        first["average_score_18_hole_equivalent"]
                        - second["average_score_18_hole_equivalent"],
                        1,
                    ),
                    "first_minus_second_average_over_par": round(
                        first["average_over_par_18_hole_equivalent"]
                        - second["average_over_par_18_hole_equivalent"],
                        1,
                    ),
                    "first_minus_second_improvement": round(
                        first["early_to_recent_improvement"]
                        - second["early_to_recent_improvement"],
                        1,
                    ),
                    "first_minus_second_radar_scores": {
                        label: round(
                            first["radar_metrics"][label]["score"]
                            - second["radar_metrics"][label]["score"],
                            1,
                        )
                        if first["radar_metrics"][label]["score"] is not None
                        and second["radar_metrics"][label]["score"] is not None
                        else None
                        for label in first["radar_metrics"]
                        if label in second["radar_metrics"]
                    },
                }
            )
    return {"players": players, "pairwise_comparisons": comparisons}
