# -*- coding: utf-8 -*-
"""
特徴量セット比較（大規模データ・時系列分割）
=========================================================================
全期間（2025/8 + 2026/1-6）で、厳選した特徴量セットを time-split で比較する。
貪欲全探索は重いので、解釈しやすい代表セットだけを評価する。

  train: --train-end まで / test: それ以降
使い方:
  py -3 compare_sets.py --train-end 260430
"""

import argparse
import predict_combos as pc


def run(rows, order_of, winner_of, feats, train_end):
    pc.CONT_FEATURES = feats
    races, feat_names, _ = pc.build_features(rows, train_end)
    all_ids = sorted(races)
    tr = [r for r in all_ids if pc.date_key(races[r]["date"]) <= train_end
          and winner_of.get(r) is not None]
    te = [r for r in all_ids if pc.date_key(races[r]["date"]) > train_end
          and winner_of.get(r) is not None]
    w = pc.train_logit(races, tr, winner_of, len(feat_names), iters=250)
    return pc.evaluate(races, te, winner_of, order_of, w), len(tr), len(te)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-end", default="260430")
    args = ap.parse_args()

    rows = pc.load_joined("features_player_history.csv",
                          "features_race_relative.csv")
    order_of = pc.load_results()
    winner_of = {rid: next((b for b, rk in od.items() if rk == 1), None)
                 for rid, od in order_of.items()}

    base9 = [
        "class_ord", "win_rate_national", "motor_top2_rate",
        "st_avg", "lane_win_rate", "recent30_winrate",
        "venue_own_lane_winrate", "motor_intrinsic_win", "motor_intrinsic_top2",
    ]
    printed = base9 + ["age", "top2_rate_local", "win_rate_local",
                       "boat_top2_rate", "weight"]
    improved17 = base9 + ["age", "top2_rate_local", "motor_rank_in_race",
                          "weight", "local_win_rate", "st_std",
                          "winrate_rank_in_race", "winrate_diff_top"]
    history_rich = improved17 + ["lane_top3_rate", "recentN_winrate",
                                 "flying_rate", "local_top3_rate"]
    plus_wakunari = improved17 + ["wakunari_rate"]
    plus_shinnyu = improved17 + ["wakunari_rate", "maezuke_rate"]

    sets = [("base9", base9), ("printed14", printed),
            ("improved17", improved17), ("history_rich21", history_rich),
            ("imp17+wakunari", plus_wakunari),
            ("imp17+waku+maezuke", plus_shinnyu)]

    print(f"\n=== set comparison (train<={args.train_end} / test_after) ===")
    print(f"{'set':16} {'nfeat':>5} {'win_acc':>8} {'exacta':>7} "
          f"{'trifecta':>9} {'logloss':>8}")
    base_acc = None
    for name, feats in sets:
        te, ntr, nte = run(rows, order_of, winner_of, feats, args.train_end)
        if base_acc is None:
            base_acc = te["win_acc"]
            hdr = f"   (train races {ntr} / test races {nte} / 1号艇ベース {te['base1_acc']:.4f})"
        print(f"{name:16} {len(feats):5d} {te['win_acc']:8.4f} "
              f"{te['exacta_top1']:7.4f} {te['trifecta_top1']:9.4f} "
              f"{te['winner_logloss']:8.4f}")
    print(hdr)


if __name__ == "__main__":
    main()
