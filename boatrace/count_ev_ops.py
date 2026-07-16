# -*- coding: utf-8 -*-
"""
「EV>1 の買い目は1日にどれくらい出るか」を実オッズで数える。

EV = モデルのPL確率 p × 実オッズ o。各レースの全ボード（2連単30点＋3連単120点）を
走査し、EV>1 の点数を数える。実オッズは data/odds/odds_YYYYMMDD.csv（締切前スナップ）。
モデル strength は predict_win.csv（train≤260430固定＝この期間はhonest OOS）。

さらに「EV>1 を全部¥100で買ったら実際に回収率>100%になるか」も精算し、
EV>1 が“本物の優位”か“モデルの自信過剰”かを検証する。
"""
import csv, glob, os
from collections import defaultdict

ENC = "cp932"
ODDS_DIR = "data/odds"

def load_strength(path="predict_win.csv"):
    boats = defaultdict(dict); ranks = defaultdict(dict)
    with open(path, encoding=ENC) as f:
        r = csv.reader(f); next(r)
        for row in r:
            rid, waku, _t, _p, strength, fin = row[:6]
            try: waku = int(waku); s = float(strength)
            except ValueError: continue
            boats[rid][waku] = s
            try: ranks[rid][int(fin)] = waku
            except ValueError: pass
    races = {}
    for rid, bs in boats.items():
        if len(bs) != 6: continue
        rk = ranks[rid]
        races[rid] = {"s": [bs[w] for w in range(1, 7)],
                      "order": (rk.get(1), rk.get(2), rk.get(3))}
    return races

def pl_prob(s, combo):
    p, rem = 1.0, sum(s)
    for w in combo:
        if rem <= 0: return 0.0
        p *= s[w - 1] / rem; rem -= s[w - 1]
    return p

def main():
    races = load_strength()
    files = sorted(glob.glob(os.path.join(ODDS_DIR, "odds_*.csv")))
    # 日付 -> race_id -> [(bet_type, combo_tuple, odds)]
    byday = defaultdict(lambda: defaultdict(list))
    for fp in files:
        d = os.path.basename(fp)[5:13]
        for row in csv.DictReader(open(fp, encoding="utf-8")):
            try: o = float(row["odds"])
            except (ValueError, KeyError): continue
            combo = tuple(int(x) for x in row["combo"].split("-"))
            byday[d][row["race_id"]].append((row["bet_type"], combo, o))

    THRS = [1.0, 1.2, 1.5, 2.0]
    # EV帯別の実精算（本物の優位が残るか）: [lo,hi) の EV の点だけ買う
    EVBANDS = [(1.0,1.2),(1.2,1.5),(1.5,2.0),(2.0,3.0),(3.0,5.0),(5.0,1e9)]
    band_acc = {b: [0,0,0] for b in EVBANDS}   # [bets, stake, ret]
    thr_acc = {t: [0,0,0] for t in [1.0,1.5,2.0,3.0,5.0]}  # EV>=t 全部買い
    day_rows = []
    tot_stake = tot_ret = 0
    tot_hit = 0
    total_board = 0
    for d in sorted(byday):
        n_race = 0; cnt = {t: {"2t": 0, "3t": 0} for t in THRS}
        races_with_ev1 = 0
        for rid, board in byday[d].items():
            race = races.get(rid)
            if not race: continue
            n_race += 1; total_board += len(board)
            s = race["s"]; order = race["order"]
            has_ev1 = False
            for bt, combo, o in board:
                p = pl_prob(s, combo)
                ev = p * o
                for t in THRS:
                    if ev >= t: cnt[t][bt] += 1
                won = (bt == "2t" and order[:2] == combo) or \
                      (bt == "3t" and order == combo)
                if ev >= 1.0:
                    has_ev1 = True
                    tot_stake += 100
                    if won: tot_ret += o * 100; tot_hit += 1
                    for (lo, hi) in EVBANDS:
                        if lo <= ev < hi:
                            band_acc[(lo,hi)][0]+=1; band_acc[(lo,hi)][1]+=100
                            if won: band_acc[(lo,hi)][2]+=o*100
                            break
                    for t in thr_acc:
                        if ev >= t:
                            thr_acc[t][0]+=1; thr_acc[t][1]+=100
                            if won: thr_acc[t][2]+=o*100
            if has_ev1: races_with_ev1 += 1
        day_rows.append((d, n_race, cnt, races_with_ev1))

    print(f"実オッズのある {len(day_rows)}日 ・ 走査した全買い目 {total_board:,}点\n")
    print(f"{'日付':<10}{'レース数':>7}{'EV>1':>7}{'(2連単':>8}{'3連単)':>7}{'EV>1.2':>8}{'EV>1.5':>8}{'EV>1のあるR':>11}")
    print("-"*70)
    tot = {t: {"2t": 0, "3t": 0} for t in THRS}; tot_r = 0; tot_rev = 0
    for d, nr, cnt, rev in day_rows:
        e1 = cnt[1.0]["2t"] + cnt[1.0]["3t"]
        print(f"{d[:4]}/{d[4:6]}/{d[6:]:<4}{nr:>7}{e1:>7}{cnt[1.0]['2t']:>8}{cnt[1.0]['3t']:>7}"
              f"{cnt[1.2]['2t']+cnt[1.2]['3t']:>8}{cnt[1.5]['2t']+cnt[1.5]['3t']:>8}{rev:>9}/{nr}")
        for t in THRS:
            for bt in ("2t","3t"): tot[t][bt]+=cnt[t][bt]
        tot_r += nr; tot_rev += rev
    print("-"*70)
    nd = len(day_rows)
    e1s = [c[1.0]["2t"]+c[1.0]["3t"] for _,_,c,_ in day_rows]
    print(f"\n【1日あたり EV>1 の発生数】")
    print(f"  平均 {sum(e1s)/nd:.1f}点/日  （2連単 {tot[1.0]['2t']/nd:.1f} + 3連単 {tot[1.0]['3t']/nd:.1f}）")
    print(f"  範囲 {min(e1s)}〜{max(e1s)}点/日  ・ 中央値 {sorted(e1s)[nd//2]}点/日")
    print(f"  1レースあたり {sum(e1s)/tot_r:.2f}点  ・ EV>1が1点以上あるレース {tot_rev}/{tot_r}"
          f"（{tot_rev/tot_r*100:.0f}%）")
    print(f"  厳しめ: EV>1.2 は {(tot[1.2]['2t']+tot[1.2]['3t'])/nd:.1f}点/日 ・ "
          f"EV>1.5 は {(tot[1.5]['2t']+tot[1.5]['3t'])/nd:.1f}点/日 ・ "
          f"EV>2.0 は {(tot[2.0]['2t']+tot[2.0]['3t'])/nd:.1f}点/日")
    print(f"\n【検証】EV>1 を全部¥100で買った実結果")
    print(f"  賭け {tot_stake//100:,}点 / 的中 {tot_hit} / 投資¥{tot_stake:,} / 回収¥{tot_ret:,.0f} "
          f"/ 回収率 {tot_ret/tot_stake*100:.1f}%")
    print("\n  EV帯ごとの実回収率（本物の優位なら100%超で残るはず）:")
    for (lo,hi) in EVBANDS:
        b,st,rt = band_acc[(lo,hi)]
        if st: print(f"    EV {lo:.1f}–{hi if hi<1e8 else '∞':<4}: {b:>6,}点  回収率 {rt/st*100:>6.1f}%")
    print("\n  EV閾値以上を全部買った実回収率:")
    for t in [1.0,1.5,2.0,3.0,5.0]:
        b,st,rt = thr_acc[t]
        if st: print(f"    EV≥{t:.1f}: {b:>6,}点/{nd}日={b/nd:>5.0f}点/日  回収率 {rt/st*100:>6.1f}%")
    print("\n  ※オッズは締切前スナップ＝本命は締切に向け締まるためEVは実際より甘い(過大)側。")

if __name__ == "__main__":
    main()
