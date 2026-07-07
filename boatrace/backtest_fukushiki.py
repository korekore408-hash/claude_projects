# -*- coding: utf-8 -*-
"""3連複・2連複のバックテスト（未検証だった券種）。

K-fileの 3連複_配当/2連複_配当 を使い、PL確率を組合せ(順不同)に畳んで
上位k点を購入した場合の的中率・回収率を帯別(API本命確率)に検証する。
順位系統は本番同一の学習モデル(model)と API の両方。

  py -3 backtest_fukushiki.py
  py -3 backtest_fukushiki.py --since 2026-05-01
"""
import argparse
import glob
import re
from itertools import combinations

import build_today as B
import backtest as BT

K3F_GRID = [1, 2, 3, 4, 5, 7, 10]     # 3連複(全20点)
K2F_GRID = [1, 2, 3, 4, 5]            # 2連複(全15点)
BANDS = [("鉄板", 0.65, 9.9), ("標準", 0.45, 0.65), ("穴 ", -1, 0.45)]


def load_payouts_fuku(keep):
    """K-fileから race_id -> (3連複配当, 2連複配当, 3連複組合, 2連複組合)。"""
    yy = {d[2:4] + d[5:7] + d[8:10] for d in keep}
    payout = {}
    for kp in glob.glob(r"data\k*.csv"):
        m = re.search(r"k(\d{6})", kp)
        if not m or m.group(1) not in yy:
            continue
        for r in B.load(kp):
            code = B.VENUE_CODE.get(r["会場"], "00")
            y, mo, dd = r["日付"].split("/")
            rid = f"{code}{int(y):04d}{int(mo):02d}{int(dd):02d}{int(r['レース']):02d}"
            if rid in payout:
                continue
            def yen(col):
                v = str(r.get(col, "")).replace(",", "").strip()
                return int(v) if v.isdigit() else 0
            def combo(col):
                s = str(r.get(col, "")).strip()
                ps = s.split("-")
                if all(p.isdigit() for p in ps) and len(ps) >= 2:
                    return frozenset(int(p) for p in ps)
                return None
            payout[rid] = (yen("3連複_配当"), yen("2連複_配当"),
                           combo("3連複_組合"), combo("2連複_組合"))
    return payout


def combo_order(s, kind):
    """PL確率を順不同組合せに畳んで降順。[(frozenset, prob)]"""
    acc = {}
    for c, p in BT.pl_order(s, kind, set()):
        k = frozenset(c)
        acc[k] = acc.get(k, 0.0) + p
    return sorted(acc.items(), key=lambda x: x[1], reverse=True)


def collect(races, hon_canon, payout, system):
    rows = []
    for rid, rc in races.items():
        if len(rc["fin"]) != 6:
            continue
        s = [rc[system].get(w) for w in range(1, 7)]
        if any(x is None for x in s):
            continue
        hon = hon_canon.get(rid)
        if hon is None:
            continue
        po = payout.get(rid)
        if po is None or po[2] is None:
            continue          # 3連複配当なし(欠場等)は除外
        o3 = [k for k, _ in combo_order(s, 3)]
        o2 = [k for k, _ in combo_order(s, 2)]
        r3 = o3.index(po[2]) if po[2] in o3 else None
        r2 = (o2.index(po[3]) if po[3] is not None and po[3] in o2 else None)
        rows.append({"d": rc["d"], "hon": hon, "r3": r3, "p3": po[0],
                     "r2": r2, "p2": po[1], "has2": po[3] is not None})
    return rows


def roi(rs, k, rk, pk, has=None):
    rs = [r for r in rs if has is None or r[has]]
    if not rs or k <= 0:
        return None, None, 0
    hit = [r for r in rs if r[rk] is not None and r[rk] < k]
    pay = sum(r[pk] for r in hit)
    stake = k * 100 * len(rs)
    return pay / stake * 100, len(hit) / len(rs) * 100, stake


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
    races, hon_api, _, _ = BT.load_races(args.rel, args.pred, args.hist, args.since)
    keep = sorted({rc["d"] for rc in races.values()})
    payout = load_payouts_fuku(keep)

    for system in ("model", "api"):
        rows = collect(races, hon_api, payout, system)
        sname = "学習モデル" if system == "model" else "API合成"
        print(f"\n######## 順位系統={sname}  {len(rows)}R ########")
        print("== 3連複(全20点) 上位k点の 回収率% / 的中率% ==")
        print("  帯      n     " + "   ".join(f"k={k}" for k in K3F_GRID))
        for lab, lo, hi in BANDS:
            rs = [r for r in rows if lo <= r["hon"] < hi]
            cells = []
            for k in K3F_GRID:
                v, h, _ = roi(rs, k, "r3", "p3")
                cells.append(f"{f(v)}/{h:4.1f}" if v is not None else "    –    ")
            print(f"  {lab} {len(rs):6d} " + " ".join(cells))
        rs = rows
        cells = []
        for k in K3F_GRID:
            v, h, _ = roi(rs, k, "r3", "p3")
            cells.append(f"{f(v)}/{h:4.1f}" if v is not None else "    –    ")
        print(f"  全体 {len(rs):6d} " + " ".join(cells))

        print("== 2連複(全15点) 上位k点の 回収率% / 的中率% ==")
        print("  帯      n     " + "   ".join(f"k={k}" for k in K2F_GRID))
        for lab, lo, hi in BANDS:
            rs = [r for r in rows if lo <= r["hon"] < hi]
            cells = []
            for k in K2F_GRID:
                v, h, _ = roi(rs, k, "r2", "p2", has="has2")
                cells.append(f"{f(v)}/{h:4.1f}" if v is not None else "    –    ")
            print(f"  {lab} {len(rs):6d} " + " ".join(cells))

    print("\n※的中率=購入レースのうち当たったレース割合。回収率=Σ配当/(点数×100円)。")
    print("※F・返還は未考慮（既存backtest.pyと同条件）。帯=API本命確率で共通。")


if __name__ == "__main__":
    main()
