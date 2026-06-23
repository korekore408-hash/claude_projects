# -*- coding: utf-8 -*-
"""配当倍率の帯（特に100〜300倍）別に、的中率・予想区分（本命/標準/波乱）の相関を分析。
predict_win.csv(p_win+finish_rank) + K-file配当。3連単中心、2連単も併記。
使い方: py -3.13 analyze_payout_band.py [--since 20260101] [--oos 20260501]"""
import argparse
import itertools

from build_today import (load, load_payouts, to_float, k_ex, k_tri,
                         bet_exclude, _pl_prob, _pl_rank)
from features_player_history import VENUE_CODE
import glob
import re


def all_dates_with_k():
    ds = set()
    for kp in glob.glob("data/k*.csv"):
        m = re.search(r"k(\d{6})", kp)
        if m:
            ds.add("20" + m.group(1)[:2] + "-" + m.group(1)[2:4] + "-" + m.group(1)[4:6])
    return sorted(ds)


def band_of(odds):
    """配当(円)→倍率帯ラベル。"""
    x = odds / 100.0
    if x < 10: return "①<10倍"
    if x < 30: return "②10-30倍"
    if x < 50: return "③30-50倍"
    if x < 100: return "④50-100倍"
    if x < 300: return "⑤100-300倍"
    return "⑥300倍+"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20260101")
    ap.add_argument("--oos", default="20260501", help="この日以降のみ集計（純OOS）。0で全期間")
    args = ap.parse_args()
    since = args.oos if args.oos != "0" else args.since

    pred = {(r["race_id"], r["枠番"]): r for r in load("predict_win.csv")}
    meta = {(r["race_id"], r["枠番"]): r for r in load("features_race_relative.csv")}
    hist = {(r["race_id"], r["枠番"]): r for r in load("features_player_history.csv")}
    payout = load_payouts(all_dates_with_k())

    # レース単位に再構成
    races = {}
    for (rid, w), pr in pred.items():
        if rid[2:10] < since:
            continue
        rc = races.setdefault(rid, {"b": {}, "cl": {}, "lw": {}})
        rc["b"][int(w)] = (to_float(pr.get("p_win")), _int(pr.get("finish_rank")))
        m = meta.get((rid, w), {})
        rc["cl"][int(w)] = to_float(m.get("class_ord"))
        rc["lw"][int(w)] = to_float(hist.get((rid, w), {}).get("lane_win_rate"))

    BANDS = ["①<10倍", "②10-30倍", "③30-50倍", "④50-100倍", "⑤100-300倍", "⑥300倍+"]
    REG = ["鉄板", "標準", "穴"]
    # band -> {n, hit3, hit2, reg{}, hon_sum}
    agg = {b: {"n": 0, "hit3": 0, "hit2": 0, "reg": {r: 0 for r in REG},
               "reghit3": {r: [0, 0] for r in REG}, "hon": 0.0} for b in BANDS}
    tot = 0
    for rid, rc in races.items():
        if len(rc["b"]) != 6:
            continue
        s = [rc["b"][w][0] for w in range(1, 7)]
        fins = [rc["b"][w][1] for w in range(1, 7)]
        if any(x is None for x in s):
            continue
        order = sorted([w for w in range(1, 7) if fins[w - 1] and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < 3 or fins[order[0] - 1] != 1:
            continue
        po = payout.get(rid)
        if not po or po[1] <= 0:
            continue
        band = band_of(po[1])           # 3連単配当で帯を決める
        hon = max(s)
        reg = "鉄板" if hon >= 0.65 else ("穴" if hon < 0.45 else "標準")
        excl = {e[0] for e in bet_exclude(rc["cl"], rc["lw"].get(1), hon)}
        kt = k_tri(hon); kx = k_ex(hon)
        m = sum(1 for w in range(1, 7) if s[w - 1] and s[w - 1] > 0 and w not in excl)
        bet3 = min(kt, m * (m - 1) * (m - 2)); bet2 = min(kx, m * (m - 1))
        h3 = bet3 > 0 and _pl_rank(s, 3, tuple(order[:3]), excl) <= bet3
        h2 = bet2 > 0 and _pl_rank(s, 2, tuple(order[:2]), excl) <= bet2
        a = agg[band]
        a["n"] += 1; tot += 1
        a["hit3"] += h3; a["hit2"] += h2
        a["reg"][reg] += 1; a["hon"] += hon
        a["reghit3"][reg][0] += 1
        a["reghit3"][reg][1] += h3

    print(f"=== 3連単配当の帯別 分析（{since}以降, {tot}R）===")
    print(f"{'帯':<11}{'R数':>6}{'3連単的中':>9}{'2連単的中':>9}{'平均本命%':>9}   予想区分内訳(鉄板/標準/穴)")
    for b in BANDS:
        a = agg[b]
        if not a["n"]:
            continue
        n = a["n"]
        r3 = a["hit3"] / n * 100; r2 = a["hit2"] / n * 100
        hon = a["hon"] / n * 100
        rg = a["reg"]
        print(f"{b:<11}{n:>6}{r3:>8.1f}%{r2:>8.1f}%{hon:>8.1f}%   "
              f"{rg['鉄板']/n*100:>4.0f}/{rg['標準']/n*100:>3.0f}/{rg['穴']/n*100:>3.0f}%")

    print(f"\n=== ⑤100-300倍 の中身：予想区分ごとの的中率 ===")
    a = agg["⑤100-300倍"]
    n = a["n"]
    print(f"  該当 {n}R（全{tot}Rの{n/tot*100:.1f}%）, 平均本命確率 {a['hon']/n*100:.1f}%")
    print(f"  {'予想区分':<6}{'該当R数':>7}{'構成%':>7}{'3連単的中率':>11}")
    for r in REG:
        cnt = a["reg"][r]
        hh = a["reghit3"][r]
        hr = hh[1] / hh[0] * 100 if hh[0] else 0
        print(f"  {r:<6}{cnt:>7}{cnt/n*100:>6.1f}%{hr:>10.1f}%")

    # 相関の要約（本命確率 vs 100-300倍フラグ / 高配当フラグ）
    print(f"\n=== 区分×帯クロス（行=予想区分, 各帯の構成%）===")
    cross = {r: {b: 0 for b in BANDS} for r in REG}
    rtot = {r: 0 for r in REG}
    for b in BANDS:
        for r in REG:
            cross[r][b] += agg[b]["reg"][r]
            rtot[r] += agg[b]["reg"][r]
    print(f"{'区分':<6}" + "".join(f"{b[1:]:>9}" for b in BANDS))
    for r in REG:
        if not rtot[r]:
            continue
        print(f"{r:<6}" + "".join(f"{cross[r][b]/rtot[r]*100:>8.1f}%" for b in BANDS))


def _int(s):
    try:
        return int(str(s).strip())
    except (ValueError, AttributeError, TypeError):
        return None


if __name__ == "__main__":
    main()
