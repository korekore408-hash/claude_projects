# -*- coding: utf-8 -*-
"""
2連単・3連単の予想と確率（直前情報なしモデル / Phase 3 簡易版）
=========================================================================
各艇の「強さ」を履歴データから条件付きロジット（レース内 softmax）で学習し、
1着確率を求める。さらに Plackett-Luce で着順全体の同時確率に展開して、
2連単（順序つき2着）・3連単（順序つき3着）の確率を算出する。

確率の出し方（仕様 2.1 の設計に対応）:
  強さ  s_i = exp(w・x_i)
  1着   P(i)       = s_i / Σ s
  2連単 P(i→j)     = P(i) · s_j / (Σ s − s_i)
  3連単 P(i→j→k)   = P(i→j) · s_k / (Σ s − s_i − s_j)

リーク防止:
  - 特徴量はすべて as-of 済み（features_player_history / _race_relative）と
    B-file 印字値のみ。標準化・欠損補完の統計量は train 期間だけから作る。
  - train/test は時系列分割（8 章）。先の期間で学習し後の期間で検証。

入力:
  features_player_history.csv / features_race_relative.csv / data/k*.csv（正解着順）
出力:
  predict_win.csv … (race_id, 枠, 登番, p_win, strength, finish_rank)
                    ※ 2連単/3連単は strength から展開できるため艇単位で保存し、
                       ビューア(JS)側で組合せ確率を計算する。

使い方:
  py -3 predict_combos.py
  py -3 predict_combos.py --train-end 250822   # 〜8/22学習, 以降検証
"""

import argparse
import csv
import glob
import math
from collections import defaultdict

from features_player_history import VENUE_CODE

# 候補となる全連続特徴 → どの CSV の列から引くか（"hist" or "rel"）。
# 条件付きロジット（レース内 softmax）ではレース内で全艇共通の特徴は打ち消される
# ため学習に入れない: venue_lane1_winrate / field_strength_std（表示用に CSV には残す）。
FEATURE_SOURCE = {
    # 履歴 CSV（as-of）
    "lane_win_rate": "hist", "lane_top3_rate": "hist",
    "local_win_rate": "hist", "local_top3_rate": "hist",
    "flying_rate": "hist", "st_avg": "hist", "st_std": "hist",
    "recent30_winrate": "hist", "recent30_avgrank": "hist",
    "recentN_winrate": "hist", "recentN_avgrank": "hist",
    "venue_own_lane_winrate": "hist",
    "motor_intrinsic_win": "hist", "motor_intrinsic_top2": "hist",
    "wakunari_rate": "hist", "maezuke_rate": "hist",
    # レース内相対 CSV（B-file 印字 + as-of 派生）
    "class_ord": "rel", "win_rate_national": "rel", "motor_top2_rate": "rel",
    "class_gap": "rel", "winrate_rank_in_race": "rel",
    "winrate_diff_top": "rel", "motor_rank_in_race": "rel",
    "st_rank_in_race": "rel",
    # 場の荒れ度・気象の交互作用（生値はレース内共通で softmax で消えるため積で持つ）。
    "venue_rough_x_gap": "rel", "wind_x_lane": "rel", "wave_x_lane": "rel",
    # B-file 印字値（公式の長期集計＝母数大）
    "top2_rate_national": "rel", "win_rate_local": "rel", "top2_rate_local": "rel",
    "boat_top2_rate": "rel", "weight": "rel", "age": "rel",
}

# 既定の連続特徴（標準化対象）。lane ダミーは別途 0/1 で追加。--features で上書き可。
# experiment_features.py の貪欲前進選択（2025/8 split, logloss最小化）で得た暫定セット。
# ※ test漏れを含む選択なので、データ拡充後（2026年分追加）に再選定する前提。
#   旧 base 9特徴に B-file 印字値（age/体重/当地2率）と相対量を加えて
#   1着的中 0.564→0.578 / logloss 1.220→1.205（test 2025/8/22以降）。
CONT_FEATURES = [
    "class_ord", "win_rate_national", "motor_top2_rate",
    "st_avg", "lane_win_rate", "recent30_winrate",
    "venue_own_lane_winrate", "motor_intrinsic_win", "motor_intrinsic_top2",
    "age", "top2_rate_local", "motor_rank_in_race", "weight",
    "local_win_rate", "st_std", "winrate_rank_in_race", "winrate_diff_top",
    # 場の荒れ度 × 実力差 / 風速 × 枠 / 波高 × 枠 の交互作用。
    # time-split 検証では重み ±0.03 以下・OOS 的中ほぼ不変（構造的上限）だが、
    # 「場ごとの荒れ・気象を反映」する方針として配線（当日の気象は中立化＝未反映）。
    "venue_rough_x_gap", "wind_x_lane", "wave_x_lane",
]


def to_float(s):
    try:
        return float(str(s).strip())
    except (ValueError, AttributeError):
        return None


def load_joined(hist_path, rel_path):
    """history と relative を (race_id, 枠) で結合して 1 行 = 1 艇の dict 群にする。
    FEATURE_SOURCE の全候補列を読み込む（実際にモデルへ入れる列は CONT_FEATURES で選択）。"""
    hist = {}
    with open(hist_path, encoding="cp932") as f:
        for r in csv.DictReader(f):
            hist[(r["race_id"], r["枠番"])] = r
    rows = []
    with open(rel_path, encoding="cp932") as f:
        for r in csv.DictReader(f):
            h = hist.get((r["race_id"], r["枠番"]), {})
            row = {
                "race_id": r["race_id"],
                "date": r["日付"],
                "枠": int(r["枠番"]),
                "登番": r["登番"],
                "選手名": r["選手名"],
            }
            for feat, src in FEATURE_SOURCE.items():
                col = h.get(feat) if src == "hist" else r.get(feat)
                row[feat] = to_float(col)
            rows.append(row)
    return rows


def load_results():
    """K-file から race_id 単位の着順→艇番（正解）を作る。"""
    order = defaultdict(dict)   # race_id -> {艇番: 着順}
    for p in sorted(glob.glob("data/k*.csv")):
        with open(p, encoding="cp932") as f:
            for r in csv.DictReader(f):
                if (r.get("status") or "finish") != "finish":
                    continue
                code = VENUE_CODE.get(r["会場"], "00")
                d = r["日付"]  # 2025/8/1
                y, m, dd = d.split("/")
                race_id = f"{code}{int(y):04d}{int(m):02d}{int(dd):02d}{int(r['レース']):02d}"
                order[race_id][int(r["艇番"])] = int(r["着順"])
    return order


def build_features(rows, train_end):
    """train 期間の統計で標準化・欠損補完し、各艇の特徴ベクトルを作る。
    返り値: races = {race_id: {"date":..., "boats":[{枠,登番,vec,finish}, ...]}}
    と feature 名リスト。"""
    train_rows = [r for r in rows if date_key(r["date"]) <= train_end]

    # 連続特徴の train 平均・標準偏差（欠損は除外して算出）
    stats = {}
    for c in CONT_FEATURES:
        vals = [r[c] for r in train_rows if r[c] is not None]
        mean = sum(vals) / len(vals) if vals else 0.0
        var = sum((v - mean) ** 2 for v in vals) / len(vals) if len(vals) > 1 else 1.0
        std = math.sqrt(var) if var > 0 else 1.0
        stats[c] = (mean, std)

    feat_names = list(CONT_FEATURES) + [f"lane{w}" for w in range(2, 7)]

    races = defaultdict(lambda: {"date": None, "boats": []})
    for r in rows:
        vec = []
        for c in CONT_FEATURES:
            mean, std = stats[c]
            v = r[c]
            vec.append(0.0 if v is None else (v - mean) / std)  # 欠損→平均(=0)
        for w in range(2, 7):
            vec.append(1.0 if r["枠"] == w else 0.0)
        races[r["race_id"]]["date"] = r["date"]
        races[r["race_id"]]["boats"].append({
            "枠": r["枠"], "登番": r["登番"], "名": r["選手名"], "vec": vec,
        })
    return races, feat_names, stats


def date_key(date_str):
    """'2025-08-22' -> '250822'（train_end 比較用）。"""
    y, m, d = date_str.split("-")
    return f"{y[2:]}{m}{d}"


def softmax_strength(boats, w):
    """各艇の strength=exp(v) と 1着確率を返す。"""
    vs = [sum(wi * xi for wi, xi in zip(w, b["vec"])) for b in boats]
    mx = max(vs)
    s = [math.exp(v - mx) for v in vs]   # 数値安定化
    tot = sum(s)
    p = [si / tot for si in s]
    return s, p, vs


def train_logit(races, train_ids, winner_of, n_feat, iters=300, lr=0.3, l2=1.0):
    """条件付きロジットを full-batch 勾配上昇で学習。winner_of[race_id]=勝者の枠。"""
    w = [0.0] * n_feat
    n = len(train_ids)
    for it in range(iters):
        grad = [0.0] * n_feat
        for rid in train_ids:
            boats = races[rid]["boats"]
            wb = winner_of.get(rid)
            if wb is None:
                continue
            s, p, _ = softmax_strength(boats, w)
            # 勝者の枠に対応する index
            widx = next((i for i, b in enumerate(boats) if b["枠"] == wb), None)
            if widx is None:
                continue
            for k in range(n_feat):
                exp_x = sum(p[i] * boats[i]["vec"][k] for i in range(len(boats)))
                grad[k] += boats[widx]["vec"][k] - exp_x
        # L2 正則化（平均化）
        for k in range(n_feat):
            w[k] += lr * (grad[k] / n - l2 * w[k] / n)
    return w


def plackett_luce_top(strengths, kind, topk):
    """strengths(list) から 2連単/3連単 上位を返す。
    返り値: [((枠tuple), 確率), ...] 枠は 1始まり。"""
    idx = list(range(len(strengths)))
    tot = sum(strengths)
    combos = []
    if kind == 2:
        for i in idx:
            for j in idx:
                if j == i:
                    continue
                p = (strengths[i] / tot) * (strengths[j] / (tot - strengths[i]))
                combos.append(((i + 1, j + 1), p))
    else:
        for i in idx:
            for j in idx:
                if j == i:
                    continue
                for k in idx:
                    if k in (i, j):
                        continue
                    p = (strengths[i] / tot) \
                        * (strengths[j] / (tot - strengths[i])) \
                        * (strengths[k] / (tot - strengths[i] - strengths[j]))
                    combos.append(((i + 1, j + 1, k + 1), p))
    combos.sort(key=lambda x: x[1], reverse=True)
    return combos[:topk]


def evaluate(races, ids, winner_of, order_of, w):
    """1着的中率・2連単/3連単 top1 的中率・勝者 logloss を返す。"""
    n = win_hit = ex_hit = tri_hit = 0
    base1 = 0  # 1号艇ベースライン
    ll = 0.0
    for rid in ids:
        boats = races[rid]["boats"]
        wb = winner_of.get(rid)
        if wb is None:
            continue
        s, p, _ = softmax_strength(boats, w)
        order_idx = sorted(range(len(boats)), key=lambda i: p[i], reverse=True)
        pred1 = boats[order_idx[0]]["枠"]
        n += 1
        win_hit += (pred1 == wb)
        base1 += (1 == wb)
        widx = next((i for i, b in enumerate(boats) if b["枠"] == wb), None)
        if widx is not None:
            ll += -math.log(max(p[widx], 1e-12))
        # 2連単・3連単 top1（枠→strength に並べ替えてから PL 展開）
        wk2s = {boats[i]["枠"]: s[i] for i in range(len(boats))}
        full = [wk2s.get(w_, 0.0) for w_ in range(1, 7)]
        ex = plackett_luce_top(full, 2, 1)[0][0]
        tri = plackett_luce_top(full, 3, 1)[0][0]
        od = order_of.get(rid, {})
        actual_order = sorted(od, key=lambda b: od[b])  # 着順昇順の艇番
        if len(actual_order) >= 2:
            ex_hit += (ex == tuple(actual_order[:2]))
        if len(actual_order) >= 3:
            tri_hit += (tri == tuple(actual_order[:3]))
    return {
        "n": n,
        "win_acc": win_hit / n if n else 0,
        "base1_acc": base1 / n if n else 0,
        "exacta_top1": ex_hit / n if n else 0,
        "trifecta_top1": tri_hit / n if n else 0,
        "winner_logloss": ll / n if n else 0,
    }


# ───────────────────────── walk-forward (of-sample) ─────────────────────────
# 各レースを「その日より前のデータだけ」で学習したモデルで予想する。
# 日次に expanding window で再学習（warm-start）し、当日を OOS 予想 → 翌日のため
# に当日を学習へ加える。標準化・欠損補完の統計量も毎フォールド train のみで再計算。

def build_raw(rows):
    """標準化前の生特徴で races を作る（フォールド毎に as-of 標準化するため）。"""
    races = defaultdict(lambda: {"date": None, "boats": []})
    for r in rows:
        races[r["race_id"]]["date"] = r["date"]
        races[r["race_id"]]["boats"].append({
            "枠": r["枠"], "登番": r["登番"], "名": r["選手名"],
            "cont": {c: r[c] for c in CONT_FEATURES},
        })
    feat_names = list(CONT_FEATURES) + [f"lane{w}" for w in range(2, 7)]
    return races, feat_names


def stats_from(rids, races):
    """train rids の生特徴から平均・標準偏差（欠損は除外）。"""
    stats = {}
    for c in CONT_FEATURES:
        vals = [b["cont"][c] for rid in rids for b in races[rid]["boats"]
                if b["cont"][c] is not None]
        mean = sum(vals) / len(vals) if vals else 0.0
        var = sum((v - mean) ** 2 for v in vals) / len(vals) if len(vals) > 1 else 1.0
        std = math.sqrt(var) if var > 0 else 1.0
        stats[c] = (mean, std)
    return stats


def vec_of(boat, stats):
    vec = []
    for c in CONT_FEATURES:
        mean, std = stats[c]
        v = boat["cont"][c]
        vec.append(0.0 if v is None else (v - mean) / std)
    for k in range(2, 7):
        vec.append(1.0 if boat["枠"] == k else 0.0)
    return vec


def strengths_with(boats, stats, w):
    vs = [sum(wi * xi for wi, xi in zip(w, vec_of(b, stats))) for b in boats]
    mx = max(vs)
    s = [math.exp(v - mx) for v in vs]
    tot = sum(s)
    return s, [si / tot for si in s]


def prep_fold(rids, races, winner_of, stats):
    """学習用に (各艇vec, 勝者index) を作る。勝者不明レースは除外。"""
    prepared = []
    for rid in rids:
        wb = winner_of.get(rid)
        if wb is None:
            continue
        boats = races[rid]["boats"]
        widx = next((i for i, b in enumerate(boats) if b["枠"] == wb), None)
        if widx is None:
            continue
        prepared.append(([vec_of(b, stats) for b in boats], widx))
    return prepared


def train_warm(prepared, w, iters, lr, l2):
    """warm-start で条件付きロジットを勾配上昇。w を破壊的に更新して返す。"""
    n = len(prepared) or 1
    nf = len(w)
    for _ in range(iters):
        grad = [0.0] * nf
        for vecs, widx in prepared:
            vs = [sum(wi * xi for wi, xi in zip(w, v)) for v in vecs]
            mx = max(vs)
            s = [math.exp(v - mx) for v in vs]
            tot = sum(s)
            p = [si / tot for si in s]
            for k in range(nf):
                ex = sum(p[i] * vecs[i][k] for i in range(len(vecs)))
                grad[k] += vecs[widx][k] - ex
        for k in range(nf):
            w[k] += lr * (grad[k] / n - l2 * w[k] / n)
    return w


def walk_forward(rows, order_of, winner_of, warmup_days, full_iters,
                 refit_iters, lr, l2, out_path):
    races, feat_names = build_raw(rows)
    by_day = defaultdict(list)
    for rid in races:
        by_day[races[rid]["date"]].append(rid)
    days = sorted(by_day)

    warm_days, pred_days = days[:warmup_days], days[warmup_days:]
    train_rids = [r for d in warm_days for r in by_day[d]]

    stats = stats_from(train_rids, races)
    w = train_warm(prep_fold(train_rids, races, winner_of, stats),
                   [0.0] * len(feat_names), full_iters, lr, l2)

    oos = []
    n = win_hit = base1 = ex_hit = tri_hit = 0
    ll = 0.0
    for d in pred_days:
        # (1) この日を OOS 予想（w, stats は d より前のデータのみを反映）
        for rid in by_day[d]:
            boats = races[rid]["boats"]
            s, p = strengths_with(boats, stats, w)
            od = order_of.get(rid, {})
            for i, b in enumerate(boats):
                oos.append({
                    "race_id": rid, "枠番": b["枠"], "登番": b["登番"],
                    "p_win": f"{p[i]:.4f}", "strength": f"{s[i]:.6f}",
                    "finish_rank": od.get(b["枠"], ""),
                })
            wb = winner_of.get(rid)
            if wb is None:
                continue
            n += 1
            pred1 = boats[max(range(len(boats)), key=lambda i: p[i])]["枠"]
            win_hit += (pred1 == wb)
            base1 += (1 == wb)
            widx = next((i for i, bb in enumerate(boats) if bb["枠"] == wb), None)
            if widx is not None:
                ll += -math.log(max(p[widx], 1e-12))
            wk2s = {boats[i]["枠"]: s[i] for i in range(len(boats))}
            full = [wk2s.get(w_, 0.0) for w_ in range(1, 7)]
            order = sorted(od, key=lambda bn: od[bn])
            if len(order) >= 2:
                ex_hit += (plackett_luce_top(full, 2, 1)[0][0] == tuple(order[:2]))
            if len(order) >= 3:
                tri_hit += (plackett_luce_top(full, 3, 1)[0][0] == tuple(order[:3]))
        # (2) この日を学習に加えて warm-start 再フィット
        train_rids += by_day[d]
        stats = stats_from(train_rids, races)
        w = train_warm(prep_fold(train_rids, races, winner_of, stats),
                       w, refit_iters, lr, l2)

    with open(out_path, "w", encoding="cp932", newline="", errors="replace") as f:
        wtr = csv.DictWriter(f, fieldnames=list(oos[0].keys()))
        wtr.writeheader()
        wtr.writerows(oos)

    print("\n=== walk-forward (of-sample) ===")
    print(f"  ウォームアップ {len(warm_days)}日 / OOS予想 {len(pred_days)}日 / 評価レース {n}")
    print(f"  1着的中率   {win_hit/n:.3f}（1号艇ベース {base1/n:.3f}）")
    print(f"  2連単 top1  {ex_hit/n:.3f} / 3連単 top1 {tri_hit/n:.3f}")
    print(f"  勝者logloss {ll/n:.3f}")
    print(f"○ 出力: {out_path}（{len(oos)} 行 / OOSのみ）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default="features_player_history.csv")
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--mode", choices=["split", "walkforward"], default="split",
                    help="split=単一時系列分割 / walkforward=日次OOS（of-sample）")
    ap.add_argument("--train-end", default="250822", help="split: 学習最終日 YYMMDD")
    ap.add_argument("--warmup-days", type=int, default=7, help="walkforward: 種学習の日数")
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--refit-iters", type=int, default=40, help="walkforward: 日次再フィット反復")
    ap.add_argument("--features", default=None,
                    help="使用する連続特徴をカンマ区切りで上書き（アブレーション用）。"
                         "例: --features class_ord,win_rate_national,st_avg")
    ap.add_argument("--out", default="predict_win.csv")
    args = ap.parse_args()

    # --features で連続特徴を差し替え（FEATURE_SOURCE にある名前のみ許可）。
    global CONT_FEATURES
    if args.features:
        sel = [f.strip() for f in args.features.split(",") if f.strip()]
        bad = [f for f in sel if f not in FEATURE_SOURCE]
        if bad:
            ap.error(f"未知の特徴: {bad}（候補: {sorted(FEATURE_SOURCE)}）")
        CONT_FEATURES = sel
        print(f"[features] {len(sel)}個: {sel}")

    if args.mode == "walkforward":
        rows = load_joined(args.hist, args.rel)
        order_of = load_results()
        winner_of = {rid: next((b for b, rk in od.items() if rk == 1), None)
                     for rid, od in order_of.items()}
        out = args.out if args.out != "predict_win.csv" else "predict_win_oos.csv"
        walk_forward(rows, order_of, winner_of, args.warmup_days,
                     args.iters, args.refit_iters, lr=0.3, l2=1.0, out_path=out)
        return

    rows = load_joined(args.hist, args.rel)
    order_of = load_results()
    winner_of = {rid: next((b for b, rk in od.items() if rk == 1), None)
                 for rid, od in order_of.items()}

    races, feat_names, stats = build_features(rows, args.train_end)
    all_ids = sorted(races)
    train_ids = [r for r in all_ids if date_key(races[r]["date"]) <= args.train_end
                 and winner_of.get(r) is not None]
    test_ids = [r for r in all_ids if date_key(races[r]["date"]) > args.train_end
                and winner_of.get(r) is not None]

    print(f"レース {len(all_ids)} / 学習 {len(train_ids)} / 検証 {len(test_ids)}")
    w = train_logit(races, train_ids, winner_of, len(feat_names), iters=args.iters)

    print("\n--- 学習した重み（標準化後） ---")
    for name, wi in zip(feat_names, w):
        print(f"  {name:20} {wi:+.3f}")

    tr = evaluate(races, train_ids, winner_of, order_of, w)
    te = evaluate(races, test_ids, winner_of, order_of, w)
    print("\n--- 精度（train / test）時系列分割 ---")
    print(f"  1着的中率   {tr['win_acc']:.3f} / {te['win_acc']:.3f}"
          f"   （1号艇ベース {te['base1_acc']:.3f}）")
    print(f"  2連単 top1  {tr['exacta_top1']:.3f} / {te['exacta_top1']:.3f}")
    print(f"  3連単 top1  {tr['trifecta_top1']:.3f} / {te['trifecta_top1']:.3f}")
    print(f"  勝者logloss {tr['winner_logloss']:.3f} / {te['winner_logloss']:.3f}")

    # 全レースで 1着確率・strength を出力（ビューアが PL 展開に使う）
    out_rows = []
    for rid in all_ids:
        boats = races[rid]["boats"]
        s, p, _ = softmax_strength(boats, w)
        od = order_of.get(rid, {})
        for i, b in enumerate(boats):
            out_rows.append({
                "race_id": rid,
                "枠番": b["枠"],
                "登番": b["登番"],
                "p_win": f"{p[i]:.4f}",
                "strength": f"{s[i]:.6f}",
                "finish_rank": od.get(b["枠"], ""),
            })
    with open(args.out, "w", encoding="cp932", newline="", errors="replace") as f:
        wtr = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        wtr.writeheader()
        wtr.writerows(out_rows)
    print(f"\n○ 出力: {args.out}（{len(out_rows)} 行）")


if __name__ == "__main__":
    main()
