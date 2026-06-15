# -*- coding: utf-8 -*-
"""
レース内相対・構造特徴量（feature_spec.md 5.5）
=========================================================================
着順は「相手との比較」で決まる。各レース（6 艇）内で相対量を算出する。

  - class_gap            : 自分の級別 − レース内最高級別（直接）
  - winrate_rank_in_race : レース内 全国勝率の順位（1=最上位）（直接）
  - winrate_diff_top     : レース内最高勝率 − 自分の全国勝率（直接）
  - motor_rank_in_race   : レース内 モーター2連率の順位（直接）
  - st_rank_in_race      : レース内 平均ST の順位（小さいほど良 → 1=最良）（履歴）
  - field_strength_std   : レース内 全国勝率の母標準偏差（拮抗 or 一強）（直接）

リーク防止:
  - 直接系は B-file（出走表）の印字値のみ。出走表発行時点で確定（4 章 2 項）。
  - st_rank_in_race は features_player_history.py が出した as-of 済み st_avg を使う。
  → 本モジュールは「レース内での横断集約」だけで、未来情報には触れない。

入力:
  - B-file CSV（data/b*.csv）       … 級別 / 全国勝率 / モーター2率 + ラインナップ
  - 履歴特徴 CSV（features_player_history.csv）… st_avg（as-of）
出力:
  - (race_id, 枠番) のロング形式 CSV。

使い方:
  py -3 features_race_relative.py
  py -3 features_race_relative.py --hist features_player_history.csv --out features_race_relative.csv
"""

import argparse
import csv
import glob
import statistics
from collections import defaultdict

from features_player_history import VENUE_CODE, file_date_key, date_from_key

CLASS_ORD = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}


def to_float(s):
    try:
        return float(str(s).strip())
    except (ValueError, AttributeError):
        return None


def rank_desc(values, x):
    """大きいほど上位。x の順位（1=最上位）。同値は同順位（競技式）。None は対象外。"""
    if x is None:
        return None
    return 1 + sum(1 for v in values if v is not None and v > x)


def rank_asc(values, x):
    """小さいほど上位（ST 用）。x の順位（1=最良）。None は対象外。"""
    if x is None:
        return None
    return 1 + sum(1 for v in values if v is not None and v < x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--b-glob", default="data/b*.csv", help="B-file CSV のグロブ")
    ap.add_argument("--hist", default="features_player_history.csv",
                    help="履歴特徴 CSV（st_avg を持つ）")
    ap.add_argument("--start", default=None, help="開始 YYMMDD（含む）")
    ap.add_argument("--end", default=None, help="終了 YYMMDD（含む）")
    ap.add_argument("--out", default="features_race_relative.csv", help="出力 CSV パス")
    args = ap.parse_args()

    # ── st_avg / 進入挙動 を (race_id, 枠) で引けるようにする ──────────
    st_avg_by = {}
    maezuke_by = {}   # (race_id,枠) -> (maezuke_rate, course_n)
    try:
        with open(args.hist, encoding="cp932") as f:
            for r in csv.DictReader(f):
                key = (r["race_id"], r["枠番"])
                st_avg_by[key] = to_float(r["st_avg"])
                maezuke_by[key] = (to_float(r.get("maezuke_rate")),
                                   to_float(r.get("course_n")))
    except FileNotFoundError:
        print(f"! 履歴 CSV が無いので st_rank_in_race 等は空になります: {args.hist}")

    # ── B-file を読み、レースごとに 6 艇をまとめる ───────────────────
    bpaths = sorted(glob.glob(args.b_glob), key=file_date_key)
    if args.start:
        bpaths = [p for p in bpaths if file_date_key(p) >= args.start]
    if args.end:
        bpaths = [p for p in bpaths if file_date_key(p) <= args.end]
    if not bpaths:
        print("× B-file CSV が見つかりません:", args.b_glob)
        return

    races = defaultdict(list)   # race_id -> [entry, ...]
    race_meta = {}
    for path in bpaths:
        d = date_from_key(file_date_key(path))
        with open(path, encoding="cp932") as f:
            for r in csv.DictReader(f):
                venue = r["会場"]
                code = VENUE_CODE.get(venue, "00")
                race_no = int(r["レース"])
                race_id = f"{code}{d:%Y%m%d}{race_no:02d}"
                waku = str(int(r["艇番"]))
                entry = {
                    "race_id": race_id,
                    "枠番": waku,
                    "登番": r["登番"],
                    "選手名": r["選手名"],
                    "class_ord": CLASS_ORD.get(r["級別"].strip()),
                    "win_rate_national": to_float(r["全国勝率"]),
                    "motor_top2_rate": to_float(r["モーター2率"]),
                    "st_avg": st_avg_by.get((race_id, waku)),
                    # B-file 印字値（公式の長期集計＝母数大・リークなし）。
                    "top2_rate_national": to_float(r.get("全国2率")),
                    "win_rate_local": to_float(r.get("当地勝率")),
                    "top2_rate_local": to_float(r.get("当地2率")),
                    "boat_top2_rate": to_float(r.get("ボート2率")),
                    "weight": to_float(r.get("体重")),
                    "age": to_float(r.get("年齢")),
                    "maezuke_rate": maezuke_by.get((race_id, waku), (None, None))[0],
                    "course_n": maezuke_by.get((race_id, waku), (None, None))[1],
                }
                races[race_id].append(entry)
                race_meta[race_id] = (f"{d:%Y-%m-%d}", code, venue, race_no)

    # ── レース内で相対量を算出 ───────────────────────────────────────
    out_rows = []
    for race_id in sorted(races):
        ents = races[race_id]
        classes = [e["class_ord"] for e in ents]
        winrates = [e["win_rate_national"] for e in ents]
        motors = [e["motor_top2_rate"] for e in ents]
        sts = [e["st_avg"] for e in ents]

        max_class = max((c for c in classes if c is not None), default=None)
        max_win = max((w for w in winrates if w is not None), default=None)
        win_vals = [w for w in winrates if w is not None]
        field_std = statistics.pstdev(win_vals) if len(win_vals) >= 2 else None

        # 5.4 field_maezuke_flag: 枠2以上に前づけ常習者（maezuke_rate>=閾値, 母数十分）が
        # いれば隊形が崩れやすい＝予測難度が上がる、というレース単位の警戒シグナル。
        # 枠1は前づけ不可なので判定対象外。
        MZ_TH, MZ_MIN_N = 0.15, 30
        mz_cands = [e["maezuke_rate"] for e in ents
                    if int(e["枠番"]) >= 2 and e["maezuke_rate"] is not None
                    and e["course_n"] is not None and e["course_n"] >= MZ_MIN_N]
        maezuke_max = max(mz_cands) if mz_cands else None
        field_maezuke_flag = 1 if (maezuke_max is not None and maezuke_max >= MZ_TH) else 0

        date_str, code, venue, race_no = race_meta[race_id]
        for e in ents:
            cg = (e["class_ord"] - max_class) if (e["class_ord"] is not None and max_class is not None) else None
            wdt = (max_win - e["win_rate_national"]) if (max_win is not None and e["win_rate_national"] is not None) else None
            out_rows.append({
                "race_id": race_id,
                "枠番": e["枠番"],
                "日付": date_str,
                "場コード": code,
                "会場": venue,
                "レース": race_no,
                "登番": e["登番"],
                "選手名": e["選手名"],
                "class_ord": e["class_ord"],
                "class_gap": cg,
                "win_rate_national": e["win_rate_national"],
                "winrate_rank_in_race": rank_desc(winrates, e["win_rate_national"]),
                "winrate_diff_top": wdt,
                "motor_top2_rate": e["motor_top2_rate"],
                "motor_rank_in_race": rank_desc(motors, e["motor_top2_rate"]),
                "st_avg": e["st_avg"],
                "st_rank_in_race": rank_asc(sts, e["st_avg"]),
                "field_strength_std": field_std,
                "top2_rate_national": e["top2_rate_national"],
                "win_rate_local": e["win_rate_local"],
                "top2_rate_local": e["top2_rate_local"],
                "boat_top2_rate": e["boat_top2_rate"],
                "weight": e["weight"],
                "age": e["age"],
                "maezuke_rate": e["maezuke_rate"],
                "field_maezuke_flag": field_maezuke_flag,
                "maezuke_max": maezuke_max,
            })

    if not out_rows:
        print("× 出力行がありません。")
        return

    def fmt(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.4f}"
        return v

    fieldnames = list(out_rows[0].keys())
    with open(args.out, "w", encoding="cp932", newline="", errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in out_rows:
            w.writerow({k: fmt(v) for k, v in row.items()})

    races_n = len({r["race_id"] for r in out_rows})
    print(f"○ 出力: {args.out}")
    print(f"  レース {races_n} / 行 {len(out_rows)}")
    print(f"  期間 {out_rows[0]['日付']} 〜 {out_rows[-1]['日付']}")


if __name__ == "__main__":
    main()
