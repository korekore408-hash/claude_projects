# -*- coding: utf-8 -*-
"""任意の『API人気順N番手』を軸固定した絞り買いの検討（本番非搭載）。

--axis 2 = 2番手 / --axis 4 = 穴候補(today.htmlの穴筆頭=APIスコア4番人気)。

出力:
  A) 軸艇が「来る」確率: 1着 / 連対(top2) / 複勝(top3)。
     全体・本命確率帯別・軸艇自身のAPI勝率帯別。
  B) 軸艇を固定した絞り買いのバックテスト（何点から→的中率/回収率）。
"""
import argparse
import itertools
from collections import defaultdict

import build_today as B


def load_all(rel_path, pred_path, hist_path, since):
    rel = B.load(rel_path)
    pred = {(r["race_id"], r["枠番"]): r for r in B.load(pred_path)}
    api_map = B.build_api_scores(rel)
    races = {}
    for r in rel:
        if r["日付"] < since:
            continue
        rid = r["race_id"]
        try:
            w = int(r["枠番"])
        except (ValueError, KeyError):
            continue
        pr = pred.get((rid, r["枠番"]), {})
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            fin = None
        rc = races.setdefault(rid, {"d": r["日付"], "fin": {}, "api": {}})
        rc["fin"][w] = fin
        rc["api"][w] = api_map.get((rid, w))
    payout = B.load_payouts(sorted({r["日付"] for r in rel if r["日付"] >= since}))
    return races, payout


def iter_races(races):
    for rid, rc in races.items():
        if len(rc["fin"]) != 6:
            continue
        s = [rc["api"].get(w) for w in range(1, 7)]
        if any(x is None for x in s):
            continue
        fins = [rc["fin"][w] for w in range(1, 7)]
        if any(f is None for f in fins):
            continue
        order = sorted([w for w in range(1, 7) if fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < 3 or fins[order[0] - 1] != 1:
            continue
        ranks = sorted(range(1, 7), key=lambda w: s[w - 1], reverse=True)
        yield rid, rc, s, order, ranks


def pct(x, n):
    return round(x / n * 100, 1) if n else 0.0


def section_prob(races, axis):
    def newrow():
        return [0, 0, 0, 0]

    overall = newrow()
    by_hon = defaultdict(newrow)
    by_pa = defaultdict(newrow)

    def honlab(h):
        return "鉄板≥65" if h >= 0.65 else ("標準45-65" if h >= 0.45 else
               ("穴35-45" if h >= 0.35 else "大穴<35"))

    def palab(p):
        return "<8%" if p < 0.08 else ("8-12%" if p < 0.12 else
               ("12-16%" if p < 0.16 else "≥16%"))

    for rid, rc, s, order, ranks in iter_races(races):
        axw = ranks[axis - 1]
        hon = s[ranks[0] - 1]
        pa = s[axw - 1]
        pos = order.index(axw) if axw in order else 99
        for row in (overall, by_hon[honlab(hon)], by_pa[palab(pa)]):
            row[0] += 1
            row[1] += pos == 0
            row[2] += pos <= 1
            row[3] += pos <= 2

    def show(title, d, keys):
        print(f"\n── {title} ──")
        print("  区分           R数    軸1着   連対(top2) 複勝(top3)")
        for k in keys:
            if k not in d:
                continue
            n, h1, h2, h3 = d[k]
            print(f"  {k:<12s} {n:6d}   {pct(h1,n):5.1f}%   {pct(h2,n):5.1f}%   {pct(h3,n):5.1f}%")

    n, h1, h2, h3 = overall
    print(f"\n========== A) 軸=API{axis}番人気 が来る確率 ==========")
    print(f"\n全体 {n}R : 1着 {pct(h1,n)}% / 連対 {pct(h2,n)}% / 複勝 {pct(h3,n)}%")
    show("本命確率(1番人気の強さ)別", by_hon, ["鉄板≥65", "標準45-65", "穴35-45", "大穴<35"])
    show("軸艇自身のAPI勝率別", by_pa, ["<8%", "8-12%", "12-16%", "≥16%"])


# 軸艇=ranks[axis-1] を固定した買い目
def bet_head_2ren(s, ranks, axis, k):
    ax = ranks[axis - 1]
    others = [w for w in ranks if w != ax][:k]
    return [(ax, x) for x in others]


def bet_2nd_2ren(s, ranks, axis, k):
    ax = ranks[axis - 1]
    others = [w for w in ranks if w != ax][:k]
    return [(x, ax) for x in others]


def bet_both_2ren(s, ranks, axis, k):
    return bet_head_2ren(s, ranks, axis, k) + bet_2nd_2ren(s, ranks, axis, k)


def bet_head_3ren(s, ranks, axis, k):
    ax = ranks[axis - 1]
    others = [w for w in ranks if w != ax][:k]
    return [(ax, a, b) for a, b in itertools.permutations(others, 2)]


def bet_head_3ren_topfix(s, ranks, axis, k):
    """3連単 軸アタマ・2-3着は上位人気(本命含む)k艇に固定流し。点数=k(k-1)。"""
    ax = ranks[axis - 1]
    tops = [w for w in ranks if w != ax][:k]
    return [(ax, a, b) for a, b in itertools.permutations(tops, 2)]


STRATS = {
    "2連 軸頭流し": (bet_head_2ren, 2),
    "2連 軸2着流し": (bet_2nd_2ren, 2),
    "2連 両建て": (bet_both_2ren, 2),
    "3連 軸頭流し": (bet_head_3ren, 3),
}


def section_bet(races, payout, axis, kgrid, hon_lo, hon_hi, tag):
    print(f"\n========== B) 軸=API{axis}番人気の絞り買い（{tag}）==========")
    print(" 戦略             k  点数  的中%   回収%   平均配当  R数")
    print(" " + "-" * 62)
    for sname, (fn, kind) in STRATS.items():
        for k in kgrid:
            inv = ret = nbet = nhit = pts = paysum = 0
            for rid, rc, s, order, ranks in iter_races(races):
                hon = s[ranks[0] - 1]
                if not (hon_lo <= hon < hon_hi):
                    continue
                combos = list(dict.fromkeys(fn(s, ranks, axis, k)))
                if not combos:
                    continue
                actual = tuple(order[:kind])
                po = payout.get(rid, (0, 0))[kind - 2]
                hit = actual in set(combos)
                inv += len(combos) * 100
                pts += len(combos)
                nbet += 1
                if hit:
                    ret += po
                    nhit += 1
                    paysum += po
            if nbet == 0:
                continue
            print(f" {sname:<14s} {k:1d} {pts/nbet:4.0f}  {pct(nhit,nbet):5.1f}% "
                  f"{pct(ret,inv):6.1f}%  ¥{(paysum/nhit if nhit else 0):6.0f}  {nbet:5d}")
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--hist", default="features_player_history.csv")
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--axis", type=int, default=4, help="API人気順(2=2番手,4=穴候補)")
    ap.add_argument("--kgrid", default="2,3,4,5")
    args = ap.parse_args()

    print(f"読込 since={args.since} axis=API{args.axis}番人気 …")
    races, payout = load_all(args.rel, args.pred, args.hist, args.since)
    print(f"対象レース {sum(1 for _ in iter_races(races))} / 配当 {len(payout)}")

    section_prob(races, args.axis)
    kgrid = [int(x) for x in args.kgrid.split(",")]
    section_bet(races, payout, args.axis, kgrid, 0.0, 1.0, "全レース")
    section_bet(races, payout, args.axis, kgrid, 0.0, 0.45, "穴帯 本命<45%")
    section_bet(races, payout, args.axis, kgrid, 0.0, 0.35, "大穴帯 本命<35%")
    print("\n※回収%>100で長期プラス（控除率約25%＝無選別の理論線≒75%）。")
    print("  『軸頭』=軸艇を1着固定。『軸2着』=軸艇を2着固定。点数は1レース平均。")


if __name__ == "__main__":
    main()
