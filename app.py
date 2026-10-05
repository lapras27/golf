import hmac
import json
import math
import os
import sqlite3

import pandas as pd
import streamlit as st
from openai import APIError, OpenAI
from streamlit.errors import StreamlitSecretNotFoundError

from golf_tracker import (
    add_player,
    build_local_report,
    delete_round,
    get_all_scores,
    get_round_scores,
    initialize_database,
    list_players,
    list_rounds,
    save_round,
    summarize_players,
)


st.set_page_config(page_title="Golf Together", page_icon="⛳", layout="wide")


def configured_value(key, default=""):
    value = os.getenv(key, "")
    if value:
        return value
    try:
        return st.secrets.get(key, default)
    except StreamlitSecretNotFoundError:
        return default


def build_radar_chart(metrics):
    radius = 1
    angles = [
        -math.pi / 2 + index * 2 * math.pi / len(metrics)
        for index in range(len(metrics))
    ]
    rows = []
    score_points = []
    for level in (20, 40, 60, 80, 100):
        for index in range(len(metrics) + 1):
            angle = angles[index % len(metrics)]
            rows.append(
                {
                    "kind": "grid",
                    "group": f"grid-{level}",
                    "order": index,
                    "x": radius * level / 100 * math.cos(angle),
                    "y": radius * level / 100 * math.sin(angle),
                }
            )
    for index, angle in enumerate(angles):
        rows.append(
            {
                "kind": "axis",
                "group": f"axis-{index}",
                "x": 0,
                "y": 0,
                "x2": radius * math.cos(angle),
                "y2": radius * math.sin(angle),
            }
        )
        metric = metrics[index]
        rows.append(
            {
                "kind": "label",
                "x": radius * 1.2 * math.cos(angle),
                "y": radius * 1.2 * math.sin(angle),
                "label": metric["label"],
            }
        )
        score_point = {
            "kind": "score",
            "group": "score",
            "order": index,
            "x": radius * metric["score"] / 100 * math.cos(angle),
            "y": radius * metric["score"] / 100 * math.sin(angle),
            "label": metric["label"],
            "value": metric["score"],
        }
        score_points.append(score_point)
        rows.append(score_point)
    rows.append({**score_points[0], "order": len(metrics)})
    chart = {
        "width": 420,
        "height": 420,
        "layer": [
            {
                "transform": [{"filter": "datum.kind === 'grid'"}],
                "mark": {"type": "line", "color": "#98a2b3", "opacity": 0.5},
                "encoding": {
                    "detail": {"field": "group", "type": "nominal"},
                    "order": {"field": "order", "type": "quantitative"},
                },
            },
            {
                "transform": [{"filter": "datum.kind === 'axis'"}],
                "mark": {"type": "rule", "color": "#98a2b3", "opacity": 0.5},
                "encoding": {
                    "x2": {"field": "x2", "type": "quantitative"},
                    "y2": {"field": "y2", "type": "quantitative"},
                },
            },
            {
                "transform": [{"filter": "datum.kind === 'score'"}],
                "mark": {
                    "type": "line",
                    "color": "#2563eb",
                    "strokeWidth": 3,
                    "point": {"filled": True, "size": 65},
                },
                "encoding": {
                    "detail": {"field": "group", "type": "nominal"},
                    "order": {"field": "order", "type": "quantitative"},
                    "tooltip": [
                        {"field": "label", "type": "nominal", "title": "指標"},
                        {"field": "value", "type": "quantitative", "title": "スコア"},
                    ],
                },
            },
            {
                "transform": [{"filter": "datum.kind === 'label'"}],
                "mark": {
                    "type": "text",
                    "fontSize": 13,
                    "fontWeight": "bold",
                },
                "encoding": {"text": {"field": "label", "type": "nominal"}},
            },
        ],
        "encoding": {
            "x": {
                "field": "x",
                "type": "quantitative",
                "scale": {"domain": [-1.35, 1.35]},
                "axis": None,
            },
            "y": {
                "field": "y",
                "type": "quantitative",
                "scale": {"domain": [-1.35, 1.35]},
                "axis": None,
            },
        },
        "config": {"view": {"stroke": None}},
    }
    return pd.DataFrame(rows), chart


def render_login():
    password = configured_value("GOLF_APP_PASSWORD")
    if not password:
        st.error(
            "アプリのパスワードが未設定です。環境変数 GOLF_APP_PASSWORD "
            "または Streamlit secrets に設定してください。"
        )
        st.stop()

    st.title("⛳ Golf Together")
    st.caption("友達とラウンドを記録して、成長を振り返ろう。")
    with st.form("login"):
        entered = st.text_input("共有パスワード", type="password")
        submitted = st.form_submit_button("ログイン", type="primary")
    if submitted:
        if hmac.compare_digest(entered.encode("utf-8"), password.encode("utf-8")):
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("パスワードが違います。")
    st.stop()


if not st.session_state.get("authenticated"):
    render_login()

initialize_database()

with st.sidebar:
    st.title("⛳ Golf Together")
    st.caption("仲間とのラウンドを記録")
    if st.button("ログアウト"):
        st.session_state["authenticated"] = False
        st.rerun()
    st.divider()
    st.subheader("プレイヤーを追加")
    with st.form("add_player", clear_on_submit=True):
        new_player = st.text_input("ニックネーム")
        add_submitted = st.form_submit_button("追加")
    if add_submitted:
        try:
            add_player(new_player)
        except ValueError as error:
            st.error(str(error))
        except sqlite3.IntegrityError:
            st.error("同じ名前のプレイヤーがすでに登録されています。")
        except sqlite3.Error as error:
            st.error(f"プレイヤーを追加できませんでした: {error}")
        else:
            st.success(f"{new_player.strip()} を追加しました。")
            st.rerun()

players = list_players()
tabs = st.tabs(["スコア入力", "ラウンド一覧", "成長・AI分析", "個人分析"])

with tabs[0]:
    st.header("ラウンドを記録")
    if not players:
        st.info("左側のメニューから、プレイヤーを1人以上追加してください。")
    else:
        selected_names = st.multiselect(
            "今回の参加者",
            [player["name"] for player in players],
            default=[player["name"] for player in players],
            help="各ホールのスコアを入力する参加者を選びます。",
        )
        selected_players = [p for p in players if p["name"] in selected_names]
        if not selected_players:
            st.info("スコアを記録する参加者を選択してください。")
        else:
            all_pars = [4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 5, 4, 4, 3, 4, 5]
            round_format = st.radio(
                "ラウンド形式",
                ["18ホール", "前半9ホール（1〜9番）", "後半9ホール（10〜18番）"],
                horizontal=True,
            )
            hole_numbers = (
                list(range(1, 19))
                if round_format == "18ホール"
                else list(range(1, 10))
                if round_format.startswith("前半")
                else list(range(10, 19))
            )
            pars = [all_pars[hole - 1] for hole in hole_numbers]
            hole_count = len(hole_numbers)
            initial = {"ホール": hole_numbers, "パー": pars}
            for player in selected_players:
                initial[player["name"]] = pd.Series([None] * hole_count, dtype="Int64")
            with st.form("round_entry"):
                round_title = st.text_input("ラウンド名", placeholder="例: 秋のゴルフ会")
                first, second = st.columns(2)
                round_date = first.date_input("プレー日")
                course = second.text_input("ゴルフ場", placeholder="例: ○○カントリークラブ")
                st.caption(
                    f"{hole_count}ホール分のパーと各プレイヤーのスコアを入力してください"
                    "（スコアは1〜30）。"
                )
                score_table = st.data_editor(
                    pd.DataFrame(initial),
                    hide_index=True,
                    width="stretch",
                    num_rows="fixed",
                    column_config={
                        "ホール": st.column_config.NumberColumn("ホール", disabled=True),
                        "パー": st.column_config.NumberColumn(
                            "パー", min_value=3, max_value=6, step=1
                        ),
                        **{
                            player["name"]: st.column_config.NumberColumn(
                                player["name"], min_value=1, max_value=30, step=1
                            )
                            for player in selected_players
                        },
                    },
                )
                save_submitted = st.form_submit_button("ラウンドを保存", type="primary")
            if save_submitted:
                try:
                    if score_table["パー"].isna().any():
                        raise ValueError("全ホールのパーを入力してください。")
                    scores = {}
                    for player in selected_players:
                        column = score_table[player["name"]]
                        if column.isna().any():
                            raise ValueError(
                                f"{player['name']} の{hole_count}ホール分のスコアを入力してください。"
                            )
                        scores[player["id"]] = column.tolist()
                    round_id = save_round(
                        round_date,
                        round_title,
                        course,
                        [player["id"] for player in selected_players],
                        score_table["パー"].tolist(),
                        scores,
                        hole_numbers=hole_numbers,
                    )
                except (ValueError, TypeError) as error:
                    st.error(str(error))
                except sqlite3.Error as error:
                    st.error(f"ラウンドを保存できませんでした: {error}")
                else:
                    st.success(f"ラウンドを保存しました（#{round_id}）。")

with tabs[1]:
    st.header("ラウンド一覧")
    rounds = list_rounds()
    if not rounds:
        st.info("まだラウンドがありません。最初のスコアを記録しましょう。")
    else:
        round_labels = {
            item["id"]: (
                f"{item['played_on']}  |  {item['title']}  |  {item['course']} "
                f"({item['hole_count']}H・{item['player_count']}人)"
            )
            for item in rounds
        }
        selected_round_id = st.selectbox(
            "ラウンドを選択",
            list(round_labels),
            format_func=lambda round_id: round_labels[round_id],
        )
        rows = get_round_scores(selected_round_id)
        if rows:
            meta = rows[0]
            st.subheader(f"{meta['title']} — {meta['course']}")
            st.caption(f"{meta['played_on']}・{len({row['hole'] for row in rows})}ホール")
            detail = pd.DataFrame(rows)
            table = detail.pivot(index="hole", columns="player", values="strokes")
            table.index.name = "ホール"
            table.index = table.index.map(str)
            table.columns.name = None
            table["パー"] = detail.drop_duplicates("hole").set_index("hole")["par"]
            total_row = table.sum(numeric_only=True).to_frame().T
            total_row.index = ["合計"]
            st.dataframe(pd.concat([table, total_row]), width="stretch")
            with st.expander("このラウンドを削除"):
                st.warning("削除したラウンドと全員のスコアは元に戻せません。")
                if st.button("ラウンドを削除", key=f"delete_{selected_round_id}"):
                    try:
                        delete_round(selected_round_id)
                    except sqlite3.Error as error:
                        st.error(f"ラウンドを削除できませんでした: {error}")
                    else:
                        st.success("ラウンドを削除しました。")
                        st.rerun()

with tabs[2]:
    st.header("成長・AI分析")
    all_scores = get_all_scores()
    summaries = summarize_players(all_scores)
    if not summaries:
        st.info("9ホールまたは18ホールの記録が追加されると、ここに分析が表示されます。")
    else:
        st.caption(
            "9ホールと18ホールの記録を1ホール当たりで集計しています。"
            "平均スコア・推移は18ホール換算の単純換算値です。"
            "初心者〜上級者の目安は平均オーバーパーによる独自分類で、公式ハンディキャップではありません。"
        )
        overview = pd.DataFrame(
            [
                {
                    "プレイヤー": item["player"],
                    "記録数": item["round_count"],
                    "平均スコア": round(item["average_score"], 1),
                    "平均対パー": f"{item['average_over_par']:+.1f}",
                    "ベスト（18H換算）": round(item["best_score"], 1),
                    "直近平均との差": f"{item['improvement']:+.1f}",
                    "目安": item["level"],
                }
                for item in summaries
            ]
        )
        st.dataframe(overview, hide_index=True, width="stretch")

        chosen_names = st.multiselect(
            "推移を表示するプレイヤー",
            [item["player"] for item in summaries],
            default=[item["player"] for item in summaries],
        )
        chart_data = {}
        for item in summaries:
            if item["player"] in chosen_names:
                chart_data[item["player"]] = {
                    f"{round_['played_on']} #{index}": round_["equivalent_total"]
                    for index, round_ in enumerate(item["rounds"], start=1)
                }
        if chart_data:
            chart = pd.DataFrame.from_dict(chart_data, orient="index").T
            st.line_chart(chart, y_label="18ホール換算スコア")

        st.subheader("得意・課題ホール")
        hole_table = pd.DataFrame(
            [
                {
                    "プレイヤー": item["player"],
                    "得意なホール": f"{item['strongest_hole'][0]}番 "
                    f"({item['strongest_hole'][1]:+.2f}/h)",
                    "課題ホール": f"{item['weakest_hole'][0]}番 "
                    f"({item['weakest_hole'][1]:+.2f}/h)",
                }
                for item in summaries
            ]
        )
        st.dataframe(hole_table, hide_index=True, width="stretch")

        st.subheader("レポート")
        st.markdown(build_local_report(summaries))
        api_key = configured_value("OPENAI_API_KEY")
        if not api_key:
            st.info(
                "AIレポートを利用するには OPENAI_API_KEY を設定してください。"
                "設定がなくても上の統計レポートは利用できます。"
            )
        st.caption(
            "AIレポートを実行すると、選択したプレイヤー名と集計スコアがOpenAI APIへ送信されます。"
            "本名ではなくニックネームの利用をおすすめします。"
        )
        report_names = st.multiselect(
            "AI分析の対象",
            [item["player"] for item in summaries],
            default=[item["player"] for item in summaries],
            key="ai_players",
        )
        if st.button("AI成長レポートを作成", type="primary", disabled=not api_key):
            selected_summaries = [
                item for item in summaries if item["player"] in report_names
            ]
            if not selected_summaries:
                st.error("分析するプレイヤーを選んでください。")
            else:
                prompt_data = [
                    {
                        "player": item["player"],
                        "round_count": item["round_count"],
                        "recorded_holes": item["hole_count"],
                        "average_score": round(item["average_score"], 1),
                        "average_over_par": round(item["average_over_par"], 1),
                        "best_score": item["best_score"],
                        "improvement": round(item["improvement"], 1),
                        "strongest_hole": item["strongest_hole"][0],
                        "weakest_hole": item["weakest_hole"][0],
                        "level_estimate": item["level"],
                    }
                    for item in selected_summaries
                ]
                try:
                    client = OpenAI(api_key=api_key)
                    response = client.chat.completions.create(
                        model=configured_value("OPENAI_MODEL", "gpt-4o-mini"),
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "あなたは親しみやすいゴルフコーチです。日本語で、"
                                    "各人の変化、比較、得意・課題、具体的な練習案を簡潔に説明してください。"
                                    "平均スコアは9ホール記録を含む18ホール換算の単純な参考値だと説明してください。"
                                    "ラウンド数が少ない場合は断定を避け、レベル分類が公式ではないと明記してください。"
                                    "データからわからないことは推測しないでください。"
                                ),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(prompt_data, ensure_ascii=False),
                            },
                        ],
                    )
                    st.markdown(response.choices[0].message.content or "AIから本文が返りませんでした。")
                except APIError as error:
                    st.error(f"OpenAI APIの呼び出しに失敗しました: {error}")

with tabs[3]:
    st.header("プレイヤー別レーダー分析")
    if not summaries:
        st.info("9ホールまたは18ホールの記録が追加されると、個人分析を表示できます。")
    else:
        radar_player = st.selectbox(
            "分析するプレイヤー",
            [item["player"] for item in summaries],
            key="radar_player",
        )
        radar_summary = next(
            item for item in summaries if item["player"] == radar_player
        )
        radar_metrics = radar_summary["radar_metrics"]
        radar_chart_column, radar_table_column = st.columns([1, 1])
        with radar_chart_column:
            if all(metric["score"] is not None for metric in radar_metrics):
                radar_data, radar_spec = build_radar_chart(radar_metrics)
                st.vega_lite_chart(
                    radar_data,
                    radar_spec,
                    width="stretch",
                    height=460,
                    key="player_radar_chart",
                )
            else:
                missing_metrics = [
                    metric["label"]
                    for metric in radar_metrics
                    if metric["score"] is None
                ]
                st.info(
                    f"{', '.join(missing_metrics)} のデータがないため、"
                    "記録が増えるとレーダーチャートを表示できます。"
                )
        with radar_table_column:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "指標": metric["label"],
                            "スコア": (
                                f"{metric['score']:.0f}/100"
                                if metric["score"] is not None
                                else "—"
                            ),
                            "実績": metric["detail"],
                            "対象ホール数": metric["sample_count"],
                        }
                        for metric in radar_metrics
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        st.caption(
            "総合スコアとPar別指標は平均対パー差から算出し、安定性は"
            "各ホールの対パー差のばらつきが小さいほど高くなる独自の0〜100点です。"
            "ショットやパット等の記録ではなく、記録済みスコアだけから見る参考値です。"
        )
