# -*- coding: utf-8 -*-
"""
特徴量アブレーション実験（精度改善の探索）
=========================================================================
predict_combos.py の split 評価器を使い、連続特徴の集合を差し替えながら
test 期間（既定 〜250822 学習 / 以降検証）の精度を比較する。

  - base       : 現行の既定 9 特徴
  - base + X   : 計算済みだが未使用の特徴を 1 つずつ追加（前進選択の効き目を測る）
  - all_good   : 単独で効いたものをまとめて投入

使い方:
  py -3 experiment_features.py
  py -3 experiment_features.py --train-end 250822
"""

import argparse

import predict_combos as pc


def run(rows, order_of, winner_of, feats, train_end):
    pc.CONT_FEATURES = feats
    races, feat_names, _ = pc.build_features(rows, train_end)
    all_ids = sorted(races)
    train_ids = [r for r in all_ids
                 if pc.date_key(races[r]["date"]) <= train_end
                 and winner_of.get(r) is not None]
    test_ids = [r for r in all_ids
                if pc.date_key(races[r]["date"]) > train_end
                and winner_of.get(r) is not None]
    w = pc.train_logit(races, train_ids, winner_of, len(feat_names), iters=300)
    return pc.evaluate(races, test_ids, winner_of, order_of, w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-end", default="250822")
    args = ap.parse_args()

    rows = pc.load_joined("features_player_history.csv",
                          "features_race_relative.csv")
    order_of = pc.load_results()
    winner_of = {rid: next((b for b, rk in od.items() if rk == 1), None)
                 for rid, od in order_of.items()}

    base = [
        "class_ord", "win_rate_national", "motor_top2_rate",
        "st_avg", "lane_win_rate", "recent30_winrate",
        "venue_own_lane_winrate", "motor_intrinsic_win", "motor_intrinsic_top2",
    ]
    # base に 1 つずつ足して試す未使用候補
    candidates = [
        "local_win_rate", "lane_top3_rate", "st_std", "flying_rate",
        "recentN_winrate", "recent30_avgrank", "recentN_avgrank",
        "class_gap", "winrate_rank_in_race", "winrate_diff_top",
        "motor_rank_in_race", "st_rank_in_race",
        # B-file 印字値（長期集計）
        "top2_rate_national", "win_rate_local", "top2_rate_local",
        "boat_top2_rate", "weight", "age",
    ]

    def show(name, te, b):
        dwin = te["win_acc"] - b["win_acc"]
        print(f"{name:30} {te['win_acc']:8.4f} {dwin:+7.4f} "
              f"{te['exacta_top1']:7.4f} {te['trifecta_top1']:9.4f} "
              f"{te['winner_logloss']:8.4f}")

    base_te = run(rows, order_of, winner_of, base, args.train_end)

    # ── 単独追加（前進選択の効き目） ──────────────────────────────
    print(f"\n=== single-add (test {args.train_end} 以降) ===")
    print(f"{'feature set':30} {'win_acc':>8} {'Δwin':>7} "
          f"{'exacta':>7} {'trifecta':>9} {'logloss':>8}")
    show("base", base_te, base_te)
    for c in candidates:
        show(f"+{c}", run(rows, order_of, winner_of, base + [c], args.train_end),
             base_te)

    # ── 貪欲な前進選択（logloss を最小化）。n=1329 で win_acc は粗いため。 ──
    print(f"\n=== greedy forward selection (logloss 最小化) ===")
    cur = list(base)
    cur_te = base_te
    remaining = list(candidates)
    while remaining:
        best = None
        for c in remaining:
            te = run(rows, order_of, winner_of, cur + [c], args.train_end)
            if best is None or te["winner_logloss"] < best[1]["winner_logloss"]:
                best = (c, te)
        c, te = best
        if te["winner_logloss"] < cur_te["winner_logloss"] - 1e-5:
            cur.append(c)
            cur_te = te
            show(f"+{c}", te, base_te)
            remaining.remove(c)
        else:
            break
    print(f"\n選択された特徴セット ({len(cur)}個):\n  {cur}")
    print(f"\n(base win_acc={base_te['win_acc']:.4f} / "
          f"selected win_acc={cur_te['win_acc']:.4f} / "
          f"1号艇ベース={base_te['base1_acc']:.4f} / n={base_te['n']})")


if __name__ == "__main__":
    main()
