# -*- coding: utf-8 -*-
"""
「展示タイムを“それぞれのレース場に合わせる”（会場補正）と③の展示シグナルは鋭くなるか」を検証。

会場ごとに展示タイムの基準（平均・標準偏差）が違う（蒲郡6.70〜徳山6.96）。
raw の6.80は蒲郡では遅い・徳山では速い。そこで会場補正 z = (time - 会場平均)/会場σ を導入し、
「展示最速の人気薄が、その会場基準でも“本当に速い(z<しきい値)”か」で③を絞れるか見る。

会場ベースライン: k*.csv の展示タイムから、before期間より前(<20260622)で算出（リーク防止）。
評価: data/before の展示 × k-file 配当（ワイド 本命×展示最速人気薄）を会場zで層別。
"""
import csv, glob, json, statistics
from collections import defaultdict
import build_today as B
import analyze_ana_taikou_roi as A
from features_player_history import VENUE_CODE

BASE_BEFORE = "20260622"   # この日より前の k*.csv で会場ベースライン算出


def venue_baseline():
    """会場コード -> (平均, 標準偏差)。展示タイムの会場特性。"""
    vt = defaultdict(list)
    for fp in glob.glob("data/k*.csv"):
        for r in csv.DictReader(open(fp, encoding="cp932")):
            v = r.get("会場"); t = r.get("展示タイム")
            y, mo, dd = (r.get("日付") or "//").split("/")
            try:
                t = float(t); d = f"{int(y):04d}{int(mo):02d}{int(dd):02d}"
            except (TypeError, ValueError):
                continue
            if d >= BASE_BEFORE:            # ベースラインは before 期間より前のみ
                continue
            code = VENUE_CODE.get(v)
            if code and 6.0 <= t <= 7.5:
                vt[code].append(t)
    base = {}
    for code, ts in vt.items():
        if len(ts) >= 200:
            base[code] = (statistics.mean(ts), statistics.pstdev(ts) or 0.08)
    return base


def collect(base):
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
            code = rid[:2]
            if code not in base: continue
            vm, vs = base[code]
            fins = rc["fin"]
            order = sorted([w for w in range(1, 7) if fins.get(w)], key=lambda w: fins[w])
            if len(order) < 3 or fins[order[0]] != 1: continue
            ps = [mp[w] for w in range(1, 7)]
            best = min(range(6), key=lambda i: time[i])           # 展示最速(0-index)
            mrank = {w: r for r, w in enumerate(sorted(range(6), key=lambda i: -ps[i]), 1)}
            favM = max(range(6), key=lambda i: ps[i])
            if mrank[best] < 4:                                   # ③発火（人気薄）でない
                continue
            z = (time[best] - vm) / vs                            # 会場補正 z（負ほど速い）
            fav_w, ex_w = favM + 1, best + 1
            pr = frozenset((fav_w, ex_w)); top3 = set(order[:3])
            hit = fav_w in top3 and ex_w in top3
            pay = rc["wide"].get(pr, 0) if hit else 0
            rows.append({"z": z, "pay": pay, "hit": int(bool(hit)), "code": code})
    return rows


def roi(rows):
    if not rows: return (0, 0, 0)
    st = len(rows) * 100; rt = sum(r["pay"] for r in rows)
    return (rt / st * 100, sum(r["hit"] for r in rows), len(rows))


def main():
    print("会場ベースライン算出中…")
    base = venue_baseline()
    print(f"会場ベースライン: {len(base)}場（<{BASE_BEFORE} のk*.csvから）\n")
    rows = collect(base)
    r0, h0, n0 = roi(rows)
    print(f"③発火(展示最速の人気薄) 会場一致 {n0}R:")
    print(f"  会場補正なし・全部買い: ワイド回収率 {r0:.1f}%（的中{h0}）\n")

    print("会場補正z（負ほど“その会場基準で速い”）で層別 → ワイド回収率:")
    print(f"  {'条件':<26}{'R':>5}{'的中':>5}{'回収率':>9}")
    cuts = [("z<-1.5 (会場でも激速)", lambda z: z < -1.5),
            ("z<-1.0", lambda z: z < -1.0),
            ("z<-0.5", lambda z: z < -0.5),
            ("z<0 (会場平均より速い)", lambda z: z < 0),
            ("z>=0 (会場平均より遅い)", lambda z: z >= 0)]
    for lab, f in cuts:
        sub = [r for r in rows if f(r["z"])]
        if len(sub) < 15:
            print(f"  {lab:<26}{len(sub):>5}   データ不足"); continue
        rr, hh, nn = roi(sub)
        print(f"  {lab:<26}{nn:>5}{hh:>5}{rr:>8.1f}%")
    print("\n※会場補正で『速い側z<0』が『遅い側z>=0』より明確に回収率上なら、会場補正は有効。")
    print("  raw最速というだけの③に“会場でも本当に速い”条件を足す価値がある。")


if __name__ == "__main__":
    main()
