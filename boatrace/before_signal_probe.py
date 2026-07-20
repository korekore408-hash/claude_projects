# -*- coding: utf-8 -*-
"""
直前情報（展示タイム/展示ST/進入コース/チルト/部品/天候風波）を全部取り込み、
「これはモデルが持っていない“本物の新signal”か」を場外(honest OOS)で検証する。

データ: data/before/*.json（展示ありは 2026-06-22〜30 の 1,384R・全てモデル場外）。
  ex.time=展示タイム / ex.st=展示ST / ex.course=進入 / ex.tilt / ex.parts / ex.weather
  result.order=着順・po2/po3=配当（自己完結で精算）。
※締切直前オッズの時系列は未収録のため対象外（単発スナップのみ）。

問い1（信号の有無）: 展示は「モデル予測を超えて」勝者を当てるか。
  例) モデル本命でも展示タイムが遅ければ実際の勝率は予測を下回るか。
  展示最速がモデル人気薄なら、その艇は予測を超えて来るか。
問い2（実益）: 展示に基づく買い/見送りで2連複の回収率が上がるか（bootstrap CI付き）。
"""
import json, glob, math, random
from collections import defaultdict
import build_today as B
import analyze_ana_taikou_roi as A

random.seed(42)


def implied_top2(sv, b):
    """PLでの『艇bが2着以内』の理論確率 = P(b 1着)+P(b 2着)。"""
    tot = sum(sv)
    if tot <= 0: return 0.0
    p1 = sv[b] / tot
    p2 = 0.0
    for a in range(6):
        if a == b: continue
        rem = tot - sv[a]
        if rem > 0: p2 += (sv[a] / tot) * (sv[b] / rem)
    return p1 + p2


def build():
    model = A.load_predict()
    rel = B.load("features_race_relative.csv")
    api_map = B.build_api_scores(rel)
    kd = A.load_all_ktxt()
    dates = sorted({f"{rid[2:6]}-{rid[6:8]}-{rid[8:10]}" for rid in kd})
    kpay = B.load_payouts(dates)                 # (2連単, 3連単, 2連複)
    recs = []
    for fp in sorted(glob.glob("data/before/*.json")):
        d = json.load(open(fp))
        for rid, v in d.items():
            ex = v.get("ex"); res = v.get("result")
            if not ex or not res: continue
            if not res.get("order"): continue
            if rid not in kpay or not kpay[rid][2]: continue   # 2連複配当(k-file)必須
            po2f = kpay[rid][2]
            mp = model.get(rid)
            if not mp or len(mp) != 6: continue
            api = [api_map.get((rid, w)) for w in range(1, 7)]
            if any(a is None for a in api): continue
            time = ex.get("time"); st = ex.get("st"); course = ex.get("course")
            if not time or len(time) != 6 or any(t is None for t in time): continue
            sv = [mp[w] for w in range(1, 7)]
            tot = sum(sv)
            if tot <= 0: continue
            p = [x / tot for x in sv]
            fav = max(range(6), key=lambda i: p[i])          # モデル本命(0-index)
            mrank = {w: r for r, w in enumerate(sorted(range(6), key=lambda i: -p[i]), 1)}
            # 展示タイム順位（小さい=速い=1位）
            trank = {w: r for r, w in enumerate(sorted(range(6), key=lambda i: time[i]), 1)}
            exbest = min(range(6), key=lambda i: time[i])     # 展示最速艇
            # 展示ST順位（小さい=速い=1位）。None混在に注意
            stv = [s if isinstance(s, (int, float)) else 1.0 for s in (st or [1.0]*6)]
            srank = {w: r for r, w in enumerate(sorted(range(6), key=lambda i: stv[i]), 1)}
            wth = ex.get("weather") or {}
            order = res["order"]; win = order[0] - 1           # 勝ち艇(0-index)
            act2 = tuple(sorted(order[:2]))
            po2 = po2f                                          # 2連複配当(k-file)
            recs.append({
                "rid": rid, "date": rid[2:10], "sv": sv, "p": p, "fav": fav,
                "win": win, "act2": act2, "po2": po2, "hon": max(api), "exbest": exbest,
                "win_top2": {order[0] - 1, order[1] - 1},
                "f": {
                    "extime_rank_fav": trank[fav],            # 本命の展示タイム順位
                    "exbest_model_rank": mrank[exbest],       # 展示最速艇のモデル順位
                    "exbest_is_longshot": 1.0 if mrank[exbest] >= 4 else 0.0,
                    "st_rank_fav": srank[fav],                # 本命の展示ST順位
                    "course_change": sum(1 for i in range(6) if course and course[i] != i + 1),
                    "wind": float(wth.get("wind") or 0),
                    "wave": float(wth.get("wave") or 0),
                    "parts_any": 1.0 if any(ex.get("parts") or []) else 0.0,
                },
                "p1": p[fav],
            })
    return recs


def wr(rows, sel):
    """selに一致する行の 本命的中率(実測) と モデル予測p1平均。"""
    s = [r for r in rows if sel(r)]
    if not s: return (0, 0, 0)
    aw = sum(1 for r in s if r["win"] == r["fav"]) / len(s)
    pp = sum(r["p1"] for r in s) / len(s)
    return (len(s), aw, pp)


def roi2(rows):
    """各行に対しアプリ2連複(新k_ex・フラット¥100/点)を買った回収率。F返還は省略(展示成立時のみ)。"""
    st = rt = hit = 0
    for r in rows:
        buy = B._pf_topk(r["sv"], B.k_ex(r["hon"]))
        for c in buy:
            st += 100
            if c == r["act2"]: rt += r["po2"]; hit += 1
    return (rt / st * 100 if st else 0), st, rt, hit, len(rows)


def boot_roi(rows, n=1000):
    """レース単位リサンプルで回収率の95%CI。"""
    if not rows: return (0, 0)
    base = []
    for r in rows:
        buy = B._pf_topk(r["sv"], B.k_ex(r["hon"]))
        s = len(buy) * 100
        rr = sum(r["po2"] for c in buy if c == r["act2"])
        base.append((s, rr))
    out = []
    N = len(base)
    for _ in range(n):
        ss = rr = 0
        for _ in range(N):
            a, b = base[random.randrange(N)]; ss += a; rr += b
        out.append(rr / ss * 100 if ss else 0)
    out.sort()
    return (out[int(n * .025)], out[int(n * .975)])


def main():
    print("データ読込中…")
    recs = build()
    print(f"展示あり・精算可能: {len(recs):,}レース（{recs[0]['date']}〜{recs[-1]['date']}・全てモデル場外）\n")

    print("=" * 70)
    print("問い1: 展示はモデル予測を超えて勝者を当てるか")
    print("=" * 70)
    print("\n① モデル本命を『本命の展示タイム順位』で分ける（予測p1はほぼ同じはず）:")
    print(f"  {'展示タイム順位':<16}{'レース数':>8}{'実測 本命勝率':>14}{'モデル予測p1':>14}{'差(実測-予測)':>14}")
    for label, lo, hi in [("1位(最速)", 1, 1), ("2-3位", 2, 3), ("4-6位(遅い)", 4, 6)]:
        n, aw, pp = wr(recs, lambda r: lo <= r["f"]["extime_rank_fav"] <= hi)
        print(f"  {label:<16}{n:>8}{aw*100:>13.1f}%{pp*100:>13.1f}%{(aw-pp)*100:>+13.1f}pt")
    print("  → 速い本命が予測超え(+)／遅い本命が予測割れ(−)なら、展示はモデル未搭載の信号。")

    print("\n② 展示ST順位で本命を分ける:")
    print(f"  {'本命の展示ST':<16}{'レース数':>8}{'実測 本命勝率':>14}{'モデル予測p1':>14}{'差':>10}")
    for label, lo, hi in [("1-2位(好ST)", 1, 2), ("3-4位", 3, 4), ("5-6位(出遅れ)", 5, 6)]:
        n, aw, pp = wr(recs, lambda r: lo <= r["f"]["st_rank_fav"] <= hi)
        print(f"  {label:<16}{n:>8}{aw*100:>13.1f}%{pp*100:>13.1f}%{(aw-pp)*100:>+9.1f}pt")

    print("\n③ 展示最速がモデル人気薄(4-6番手)のとき、その最速艇は実際に2着以内に来るか:")
    ls = [r for r in recs if r["f"]["exbest_is_longshot"]]
    if ls:
        top2 = sum(1 for r in ls if r["exbest"] in r["win_top2"]) / len(ls)
        pred = sum(implied_top2(r["sv"], r["exbest"]) for r in ls) / len(ls)  # PL理論2連対率
        print(f"   該当 {len(ls)}R: 展示最速(人気薄)艇の実測2連対率 {top2*100:.1f}% / "
              f"モデルPL理論2連対率 {pred*100:.1f}%  差 {(top2-pred)*100:+.1f}pt")
        print("   → 実測がPL理論を大きく上回れば、展示は“人気薄の激走”を先読みしている。")

    print("\n" + "=" * 70)
    print("問い2: 展示に基づく『買う/見送る』で2連複の回収率は上がるか（bootstrap 95%CI）")
    print("=" * 70)
    base_roi = roi2(recs); lo, hi = boot_roi(recs)
    print(f"\n  ベース（全{len(recs)}R買い）: 回収率 {base_roi[0]:.1f}%  [95%CI {lo:.1f}〜{hi:.1f}]  収支¥{base_roi[2]-base_roi[1]:,}")
    print(f"\n  {'ゲート条件':<34}{'残レース':>8}{'回収率':>9}{'95%CI':>18}")
    gates = [
        ("本命の展示タイムが1-3位のみ買う", lambda r: r["f"]["extime_rank_fav"] <= 3),
        ("本命の展示タイムが遅い(4-6位)は見送り→上と同じ", lambda r: r["f"]["extime_rank_fav"] <= 3),
        ("本命の展示STが1-3位のみ買う", lambda r: r["f"]["st_rank_fav"] <= 3),
        ("展示最速がモデル本命と一致のみ買う", lambda r: r["f"]["exbest_model_rank"] == 1),
        ("進入変化なし(前づけ無し)のみ買う", lambda r: r["f"]["course_change"] == 0),
        ("風が弱い(≤3m)のみ買う", lambda r: r["f"]["wind"] <= 3),
        ("波が穏やか(≤2cm)のみ買う", lambda r: r["f"]["wave"] <= 2),
    ]
    for label, sel in gates:
        sub = [r for r in recs if sel(r)]
        if len(sub) < 30:
            print(f"  {label:<34}{len(sub):>8}  データ不足"); continue
        rr = roi2(sub); clo, chi = boot_roi(sub)
        mark = "★" if clo > base_roi[0] else ""
        print(f"  {label:<34}{len(sub):>8}{rr[0]:>8.1f}%   [{clo:>5.1f}〜{chi:>5.1f}]{mark}")
    print("\n  ★=CI下限がベース回収率を上回る（=統計的に効いていそう）。無ければ誤差の範囲。")

    # 最有望③の直接収益化: 展示最速の人気薄を『本命との2連複1点』で買う
    print("\n" + "-" * 70)
    print("問い2b: ③の信号を直接買う ― 展示最速がモデル人気薄の時、その艇×本命の2連複1点")
    print("-" * 70)
    base = []
    for r in recs:
        if not r["f"]["exbest_is_longshot"]: continue
        pair = tuple(sorted((r["fav"] + 1, r["exbest"] + 1)))
        s = 100
        rr = r["po2"] if pair == r["act2"] else 0
        base.append((s, rr))
    if base:
        st = sum(a for a, _ in base); rt = sum(b for _, b in base)
        hit = sum(1 for _, b in base if b > 0)
        # bootstrap
        N = len(base); out = []
        for _ in range(2000):
            ss = rr = 0
            for _ in range(N):
                a, b = base[random.randrange(N)]; ss += a; rr += b
            out.append(rr / ss * 100 if ss else 0)
        out.sort()
        print(f"  対象 {N}R / 的中 {hit} / 回収率 {rt/st*100:.1f}%  "
              f"[95%CI {out[50]:.1f}〜{out[1949]:.1f}]  収支¥{rt-st:,}")
        print("  → 展示が示す『人気薄の激走』を市場より安く買えているか（CI下限>100なら妙味）。")


if __name__ == "__main__":
    main()
