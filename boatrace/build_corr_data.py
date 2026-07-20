# -*- coding: utf-8 -*-
"""本命確率(モデルtop1 p_win) × 3連単配当 の相関データを JSON で書き出す。
散布図の点・ビン別中央値/四分位・相関係数(Spearman/Pearson on log)をまとめる。"""
import csv, glob, json, math
from collections import defaultdict
from features_player_history import VENUE_CODE

ENC = "cp932"

def load_predict(path="predict_win.csv"):
    races = defaultdict(dict)
    with open(path, encoding=ENC, newline="") as f:
        r = csv.reader(f); h = next(r)
        i_lane, i_p, i_fin = 1, h.index("p_win"), h.index("finish_rank")
        for row in r:
            if not row: continue
            try: lane = int(row[i_lane]); p = float(row[i_p])
            except ValueError: continue
            races[row[0]][lane] = p
    return races

def load_tri_payouts():
    payout = {}
    for kp in glob.glob("data/k*.csv"):
        with open(kp, encoding=ENC) as f:
            for r in csv.DictReader(f):
                code = VENUE_CODE.get(r["会場"], "00")
                y, mo, dd = r["日付"].split("/")
                rid = f"{code}{int(y):04d}{int(mo):02d}{int(dd):02d}{int(r['レース']):02d}"
                if rid in payout: continue
                try: payout[rid] = int(r["3連単_配当"])
                except (ValueError, KeyError): pass
    return payout

def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        rk = [0.0]*len(v); i = 0
        while i < len(v):
            j = i
            while j+1 < len(v) and v[order[j+1]] == v[order[i]]: j += 1
            avg = (i+j)/2.0 + 1
            for k in range(i, j+1): rk[order[k]] = avg
            i = j+1
        return rk
    rx, ry = ranks(xs), ranks(ys)
    return pearson(rx, ry)

def pearson(xs, ys):
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    sx = sum((x-mx)**2 for x in xs); sy = sum((y-my)**2 for y in ys)
    sxy = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    return sxy/math.sqrt(sx*sy) if sx>0 and sy>0 else 0.0

def main():
    races = load_predict(); payout = load_tri_payouts()
    pts = []   # (prob, payout)
    for rid, boats in races.items():
        if len(boats) != 6: continue
        s = [boats.get(w, 0.0) for w in range(1, 7)]
        if any(v <= 0 for v in s): continue
        if rid not in payout: continue
        pay = payout[rid]
        if pay <= 0: continue
        pts.append((max(s), pay))

    probs = [p for p, _ in pts]
    pays = [q for _, q in pts]
    logpays = [math.log10(q) for q in pays]
    sp = spearman(probs, pays)
    pe = pearson(probs, logpays)

    # ビン別（本命確率 0.02 刻み）中央値/四分位/平均
    bins = defaultdict(list)
    for p, q in pts:
        b = round(math.floor(p/0.02)*0.02 + 0.01, 3)
        bins[b].append(q)
    binstats = []
    for b in sorted(bins):
        v = sorted(bins[b])
        if len(v) < 20: continue
        def qtl(f): return v[min(len(v)-1, int(f*len(v)))]
        binstats.append({
            "x": b, "n": len(v),
            "p25": qtl(0.25), "med": qtl(0.50), "p75": qtl(0.75),
            "mean": round(sum(v)/len(v), 1),
        })

    # 散布図の点（全件）。prob=per-mille(int), payout=int で軽量化
    scatter = [[int(round(p*1000)), q] for p, q in pts]

    out = {
        "n": len(pts),
        "spearman": round(sp, 3),
        "pearson_log": round(pe, 3),
        "prob_min": round(min(probs), 3), "prob_max": round(max(probs), 3),
        "pay_min": min(pays), "pay_max": max(pays),
        "pay_median": sorted(pays)[len(pays)//2],
        "bins": binstats,
        "scatter": scatter,
    }
    with open("corr_data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"n={len(pts)}  Spearman(prob,payout)={sp:.3f}  Pearson(prob,log10 payout)={pe:.3f}")
    print(f"payout: min={min(pays)} median={out['pay_median']} max={max(pays)}")
    print(f"bins={len(binstats)}  size={len(json.dumps(out))/1024:.0f}KB")

if __name__ == "__main__":
    main()
