# -*- coding: utf-8 -*-
"""
③の妙味の精査: 「展示最速がモデル人気薄」の艇を、券種別・帯別・人気薄度別に買って
回収率を bootstrap CI 付きで見る。低分散の複勝/ワイドでも妙味が残るかが焦点。

賭け対象は展示最速艇(exbest)。相手が要るワイド/2連複はモデル本命(fav)と組む。
  単勝  exbest         : 勝てば tan
  複勝  exbest         : 2着内で fuku
  ワイド exbest×fav     : 両者3着内で wide
  2連複 exbest×fav      : 決着の2連複が一致で nf(=k-fileの2連複配当)
データ: 展示=data/before/*.json、配当/着順=k-file(A.load_all_ktxt)、rid結合。
モデル場外(2026-06-23〜30・展示あり)のみ。標本が小さいのでCIで慎重に読む。
"""
import json, glob, random
from collections import defaultdict
import build_today as B
import analyze_ana_taikou_roi as A

random.seed(7)


def load():
    model = A.load_predict()
    rel = B.load("features_race_relative.csv")
    api_map = B.build_api_scores(rel)
    kd = A.load_all_ktxt()
    recs = []
    for fp in sorted(glob.glob("data/before/*.json")):
        for rid, v in json.load(open(fp)).items():
            ex = v.get("ex")
            if not ex: continue
            time = ex.get("time")
            if not time or len(time) != 6 or any(t is None for t in time): continue
            mp = model.get(rid); rc = kd.get(rid)
            if not mp or len(mp) != 6 or not rc: continue
            api = [api_map.get((rid, w)) for w in range(1, 7)]
            if any(a is None for a in api): continue
            fins = rc["fin"]
            order = sorted([w for w in range(1, 7) if fins.get(w)], key=lambda w: fins[w])
            if len(order) < 3 or fins[order[0]] != 1: continue
            sv = [mp[w] for w in range(1, 7)]; tot = sum(sv)
            if tot <= 0: continue
            p = [x / tot for x in sv]
            mrank = {w: r for r, w in enumerate(sorted(range(6), key=lambda i: -p[i]), 1)}
            fav = max(range(6), key=lambda i: p[i]) + 1          # 本命(1-index)
            exbest = min(range(6), key=lambda i: time[i]) + 1     # 展示最速(1-index)
            recs.append({
                "rid": rid, "hon": max(api), "fav": fav, "exbest": exbest,
                "exbest_rank": mrank[exbest - 1], "order": order, "rc": rc,
            })
    return recs


def band(hon):
    return "鉄板" if hon >= 0.65 else "波乱" if hon < 0.45 else "標準"


def settle(r, bet):
    """(stake, ret) を返す。stake=100固定。非該当は None。"""
    rc = r["rc"]; order = r["order"]
    top2 = set(order[:2]); top3 = set(order[:3]); win = order[0]
    ex = r["exbest"]; fav = r["fav"]
    if bet == "単勝":
        if not rc["tan"]: return None
        return (100, rc["tan"][1] if win == ex else 0)
    if bet == "複勝":
        return (100, rc["fuku"].get(ex, 0) if ex in top2 else 0)
    if bet == "ワイド":
        if ex == fav: return None
        pr = frozenset((ex, fav))
        return (100, rc["wide"].get(pr, 0) if (ex in top3 and fav in top3) else 0)
    if bet == "2連複":
        if ex == fav: return None
        pr = frozenset((ex, fav)); act = frozenset(order[:2])
        return (100, rc["nf"][1] if (pr == act and rc.get("nf")) else 0)
    return None


def roi_ci(pairs, n=2000):
    if not pairs: return (0, 0, 0, 0)
    st = sum(a for a, _ in pairs); rt = sum(b for _, b in pairs)
    hit = sum(1 for _, b in pairs if b > 0)
    N = len(pairs); out = []
    for _ in range(n):
        ss = rr = 0
        for _ in range(N):
            a, b = pairs[random.randrange(N)]; ss += a; rr += b
        out.append(rr / ss * 100 if ss else 0)
    out.sort()
    return (rt / st * 100, hit, out[int(n * .025)], out[int(n * .975)])


def show(title, sel, recs):
    print(f"\n【{title}】")
    print(f"  {'券種':<8}{'対象R':>7}{'的中':>6}{'回収率':>9}{'95%CI':>18}")
    for bet in ("単勝", "複勝", "ワイド", "2連複"):
        pairs = []
        for r in recs:
            if not sel(r): continue
            s = settle(r, bet)
            if s: pairs.append(s)
        if len(pairs) < 25:
            print(f"  {bet:<8}{len(pairs):>7}   データ不足"); continue
        roi, hit, lo, hi = roi_ci(pairs)
        mark = "★" if lo > 100 else ("△" if roi > 100 else "")
        print(f"  {bet:<8}{len(pairs):>7}{hit:>6}{roi:>8.1f}%   [{lo:>5.1f}〜{hi:>6.1f}]{mark}")


def main():
    print("データ読込中…")
    recs = load()
    ls = [r for r in recs if r["exbest_rank"] >= 4]     # 展示最速がモデル人気薄
    print(f"展示あり {len(recs)}R / うち展示最速が人気薄(4-6番手) {len(ls)}R "
          f"（{recs[0]['rid'][2:10]}〜{recs[-1]['rid'][2:10]}）")
    print("\n★=CI下限>100%（妙味濃厚） / △=点推定>100%だがCIは100跨ぎ（未確定）")

    show("全体：展示最速の人気薄を買う", lambda r: r["exbest_rank"] >= 4, recs)
    show("荒れ度=波乱帯(hon<0.45)のみ", lambda r: r["exbest_rank"] >= 4 and band(r["hon"]) == "波乱", recs)
    show("荒れ度=標準帯(0.45-0.65)のみ", lambda r: r["exbest_rank"] >= 4 and band(r["hon"]) == "標準", recs)
    show("人気薄度=最下位級(5-6番手)のみ", lambda r: r["exbest_rank"] >= 5, recs)
    show("対照：展示最速がモデル本命(1番手)＝順当", lambda r: r["exbest_rank"] == 1, recs)

    print("\n※標本は8日と小さい。★が複数券種で揃えば妙味の実在が濃厚、△止まりなら要データ積増し。")


if __name__ == "__main__":
    main()
