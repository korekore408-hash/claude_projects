# -*- coding: utf-8 -*-
"""標準帯(本命確率0.45-0.65)の「見送り境界」探索（2連複ベース＝現行本番）。

標準帯の分割は回収率不変だった([[backtest_stdsplit]])が、帯の中に
「買わない方がよい層」があるかを複数の軸で検証する。各軸をビンに切り、
①ビン別の2連複回収率 ②そのビンを丸ごと見送った時の残り回収率と賭け金
を出す。100%超は望めないので、判断基準は「明確に低い層を外して残りが上がるか」。

軸: hon(本命確率0.025刻み) / rspread(相手の差=非本命5艇スコアのばらつき) /
    hongap(本命-2番手のスコア差) / favlane(本命枠) / topneed(1点目2連複の必要オッズ=1/p) /
    fss(field_strength_std)。順位系統=model(本番)/api。

  py -3 backtest_stdskip.py
  py -3 backtest_stdskip.py --since 2026-05-01   # OOS安定性
"""
import argparse
import statistics as st

import build_today as B
import backtest as BT
import backtest_fukushiki as F


def collect(races, hon_api, payout, system, rel_fss):
    rows = []
    for rid, rc in races.items():
        if len(rc["fin"]) != 6:
            continue
        s = [rc[system].get(w) for w in range(1, 7)]
        ab = [rc["api"].get(w) for w in range(1, 7)]
        if any(x is None for x in s) or any(x is None for x in ab):
            continue
        hon = hon_api.get(rid)
        if hon is None or not (0.45 <= hon < 0.65):
            continue
        po = payout.get(rid)
        # load_payouts_fuku: po=(3連複配当, 2連複配当, 3連複組, 2連複組)
        if po is None or not po[1]:
            continue                       # 2連複配当なし/0
        fins = [rc["fin"][w] for w in range(1, 7)]
        order = sorted([w for w in range(1, 7)
                        if fins[w - 1] is not None and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < 2 or fins[order[0] - 1] != 1:
            continue
        # 2連複: PL確率を順不同ペアに畳んで上位k。的中順位 r2。
        pairs = _pf_order(s)
        act = tuple(sorted(order[:2]))
        r2 = next((i for i, (c, _) in enumerate(pairs) if c == act), None)
        # 軸（順位系統 s のスコアで算出）
        sd = sorted(s, reverse=True)
        favlane = max(range(6), key=lambda i: s[i]) + 1
        riv = [s[i] for i in range(6) if i + 1 != favlane]
        rspread = st.pstdev(riv) / (sum(s) or 1)              # 0-1正規化
        hongap = (sd[0] - sd[1]) / (sum(s) or 1)
        topneed = 1.0 / pairs[0][1] if pairs and pairs[0][1] > 0 else None  # 1点目の必要オッズ
        rows.append({"d": rc["d"], "hon": hon, "r2": r2, "p2f": po[1],
                     "rspread": rspread, "hongap": hongap, "favlane": favlane,
                     "topneed": topneed, "fss": rel_fss.get(rid)})
    return rows


def _pf_order(s):
    idx = [i + 1 for i in range(6) if s[i] and s[i] > 0]
    def pf(a, b):
        return B._pl_prob(s, [a, b]) + B._pl_prob(s, [b, a])
    pairs = [((min(a, b), max(a, b)), pf(a, b))
             for i, a in enumerate(idx) for b in idx[i + 1:]]
    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs


def roi(rows, kfn=None):
    """kfn(hon)=点数（既定=現行kEx）。2連複回収率・的中率・賭け金・n。"""
    kfn = kfn or B.k_ex
    pay = stake = hit = 0
    for r in rows:
        k = kfn(r["hon"])
        stake += k * 100
        if r["r2"] is not None and r["r2"] < k:
            pay += r["p2f"]
            hit += 1
    n = len(rows)
    return (pay / stake * 100 if stake else 0, hit / n * 100 if n else 0, stake, n)


def qbins(rows, key, nq=5):
    """key の値でnq分位に分割（Noneは除外）。境界と各ビンrows。"""
    vals = sorted(r[key] for r in rows if r[key] is not None)
    if len(vals) < nq * 20:
        nq = max(2, len(vals) // 40)
    if nq < 2:
        return []
    cuts = [vals[int(len(vals) * i / nq)] for i in range(1, nq)]
    bins = [[] for _ in range(nq)]
    for r in rows:
        v = r[key]
        if v is None:
            continue
        b = 0
        while b < nq - 1 and v >= cuts[b]:
            b += 1
        bins[b].append(r)
    return list(zip(range(nq), bins)), cuts


def show_axis(rows, key, label, nq=5):
    res = qbins(rows, key, nq)
    if not res:
        print(f"\n【{label}】データ不足")
        return
    bins, cuts = res
    base_r, base_h, base_s, base_n = roi(rows)
    print(f"\n【{label}】標準帯全体 回収{base_r:.1f}% 的中{base_h:.1f}% n={base_n}")
    print(f"  境界値: {[round(c,3) for c in cuts]}")
    print("  ビン   n    回収%  的中%   このビンを見送ると→ 残り回収% (賭け金比)")
    for bi, br in bins:
        if not br:
            continue
        r, h, s, n = roi(br)
        rest = [x for x in rows if x not in br]
        rr, rh, rs, rn = roi(rest)
        print(f"  {bi+1}/{nq}  {n:4d}  {r:6.1f} {h:5.1f}    残り {rr:6.1f}% ({rs/base_s*100:3.0f}%)  Δ{rr-base_r:+.1f}pt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    args = ap.parse_args()

    print(f"データ読込 since={args.since} …")
    races, hon_api, _, _ = BT.load_races(
        "features_race_relative.csv", "predict_win.csv",
        "features_player_history.csv", args.since)
    keep = sorted({rc["d"] for rc in races.values()})
    payout = F.load_payouts_fuku(keep)
    # field_strength_std を rel から
    rel = B.load("features_race_relative.csv")
    rel_fss = {}
    for r in rel:
        v = B.to_float(r.get("field_strength_std"))
        if v is not None:
            rel_fss[r["race_id"]] = v

    for system in ("model", "api"):
        rows = collect(races, hon_api, payout, system, rel_fss)
        print(f"\n{'='*66}\n順位系統={system}  標準帯2連複 {len(rows)}R"
              f"（{min(r['d'] for r in rows)}〜{max(r['d'] for r in rows)}）\n{'='*66}")
        show_axis(rows, "hon", "本命確率(生値)", 5)
        show_axis(rows, "rspread", "相手の差（非本命5艇のばらつき・小=横一線）", 5)
        show_axis(rows, "hongap", "本命-2番手のスコア差", 5)
        show_axis(rows, "topneed", "1点目2連複の必要オッズ(1/p・大=堅くない)", 5)
        show_axis(rows, "fss", "field_strength_std（メンバー実力差）", 5)
        # 本命枠は離散
        print("\n【本命枠（順位系統の1番手）】")
        base_r, _, base_s, _ = roi(rows)
        for lane in range(1, 7):
            br = [r for r in rows if r["favlane"] == lane]
            if len(br) < 30:
                continue
            r, h, s, n = roi(br)
            rest = [x for x in rows if x["favlane"] != lane]
            rr, _, rs, _ = roi(rest)
            print(f"  {lane}号艇本命 n={n:4d} 回収{r:6.1f}% 的中{h:5.1f}%  見送ると残り{rr:6.1f}%(Δ{rr-base_r:+.1f})")


if __name__ == "__main__":
    main()
