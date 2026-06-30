# -*- coding: utf-8 -*-
"""2番手(APIスコア2番人気)を軸に固定した絞り買いの検討（本番非搭載）。

出力:
  A) 2番手が「来る」確率: 1着 / 連対(top2) / 複勝圏(top3)。
     全体・本命確率帯別・2番手自身のAPI勝率帯別・1-2番手スコア差帯別。
  B) 2番手を軸固定した絞り買いのバックテスト（点数と回収率）:
     - 2連単 2番手アタマ流し / 2番手2着流し / 両建て
     - 3連単 2番手アタマ固定の流し（相手を上位K艇に絞る）
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
    """有効レースを (rc, s, order, ranks) で返す。
    ranks=APIスコア降順の枠list（ranks[0]=1番人気, ranks[1]=2番手…）。"""
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


def section_prob(races):
    """2番手の来場確率テーブル。"""
    def newrow():
        return [0, 0, 0, 0]   # n, 1着, top2, top3

    overall = newrow()
    by_hon = defaultdict(newrow)
    by_p2 = defaultdict(newrow)
    by_gap = defaultdict(newrow)

    def honlab(h):
        return "鉄板≥65" if h >= 0.65 else ("標準45-65" if h >= 0.45 else
               ("穴35-45" if h >= 0.35 else "大穴<35"))

    def p2lab(p):
        return "<15%" if p < 0.15 else ("15-20%" if p < 0.20 else
               ("20-25%" if p < 0.25 else "≥25%"))

    def gaplab(g):
        return "僅差<5%" if g < 0.05 else ("5-12%" if g < 0.12 else
               ("12-20%" if g < 0.20 else "歴然≥20%"))

    for rid, rc, s, order, ranks in iter_races(races):
        fav, sec = ranks[0], ranks[1]
        hon = s[fav - 1]
        p2 = s[sec - 1]
        gap = hon - p2
        pos = order.index(sec) if sec in order else 99
        hit1 = pos == 0
        hit2 = pos <= 1
        hit3 = pos <= 2
        for row in (overall, by_hon[honlab(hon)], by_p2[p2lab(p2)],
                    by_gap[gaplab(gap)]):
            row[0] += 1
            row[1] += hit1
            row[2] += hit2
            row[3] += hit3

    def show(title, d, order_keys):
        print(f"\n── {title} ──")
        print("  区分           R数    2番手1着  連対(top2) 複勝(top3)")
        for k in order_keys:
            if k not in d:
                continue
            n, h1, h2, h3 = d[k]
            print(f"  {k:<12s} {n:6d}    {pct(h1,n):5.1f}%   {pct(h2,n):5.1f}%   {pct(h3,n):5.1f}%")

    print("\n========== A) 2番手(API2番人気)が来る確率 ==========")
    n, h1, h2, h3 = overall
    print(f"\n全体 {n}R : 1着 {pct(h1,n)}% / 連対 {pct(h2,n)}% / 複勝 {pct(h3,n)}%")
    show("本命確率(1番人気の強さ)別", by_hon,
         ["鉄板≥65", "標準45-65", "穴35-45", "大穴<35"])
    show("2番手自身のAPI勝率別", by_p2, ["<15%", "15-20%", "20-25%", "≥25%"])
    show("1-2番手スコア差別", by_gap, ["僅差<5%", "5-12%", "12-20%", "歴然≥20%"])


# ───────── B) 2番手軸固定の絞り買い ─────────
def bet_head2_2ren(s, ranks, k):
    """2連単 2番手アタマ流し: (2番手→相手). 相手=2番手以外のAPI上位k艇。点数=k。"""
    sec = ranks[1]
    others = [w for w in ranks if w != sec][:k]
    return [(sec, x) for x in others]


def bet_2nd2_2ren(s, ranks, k):
    """2連単 2番手2着流し: (相手→2番手). 相手=2番手以外の上位k艇。点数=k。"""
    sec = ranks[1]
    others = [w for w in ranks if w != sec][:k]
    return [(x, sec) for x in others]


def bet_both2_2ren(s, ranks, k):
    """2番手アタマ+2着の両建て。点数=2k。"""
    return bet_head2_2ren(s, ranks, k) + bet_2nd2_2ren(s, ranks, k)


def bet_head2_3ren(s, ranks, k):
    """3連単 2番手アタマ固定流し: 1着=2番手、2-3着=他の上位k艇の順列。点数=k(k-1)。"""
    sec = ranks[1]
    others = [w for w in ranks if w != sec][:k]
    return [(sec, a, b) for a, b in itertools.permutations(others, 2)]


def bet_12_3ren(s, ranks, k):
    """3連単 1-2番手で1-2着を占める2点軸、3着=他の上位k艇流し。点数=2k。"""
    fav, sec = ranks[0], ranks[1]
    thirds = [w for w in ranks if w not in (fav, sec)][:k]
    out = [(sec, fav, t) for t in thirds] + [(fav, sec, t) for t in thirds]
    return out


STRATS = {
    "2連 2番手頭(k)": (bet_head2_2ren, 2),
    "2連 2番手2着(k)": (bet_2nd2_2ren, 2),
    "2連 両建て(2k)": (bet_both2_2ren, 2),
    "3連 2番手頭流し(k(k-1))": (bet_head2_3ren, 3),
    "3連 1-2番手BOX×3着(2k)": (bet_12_3ren, 3),
}


def section_bet(races, payout, kgrid, hon_lo, hon_hi, p2min=0.0):
    band = f"本命{int(hon_lo*100)}-{int(hon_hi*100) if hon_hi<1 else 100}%"
    if p2min > 0:
        band += f"・2番手API≥{int(p2min*100)}%"
    print(f"\n========== B) 2番手軸の絞り買い（対象帯: {band}）==========")
    print(" 戦略                       k  点数  的中%   回収%   平均配当  R数")
    print(" " + "-" * 74)
    for sname, (fn, kind) in STRATS.items():
        for k in kgrid:
            inv = ret = nbet = nhit = pts_sum = paysum = 0
            for rid, rc, s, order, ranks in iter_races(races):
                hon = s[ranks[0] - 1]
                if not (hon_lo <= hon < hon_hi):
                    continue
                if s[ranks[1] - 1] < p2min:
                    continue
                combos = list(dict.fromkeys(fn(s, ranks, k)))
                if not combos:
                    continue
                actual = tuple(order[:kind])
                po = payout.get(rid, (0, 0))[kind - 2]
                hit = actual in set(combos)
                inv += len(combos) * 100
                pts_sum += len(combos)
                nbet += 1
                if hit:
                    ret += po
                    nhit += 1
                    paysum += po
            if nbet == 0:
                continue
            avgpts = pts_sum / nbet
            avgpay = paysum / nhit if nhit else 0
            print(f" {sname:<26s} {k:1d} {avgpts:4.0f}  {pct(nhit,nbet):5.1f}% "
                  f"{pct(ret,inv):6.1f}%  ¥{avgpay:6.0f}  {nbet:5d}")
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--hist", default="features_player_history.csv")
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--kgrid", default="2,3,4")
    args = ap.parse_args()

    print(f"読込 since={args.since} …")
    races, payout = load_all(args.rel, args.pred, args.hist, args.since)
    print(f"対象レース {sum(1 for _ in iter_races(races))} / 配当 {len(payout)}")

    section_prob(races)

    kgrid = [int(x) for x in args.kgrid.split(",")]
    # 帯別に絞り買いを評価（全体/標準/穴/大穴）
    section_bet(races, payout, kgrid, 0.0, 1.0)      # 全レース
    section_bet(races, payout, kgrid, 0.45, 0.65)    # 標準帯
    section_bet(races, payout, kgrid, 0.0, 0.45)     # 穴帯
    section_bet(races, payout, kgrid, 0.0, 1.0, p2min=0.20)   # 2番手が強い時だけ
    print("\n※回収%>100で長期プラス（控除率約25%＝無選別の理論線≒75%）。")
    print("  『2番手頭』=2番手を1着に固定。『2番手2着』=2番手を2着に固定。点数は1レース平均。")


if __name__ == "__main__":
    main()
