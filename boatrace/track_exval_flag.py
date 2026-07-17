# -*- coding: utf-8 -*-
"""
③展示妙味フラグの実戦トラッキング。

アプリ(exView)の③フラグと同一条件で発火判定し、推奨どおり
「本命(favM) × 展示最速の人気薄(best) のワイド 1点 ¥100」を買った想定で精算する。
before データ(data/before/*.json)を貯めて再実行するたび累積回収率が更新される。

フラグ条件（アプリと一致）:
  best   = 展示タイム最速の枠（min ex.time）
  mrank  = 朝AI p_win(predict_win.csv) の順位
  favM   = p_win 最上位（本命）
  発火   = mrank[best] >= 4（=人気薄）  ※best!=favM は自動的に含意
精算: k-file のワイド配当（A.load_all_ktxt: rc["wide"]={frozenset(pair):配当}）。
出力: 累積回収率+95%CI・日別推移・荒れ度帯別・参考(2連複)。log CSV も書き出す。
"""
import json, glob, csv, random
from collections import defaultdict, OrderedDict
import build_today as B
import analyze_ana_taikou_roi as A

random.seed(11)
LOG = "exval_flag_log.csv"


def band(hon):
    return "鉄板" if hon >= 0.65 else "波乱" if hon < 0.45 else "標準"


def collect():
    model = A.load_predict()
    rel = B.load("features_race_relative.csv")
    api_map = B.build_api_scores(rel)
    kd = A.load_all_ktxt()
    rows = []
    for fp in sorted(glob.glob("data/before/*.json")):
        for rid, v in json.load(open(fp)).items():
            ex = v.get("ex")
            if not ex or not ex.get("time"): continue
            time = ex["time"]
            if len(time) != 6 or any(t is None for t in time): continue
            mp = model.get(rid); rc = kd.get(rid)
            if not mp or len(mp) != 6 or not rc: continue
            api = [api_map.get((rid, w)) for w in range(1, 7)]
            if any(a is None for a in api): continue
            fins = rc["fin"]
            order = sorted([w for w in range(1, 7) if fins.get(w)], key=lambda w: fins[w])
            if len(order) < 3 or fins[order[0]] != 1: continue
            # --- アプリ③フラグと同一の発火判定 ---
            ps = [mp[w] for w in range(1, 7)]                 # 朝AI p_win（枠1..6）
            best = min(range(6), key=lambda i: time[i])       # 展示最速(0-index)
            mrank = {w: r for r, w in enumerate(sorted(range(6), key=lambda i: -ps[i]), 1)}
            favM = max(range(6), key=lambda i: ps[i])
            if mrank[best] < 4:                                # 人気薄でない＝非発火
                continue
            # --- ワイド favM×best を精算 ---
            fav_w, ex_w = favM + 1, best + 1
            pr = frozenset((fav_w, ex_w))
            top3 = set(order[:3])
            hit = (fav_w in top3 and ex_w in top3)
            pay = rc["wide"].get(pr, 0) if hit else 0
            # 参考: 同じ組合せの2連複
            act2 = frozenset(order[:2])
            nf = rc.get("nf")
            hit2 = (nf and pr == act2)
            pay2 = nf[1] if hit2 else 0
            rows.append({
                "date": rid[2:10], "rid": rid, "band": band(max(api)),
                "fav": fav_w, "exbest": ex_w, "exbest_rank": mrank[best],
                "wide_hit": int(bool(hit)), "wide_pay": pay,
                "nf_hit": int(bool(hit2)), "nf_pay": pay2,
            })
    rows.sort(key=lambda r: (r["date"], r["rid"]))
    return rows


def roi_ci(pairs, n=3000):
    if not pairs: return (0, 0, 0, 0, 0, 0)
    st = sum(a for a, _ in pairs); rt = sum(b for _, b in pairs)
    hit = sum(1 for _, b in pairs if b > 0)
    N = len(pairs); out = []
    for _ in range(n):
        ss = rr = 0
        for _ in range(N):
            a, b = pairs[random.randrange(N)]; ss += a; rr += b
        out.append(rr / ss * 100 if ss else 0)
    out.sort()
    return (rt / st * 100, hit, out[int(n * .025)], out[int(n * .975)], st, rt)


def main():
    print("データ読込中…")
    rows = collect()
    if not rows:
        print("フラグ発火レースがまだありません（before データを貯めてください）。"); return

    # ログCSV書き出し（再実行で最新化・派生物）
    with open(LOG, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    d0, d1 = rows[0]["date"], rows[-1]["date"]
    print(f"③フラグ発火: {len(rows)}レース（{d0}〜{d1}） → ログ {LOG} に保存\n")

    wide = [(100, r["wide_pay"]) for r in rows]
    roi, hit, lo, hi, st, rt = roi_ci(wide)
    print("=" * 60)
    print("【本線】本命×展示最速人気薄 のワイド（各¥100）")
    print("=" * 60)
    print(f"  発火 {len(rows)}R / 的中 {hit}（{hit/len(rows)*100:.1f}%） / "
          f"回収率 {roi:.1f}%  [95%CI {lo:.1f}〜{hi:.1f}]")
    print(f"  投資 ¥{st:,} / 回収 ¥{rt:,.0f} / 収支 ¥{rt-st:,.0f}")
    verdict = ("★妙味濃厚(CI下限>100)" if lo > 100 else
               "△点推定>100だが未確定(CI跨ぎ)" if roi > 100 else
               "×現状は優位なし")
    print(f"  判定: {verdict}")

    # 参考: 2連複
    nf = [(100, r["nf_pay"]) for r in rows]
    r2, h2, l2, hh2, s2, t2 = roi_ci(nf)
    print(f"\n  参考(2連複 同組): 的中{h2} 回収率{r2:.1f}% [CI {l2:.1f}〜{hh2:.1f}]")

    # 荒れ度帯別
    print("\n【荒れ度帯別（ワイド）】")
    by = defaultdict(list)
    for r in rows: by[r["band"]].append((100, r["wide_pay"]))
    for b in ("波乱", "標準", "鉄板"):
        if b not in by: continue
        p = by[b]; ro, h, l, hgh, s, t = roi_ci(p)
        print(f"  {b:<4} {len(p):>4}R 的中{h:>3} 回収率{ro:>6.1f}% [CI {l:>5.1f}〜{hgh:>6.1f}]")

    # 日別推移（累積回収率が育つのを見る）
    print("\n【日別推移（ワイド・累積）】")
    day = OrderedDict()
    for r in rows: day.setdefault(r["date"], []).append(r)
    cum_s = cum_r = 0
    print(f"  {'日付':<10}{'R':>4}{'的中':>5}{'当日ROI':>9}{'累積ROI':>9}")
    for d, rs in day.items():
        ds = len(rs) * 100; dr = sum(r["wide_pay"] for r in rs); dh = sum(r["wide_hit"] for r in rs)
        cum_s += ds; cum_r += dr
        print(f"  {d[:4]}/{d[4:6]}/{d[6:]:<2}{len(rs):>4}{dh:>5}{dr/ds*100:>8.0f}%{cum_r/cum_s*100:>8.0f}%")

    print("\n※判定★が出るまで実戦は少額。CIが100%を割らなくなれば妙味の実在が濃厚。")
    print("  before を貯めて本スクリプトを再実行するほどCIが締まり、×/△/★が確定に近づく。")


if __name__ == "__main__":
    main()
