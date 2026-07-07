# -*- coding: utf-8 -*-
"""標準帯(本命確率0.45-0.65)のサブ帯分割バックテスト。

鉄板・穴は分割不要と判明済み。標準帯を2〜4サブ帯に分け、帯ごとに
点数(2連単k2/3連単k3)や買い目構成(PL上位/本命軸固定/穴混合)を変えると
回収率が上がるかを検証する。荒れ度・スコアはAPI(本番系統)。

  py -3 backtest_stdsplit.py                    # 全期間
  py -3 backtest_stdsplit.py --since 2026-06-01 # 直近だけ(安定性確認)
"""
import argparse

import build_today as B
import backtest as BT

FINE = [(0.45, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 0.65)]   # 診断用0.05刻み
SCHEMES = {
    "2分割@0.55": [(0.45, 0.55), (0.55, 0.65)],
    "2分割@0.50(現行境界)": [(0.45, 0.50), (0.50, 0.65)],
    "3分割": [(0.45, 0.517), (0.517, 0.583), (0.583, 0.65)],
    "4分割": FINE,
}
K2_GRID = [1, 2, 3, 4, 5]
K3_GRID = [0, 3, 5, 7, 10, 14, 20]
BUILDER_K3 = [5, 7, 10, 14]
BUILDERS = {"topk": BT.build_topk, "axis": BT.build_axis, "std30": BT.build_std30}


def collect(races, hon_canon, payout):
    """標準帯レースを1行ずつ: hon, 実結果のPL順位(r2/r3), 配当, builder別ヒット。"""
    rows = []
    for rid, rc in races.items():
        if len(rc["fin"]) != 6:
            continue
        s = [rc["api"].get(w) for w in range(1, 7)]
        if any(x is None for x in s):
            continue
        hon = hon_canon.get(rid)
        if hon is None or not (0.45 <= hon < 0.65):
            continue
        fins = [rc["fin"][w] for w in range(1, 7)]
        order = sorted([w for w in range(1, 7)
                        if fins[w - 1] is not None and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < 2 or fins[order[0] - 1] != 1:
            continue
        po = payout.get(rid, (0, 0))
        pl2 = BT.pl_order(s, 2, set())
        r2 = {c[0]: i for i, c in enumerate(pl2)}.get(tuple(order[:2]))
        has3 = len(order) >= 3
        r3, bhit = None, {}
        if has3:
            pl3 = BT.pl_order(s, 3, set())
            r3 = {c[0]: i for i, c in enumerate(pl3)}.get(tuple(order[:3]))
            act = tuple(order[:3])
            rank = BT.lane_ranks(s)
            for bn, bf in BUILDERS.items():
                if bn == "topk":
                    continue                       # topk は r3 から復元できる
                for k in BUILDER_K3:
                    buy = bf(pl3, k, s, rank)
                    bhit[(bn, k)] = act in {c[0] for c in buy}
        rows.append({"d": rc["d"], "hon": hon, "r2": r2, "p2": po[0],
                     "has3": has3, "r3": r3, "p3": po[1], "bhit": bhit})
    return rows


def sub(rows, lo, hi):
    return [r for r in rows if lo <= r["hon"] < hi]


def roi2(rs, k):
    """2連単 上位k点固定の回収率と賭け金。"""
    if not rs or k <= 0:
        return None, 0
    pay = sum(r["p2"] for r in rs if r["r2"] is not None and r["r2"] < k)
    stake = k * 100 * len(rs)
    return pay / stake * 100, stake


def roi3(rs, k, builder="topk"):
    """3連単 k点(builder構成)の回収率と賭け金。"""
    r3s = [r for r in rs if r["has3"]]
    if not r3s or k <= 0:
        return None, 0
    if builder == "topk":
        pay = sum(r["p3"] for r in r3s if r["r3"] is not None and r["r3"] < k)
    else:
        pay = sum(r["p3"] for r in r3s if r["bhit"].get((builder, k)))
    stake = k * 100 * len(r3s)
    return pay / stake * 100, stake


def roi_current(rs):
    """現行ポリシー(hon連動のk_ex/k_tri・PL上位)の帯内回収率。"""
    pay = stake = 0
    for r in rs:
        k2, k3 = B.k_ex(r["hon"]), B.k_tri(r["hon"])
        stake += k2 * 100
        if r["r2"] is not None and r["r2"] < k2:
            pay += r["p2"]
        if r["has3"]:
            stake += k3 * 100
            if r["r3"] is not None and r["r3"] < k3:
                pay += r["p3"]
    return (pay / stake * 100 if stake else None), stake, pay


def f(v):
    return f"{v:6.1f}" if v is not None else "   – "


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--hist", default="features_player_history.csv")
    ap.add_argument("--since", default="2026-01-01")
    args = ap.parse_args()

    print(f"データ読込 since={args.since} …")
    races, hon_api, _, payout = BT.load_races(args.rel, args.pred, args.hist, args.since)
    rows = collect(races, hon_api, payout)
    print(f"標準帯レース {len(rows)} 件（期間 {min(r['d'] for r in rows)}〜{max(r['d'] for r in rows)}）")

    # ---- 1) 0.05刻み診断: 本命1着率と点数別の累積回収率 ----
    print("\n==== 診断: 0.05刻みサブ帯 × 累積回収率(PL上位k点) ====")
    hdr2 = " ".join(f"k{k}" for k in K2_GRID)
    print(f"  帯          n    1着率   2連単: {hdr2}")
    for lo, hi in FINE:
        rs = sub(rows, lo, hi)
        n = len(rs)
        cells = " ".join(f(roi2(rs, k)[0]) for k in K2_GRID)
        print(f"  {lo:.2f}-{hi:.2f} {n:5d}        {cells}")
    print(f"\n  帯          n3   3連単: " + " ".join(f"k{k:<2d}" for k in K3_GRID if k))
    for lo, hi in FINE:
        rs = sub(rows, lo, hi)
        n3 = sum(1 for r in rs if r["has3"])
        cells = " ".join(f(roi3(rs, k)[0]) for k in K3_GRID if k)
        print(f"  {lo:.2f}-{hi:.2f} {n3:5d}       {cells}")

    # ---- 2) 3連単の1点刻み限界回収率（点数の崖の位置を見る）----
    print("\n==== 3連単: 追加1点ごとの限界回収率（その1点だけの回収率）====")
    print("  帯          " + " ".join(f"p{i:<2d}" for i in range(1, 15)))
    for lo, hi in FINE:
        rs = [r for r in sub(rows, lo, hi) if r["has3"]]
        n3 = len(rs)
        cells = []
        for i in range(1, 15):
            pay = sum(r["p3"] for r in rs if r["r3"] == i - 1)
            cells.append(f(pay / (n3 * 100) * 100 if n3 else None))
        print(f"  {lo:.2f}-{hi:.2f} " + " ".join(cells))
    print("  ※各点の単独回収率。100%超の点だけ買うのが理想（勝者の呪いに注意）")

    print("\n==== 2連単: 追加1点ごとの限界回収率 ====")
    print("  帯          " + " ".join(f"p{i:<2d}" for i in range(1, 7)))
    for lo, hi in FINE:
        rs = sub(rows, lo, hi)
        n = len(rs)
        cells = []
        for i in range(1, 7):
            pay = sum(r["p2"] for r in rs if r["r2"] == i - 1)
            cells.append(f(pay / (n * 100) * 100 if n else None))
        print(f"  {lo:.2f}-{hi:.2f} " + " ".join(cells))

    # ---- 3) 買い目構成(builder)比較: サブ帯 × k3 ----
    print("\n==== 3連単の買い目構成比較（topk=PL上位 / axis=本命1着固定 / std30=穴3割混合）====")
    print("  帯         k3   topk   axis  std30")
    for lo, hi in FINE:
        rs = sub(rows, lo, hi)
        for k in BUILDER_K3:
            cells = " ".join(f(roi3(rs, k, b)[0]) for b in BUILDERS)
            print(f"  {lo:.2f}-{hi:.2f} {k:3d} {cells}")

    # ---- 4) 現行ポリシー基準値 ----
    print("\n==== 現行ポリシー（0.50境界で2連3/4点・3連7/10点）====")
    for lo, hi in FINE:
        rs = sub(rows, lo, hi)
        roi, stake, pay = roi_current(rs)
        print(f"  {lo:.2f}-{hi:.2f}  回収 {f(roi)}%  賭け金 ¥{stake:,}")
    roi, stake, pay = roi_current(rows)
    print(f"  標準帯全体  回収 {f(roi)}%  賭け金 ¥{stake:,} / 払戻 ¥{pay:,}")


if __name__ == "__main__":
    main()
