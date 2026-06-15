# -*- coding: utf-8 -*-
"""
選手・履歴系特徴量（直前情報なしモデル）― as-of 逐次集計
=========================================================================
feature_spec.md 5.1 のうち「履歴」区分の特徴量を計算する。

  - lane_win_rate / lane_top3_rate : 枠番別（艇番=枠）の過去 1着率・3連対率
  - local_win_rate / local_top3_rate : この場での過去全成績（当地勝率の深掘り）
  - flying_rate        : F（フライング）発生率 = F回数 / 出走数（欠場を除く）
  - st_avg / st_std    : 平均スタートタイミング・その標準偏差
  - recent_form_30d    : 直近30日の平均着順・勝率
  - recent_form_n      : 直近Nレース（既定 N=20）の勝率・平均着順

あわせて 5.3 場特徴・5.2 モーター特徴の as-of 集計も同じ1パスで行う:
  - venue_lane1_winrate     : その場の1コース(枠1)1着率
  - venue_own_lane_winrate  : その場×自枠の1着率
  - motor_intrinsic_win/top2: 場×モーター番号で全乗り手を集計した素の機力

ラインナップ源（出力行）は --lineup で選択:
  b = B-file（出走表。全6艇。本番想定の既定）/ k = K-file（完走艇のみ）
履歴集計は常に K-file から行う。lane/local/st/recent は完走行のみを母数とし、
flying_rate のみ非完走行（status 列: F/S*/K*）を使う。
※ flying_rate には k_to_csv.py が出力する 'status' 列が必要（非完走行を保持する版）。

リーク防止（仕様 4 章）の絶対則:
  - as-of 基準日 = レース当日（当日を含まない）。
  - K-file を日付昇順に 1 パスで前進処理し、選手×枠ごとの集計を逐次更新する。
    各日 D について「D 未満の状態で特徴量を出力」→「D の結果を状態へ反映」の順に
    行うため、当日・未来のレース結果は構造的に混入しない。
  - 全期間一括集計は行わない。

入力 : 既存の出力 CSV（k_to_csv.py が作る data/k*.csv）。cp932。
出力 : (race_id, 枠番) のロング形式 CSV（1 レース最大 6 行）。

使い方:
  py -3 features_player_history.py                       # data/k*.csv 全期間
  py -3 features_player_history.py --start 250801 --end 250831
  py -3 features_player_history.py --out features_hist.csv --recent-n 20
"""

import argparse
import csv
import glob
import math
import os
import re
from collections import defaultdict
from datetime import datetime

# ── 会場名 → 場コード(2桁) ──────────────────────────────────────────────
# K-file の会場名は clean_name 済み（全角スペース除去）。標準 24 場。
VENUE_CODE = {
    "桐生": "01", "戸田": "02", "江戸川": "03", "平和島": "04",
    "多摩川": "05", "浜名湖": "06", "蒲郡": "07", "常滑": "08",
    "津": "09", "三国": "10", "びわこ": "11", "住之江": "12",
    "尼崎": "13", "鳴門": "14", "丸亀": "15", "児島": "16",
    "宮島": "17", "徳山": "18", "下関": "19", "若松": "20",
    "芦屋": "21", "福岡": "22", "唐津": "23", "大村": "24",
}


def parse_date(s):
    """K-file の日付 '2025/8/1' を date に変換。"""
    return datetime.strptime(s.strip(), "%Y/%m/%d").date()


def file_date_key(path):
    """data/k250801.csv / data/b250801.csv → 250801（並び替え用）。"""
    m = re.search(r"[bk](\d{6})", os.path.basename(path))
    return m.group(1) if m else "999999"


def date_from_key(key):
    """YYMMDD '250809' → date(2025, 8, 9)。B-file は日付列が無いのでファイル名から導く。"""
    return datetime(2000 + int(key[:2]), int(key[2:4]), int(key[4:6])).date()


def std_from_moments(n, s, ss):
    """母標準偏差。n<2 は None。"""
    if n < 2:
        return None
    var = ss / n - (s / n) ** 2
    return math.sqrt(var) if var > 0 else 0.0


def load_by_date(paths, src):
    """CSV 群を date -> 行リスト の辞書に読み込む。
    src='k': 行内の '日付' 列で日付を決める（results / 履歴の元）。
    src='b': 日付列が無いのでファイル名（YYMMDD）で決める（出走表 = ラインナップ）。
    """
    by_date = defaultdict(list)
    for path in paths:
        with open(path, encoding="cp932") as f:
            rows = list(csv.DictReader(f))
        if src == "b":
            d = date_from_key(file_date_key(path))
            for r in rows:
                by_date[d].append(r)
        else:
            for r in rows:
                by_date[parse_date(r["日付"])].append(r)
    return by_date


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="data/k*.csv",
                    help="K-file CSV のグロブ（履歴集計・結果の元。常に使用）")
    ap.add_argument("--b-glob", default="data/b*.csv",
                    help="B-file CSV のグロブ（--lineup b のときのラインナップ源）")
    ap.add_argument("--lineup", choices=["b", "k"], default="b",
                    help="出力行のラインナップ源。b=出走表(全6艇/本番想定) / k=完走艇のみ")
    ap.add_argument("--start", default=None, help="開始 YYMMDD（含む）例: 250801")
    ap.add_argument("--end", default=None, help="終了 YYMMDD（含む）例: 250831")
    ap.add_argument("--recent-n", type=int, default=20, help="recent_form_n の N")
    ap.add_argument("--recent-days", type=int, default=30, help="recent_form_30d の窓日数")
    ap.add_argument("--low-sample-th", type=int, default=6,
                    help="is_low_sample を立てる総出走数のしきい値（未満で 1）")
    ap.add_argument("--out", default="features_player_history.csv", help="出力 CSV パス")
    args = ap.parse_args()

    def pick(g):
        ps = sorted(glob.glob(g), key=file_date_key)
        if args.start:
            ps = [p for p in ps if file_date_key(p) >= args.start]
        if args.end:
            ps = [p for p in ps if file_date_key(p) <= args.end]
        return ps

    kpaths = pick(args.glob)                          # 履歴・結果（常に必要）
    bpaths = pick(args.b_glob) if args.lineup == "b" else []
    if not kpaths:
        print("× K-file CSV が見つかりません:", args.glob)
        return
    if args.lineup == "b" and not bpaths:
        print("× B-file CSV が見つかりません:", args.b_glob)
        return

    # 結果（状態更新の元）は常に K-file。ラインナップは選択した源。
    kresults_by_date = load_by_date(kpaths, "k")
    lineup_by_date = (load_by_date(bpaths, "b") if args.lineup == "b"
                      else kresults_by_date)

    # ── 逐次集計の状態（すべて「処理済みの日 < D」だけを反映） ────────
    # 着順ベースの集計（lane / local）は完走行のみを母数にする（元の定義を維持）。
    lane = defaultdict(lambda: [0, 0, 0])       # (登番, 枠) -> [出走数, 勝数, 3連対数]
    local = defaultdict(lambda: [0, 0, 0])      # (登番, 場コード) -> [出走数, 勝数, 3連対数]
    st = defaultdict(lambda: [0, 0.0, 0.0])     # 登番 -> [n, Σst, Σst^2]
    recent = defaultdict(list)                  # 登番 -> [(date, 着順, is_win), ...]
    total_races = defaultdict(int)              # 登番 -> 完走数（is_low_sample 用）
    starts = defaultdict(int)                   # 登番 -> 出走数（欠場Kを除く。flying_rate 分母）
    flying = defaultdict(int)                   # 登番 -> F（フライング）回数
    # 5.4 進入: 枠なり率・前づけ率（K-file の進入コース from as-of 集計）
    course = defaultdict(lambda: [0, 0, 0])     # 登番 -> [進入判明数, 枠なり数, 前づけ数]
    # 5.3 場特徴: 場×枠の as-of 1着率（venue_lane1_winrate = 場×枠1）
    venue_lane = defaultdict(lambda: [0, 0])    # (場コード, 枠) -> [出走数, 勝数]
    # 5.2 モーター: 場×モーター番号で全乗り手の成績を集計（腕と機力の分離）
    motor = defaultdict(lambda: [0, 0, 0])      # (場コード, モーター番号) -> [出走数, 勝数, 2連対数]

    out_rows = []
    n = args.recent_n
    win_days = args.recent_days

    # ラインナップ日と結果日の和集合を昇順に1パス処理。
    all_dates = sorted(set(lineup_by_date) | set(kresults_by_date))
    for d in all_dates:
        # ── (1) D 未満の状態で当日ラインナップの特徴量を出力 ──────────
        for r in lineup_by_date.get(d, []):
            toban = r["登番"]
            waku = int(r["艇番"])
            venue = r["会場"]
            code = VENUE_CODE.get(venue, "00")
            race_no = int(r["レース"])
            race_id = f"{code}{d:%Y%m%d}{race_no:02d}"

            # lane_win_rate / lane_top3_rate（この選手×この枠の as-of 成績）
            ln, lw, lt3 = lane[(toban, waku)]
            lane_win_rate = (lw / ln) if ln else None
            lane_top3_rate = (lt3 / ln) if ln else None

            # local_hist_win（この場での過去全成績。印字当地勝率より長期/詳細）
            lcn, lcw, lct3 = local[(toban, code)]
            local_win_rate = (lcw / lcn) if lcn else None
            local_top3_rate = (lct3 / lcn) if lcn else None

            # flying_rate（F回数 / 出走数。出走は欠場を除く）
            sc = starts[toban]
            flying_rate = (flying[toban] / sc) if sc else None

            # 5.4 wakunari_rate / maezuke_rate（過去の進入挙動。as-of。当日の進入は使わない）
            cn, cw, cm = course[toban]
            wakunari_rate = (cw / cn) if cn else None
            maezuke_rate = (cm / cn) if cn else None

            # 5.3 venue_lane1_winrate（場×枠1の1着率）・venue_own_lane（場×自枠）
            v1n, v1w = venue_lane[(code, 1)]
            venue_lane1_winrate = (v1w / v1n) if v1n else None
            von, vow = venue_lane[(code, waku)]
            venue_own_lane_winrate = (vow / von) if von else None

            # 5.2 motor_intrinsic（場×モーター番号で全乗り手集計＝素の機力）
            motor_no = None
            try:
                motor_no = int(r["モーター番号"])
            except (ValueError, KeyError, TypeError):
                pass
            mn, mw, mt2 = motor[(code, motor_no)] if motor_no is not None else (0, 0, 0)
            motor_intrinsic_win = (mw / mn) if mn else None
            motor_intrinsic_top2 = (mt2 / mn) if mn else None

            # st_avg / st_std
            sn, ssum, sss = st[toban]
            st_avg = (ssum / sn) if sn else None
            st_std = std_from_moments(sn, ssum, sss)

            # recent_form_30d（直近 win_days 日の平均着順・勝率）
            hist = recent[toban]
            w30 = [h for h in hist if 0 <= (d - h[0]).days <= win_days]
            if w30:
                r30_n = len(w30)
                r30_avgrank = sum(h[1] for h in w30) / r30_n
                r30_winrate = sum(h[2] for h in w30) / r30_n
            else:
                r30_n, r30_avgrank, r30_winrate = 0, None, None

            # recent_form_n（直近 N レースの勝率・平均着順）
            wN = hist[-n:]
            if wN:
                rN_n = len(wN)
                rN_avgrank = sum(h[1] for h in wN) / rN_n
                rN_winrate = sum(h[2] for h in wN) / rN_n
            else:
                rN_n, rN_avgrank, rN_winrate = 0, None, None

            is_low = 1 if total_races[toban] < args.low_sample_th else 0

            out_rows.append({
                "race_id": race_id,
                "枠番": waku,
                "日付": f"{d:%Y-%m-%d}",
                "場コード": code,
                "会場": venue,
                "レース": race_no,
                "登番": toban,
                "選手名": r["選手名"],
                "lane_win_rate": lane_win_rate,
                "lane_top3_rate": lane_top3_rate,
                "lane_n": ln,
                "local_win_rate": local_win_rate,
                "local_top3_rate": local_top3_rate,
                "local_n": lcn,
                "flying_rate": flying_rate,
                "flying_n": flying[toban],
                "starts_n": sc,
                "wakunari_rate": wakunari_rate,
                "maezuke_rate": maezuke_rate,
                "course_n": cn,
                "venue_lane1_winrate": venue_lane1_winrate,
                "venue_own_lane_winrate": venue_own_lane_winrate,
                "venue_lane_n": von,
                "motor_intrinsic_win": motor_intrinsic_win,
                "motor_intrinsic_top2": motor_intrinsic_top2,
                "motor_intrinsic_n": mn,
                "st_avg": st_avg,
                "st_std": st_std,
                "st_n": sn,
                "recent30_winrate": r30_winrate,
                "recent30_avgrank": r30_avgrank,
                "recent30_n": r30_n,
                "recentN_winrate": rN_winrate,
                "recentN_avgrank": rN_avgrank,
                "recentN_n": rN_n,
                "n_races_used": total_races[toban],
                "is_low_sample": is_low,
            })

        # ── (2) 当日 D の結果（K-file）を状態へ反映 ─────────────────
        for r in kresults_by_date.get(d, []):
            toban = r["登番"]
            waku = int(r["艇番"])
            status = (r.get("status") or "finish").strip()

            # 出走数（flying_rate 分母）と F 回数。欠場(K*)は出走に数えない。
            if not status.startswith("K"):
                starts[toban] += 1
                if status == "F":
                    flying[toban] += 1

            # 着順ベースの集計は完走行のみ（lane/local/st/recent/total）。
            if status != "finish":
                continue

            rank = int(r["着順"])
            is_win = 1 if rank == 1 else 0
            is_top3 = 1 if rank <= 3 else 0

            lane[(toban, waku)][0] += 1
            lane[(toban, waku)][1] += is_win
            lane[(toban, waku)][2] += is_top3

            code = VENUE_CODE.get(r["会場"], "00")
            local[(toban, code)][0] += 1
            local[(toban, code)][1] += is_win
            local[(toban, code)][2] += is_top3

            # 5.3 場×枠の1着率
            venue_lane[(code, waku)][0] += 1
            venue_lane[(code, waku)][1] += is_win

            # 5.2 場×モーター番号（全乗り手）。is_top2 で機力の安定度も拾う。
            try:
                motor_no = int(r["モーター番号"])
                motor[(code, motor_no)][0] += 1
                motor[(code, motor_no)][1] += is_win
                motor[(code, motor_no)][2] += 1 if rank <= 2 else 0
            except (ValueError, KeyError, TypeError):
                pass

            # ST は正常スタートのみ（F/L 等は status!=finish で既に除外）。
            try:
                stv = float(r["スタートタイミング"])
                st[toban][0] += 1
                st[toban][1] += stv
                st[toban][2] += stv * stv
            except (ValueError, KeyError):
                pass

            # 5.4 進入コース（finish 行のみ。枠なり=進入==艇番 / 前づけ=進入<艇番）
            try:
                shin = int(r["進入コース"])
                course[toban][0] += 1
                if shin == waku:
                    course[toban][1] += 1
                elif shin < waku:
                    course[toban][2] += 1
            except (ValueError, KeyError, TypeError):
                pass

            recent[toban].append((d, rank, is_win))
            total_races[toban] += 1

    if not out_rows:
        print("× 出力行がありません。")
        return

    # ── 出力 ──────────────────────────────────────────────────────────
    fieldnames = list(out_rows[0].keys())

    def fmt(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.4f}"
        return v

    with open(args.out, "w", encoding="cp932", newline="", errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in out_rows:
            w.writerow({k: fmt(v) for k, v in row.items()})

    days = len({r["日付"] for r in out_rows})
    races = len({r["race_id"] for r in out_rows})
    print(f"○ 出力: {args.out}")
    print(f"  ラインナップ源={args.lineup}  K-file {len(kpaths)} / B-file {len(bpaths)}")
    print(f"  日数 {days} / レース {races} / 行 {len(out_rows)}")
    print(f"  期間 {out_rows[0]['日付']} 〜 {out_rows[-1]['日付']}  recent-n={n} recent-days={win_days}")


if __name__ == "__main__":
    main()
