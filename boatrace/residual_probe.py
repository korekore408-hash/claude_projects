# -*- coding: utf-8 -*-
"""
「まだ取れていない勝因」が残っているかの残差テスト（honest OOS 限定）。

問い: モデルが低く見た艇でも、モーター/直近成績/ST が良ければ
      モデル予測を超えて勝つのか？（=モデルが吸収しきれていない“勝因”の有無）
差(実測勝率 - 予測p_win) がプラスに偏れば「未取得シグナルあり=ROI改善余地」、
ほぼ0なら「モデルが既に織込済=特徴を足しても妙味は出ない」。

学習に使っていない期間(rid日付 > 2026-04-30)だけで評価。
"""
import csv
from collections import defaultdict
ENC = "cp932"

def fnum(s):
    try: return float(s)
    except (ValueError, TypeError): return None

def load(path, cols):
    out = {}
    with open(path, encoding=ENC, newline="") as f:
        r = csv.reader(f); h = next(r)
        idx = {c: h.index(c) for c in cols if c in h}
        for row in r:
            if not row: continue
            out[(row[0], row[1])] = {c: row[i] for c, i in idx.items()}
    return out

def main():
    rel = load("features_race_relative.csv",
               ["motor_top2_rate", "st_rank_in_race", "winrate_rank_in_race"])
    hist = load("features_player_history.csv",
                ["recent30_winrate", "motor_intrinsic_win"])

    rows = []
    with open("predict_win.csv", encoding=ENC, newline="") as f:
        r = csv.reader(f); h = next(r)
        i_p, i_f = h.index("p_win"), h.index("finish_rank")
        races = defaultdict(list)
        for row in r:
            if not row: continue
            races[row[0]].append((row[1], fnum(row[i_p]), fnum(row[i_f])))
    OOS = "20260430"
    for rid, boats in races.items():
        if rid[2:10] <= OOS:            # 学習期間内は除外（honest OOS）
            continue
        if len(boats) != 6 or any(b[1] is None for b in boats):
            continue
        order = sorted(boats, key=lambda b: -b[1])
        rank = {b[0]: i + 1 for i, b in enumerate(order)}
        for lane, p, fr in boats:
            rr = rel.get((rid, lane), {}); hh = hist.get((rid, lane), {})
            rows.append({"p": p, "win": 1 if fr == 1 else 0, "rank": rank[lane],
                         "motor": fnum(rr.get("motor_top2_rate")),
                         "recent": fnum(hh.get("recent30_winrate")),
                         "motor_in": fnum(hh.get("motor_intrinsic_win"))})

    low = [v for v in rows if v["rank"] >= 3 and v["p"] < 0.15]
    print(f"OOS期間(>{OOS}) 全艇行: {len(rows)}  低評価艇(順位3-6・p<0.15): {len(low)}")
    print(f"低評価艇 平均予測p_win={sum(v['p'] for v in low)/len(low):.4f}  "
          f"実測勝率={sum(v['win'] for v in low)/len(low):.4f}  "
          f"差={sum(v['win']-v['p'] for v in low)/len(low):+.4f}")
    print()

    def report(key, label):
        vals = sorted(v[key] for v in low if v[key] is not None)
        if len(vals) < 8:
            print(f"  [{label}] データ不足"); return
        a, b, c = vals[len(vals)//4], vals[len(vals)//2], vals[3*len(vals)//4]
        bkt = defaultdict(lambda: [0, 0.0, 0])
        for v in low:
            x = v[key]
            if x is None: continue
            q = 1 if x <= a else 2 if x <= b else 3 if x <= c else 4
            bkt[q][0] += 1; bkt[q][1] += v["p"]; bkt[q][2] += v["win"]
        print(f"  [{label}]  四分位で分割 → 予測 vs 実測（差がプラスなら未取得シグナル）")
        print(f"     Q    n    予測p_win  実測勝率   差")
        for q in range(1, 5):
            n, ps, w = bkt[q]
            if n:
                print(f"     Q{q} {n:5d}    {ps/n:.4f}    {w/n:.4f}   {w/n-ps/n:+.4f}")
        print()

    print("低評価艇を『モデルが使っている特徴』で四分位分割（既に織込済なら差≈0）:")
    report("motor", "モーター2連率")
    report("recent", "直近30走 勝率")
    report("motor_in", "モーター実績(節内)")

if __name__ == "__main__":
    main()
