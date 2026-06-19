# -*- coding: utf-8 -*-
"""
当日予想 メイン画面（携帯向け・単体HTML・一覧⇔詳細アプリ）
=========================================================================
当日・前日・前々日の3日分を切替表示（既定=当日）。直前情報なしモデルの
1着確率から 2連単 上位5 / 3連単 上位10 を Plackett-Luce で算出。
前日・前々日は結果（着順・的中判定）まで表示する。

入力: predict_win.csv（p_win, finish_rank）/ features_race_relative.csv
出力: today.html（全レース詳細でも軽量・自己完結）

使い方:
  py -3 build_today.py                  # データ最新日から3日分
  py -3 build_today.py --date 2026-06-16 --days 3
"""

import argparse
import csv
import glob
import json
import re

from features_player_history import VENUE_CODE


def load(path):
    with open(path, encoding="cp932") as f:
        return list(csv.DictReader(f))


def to_float(s):
    try:
        return float(str(s).strip())
    except (ValueError, AttributeError):
        return None


# 予想確率（本命確率 hon=top1 p_win）に応じた買目点数。堅い→少点 / 荒れ→多点。
# 検証(2026年): 鉄板ほど少点で回収率が高く、大混戦は点数を広げる方が良い。上限は 2連単5 / 3連単20。
def k_ex(hon):     # 2連単（上限5）
    return 2 if hon >= 0.65 else 3 if hon >= 0.50 else 4 if hon >= 0.40 else 5


def k_tri(hon):    # 3連単（上限20）
    return (4 if hon >= 0.65 else 7 if hon >= 0.50 else 10 if hon >= 0.40
            else 14 if hon >= 0.30 else 20)


def bet_exclude(cls_by_w, lane_win1, hon):
    """買い目から除外する枠 [[枠,理由], ...]（検証済ガイドライン）。
    B2(class_ord==1)は常時除外 / 荒れ帯(本命確率<0.45)で1号艇が成績不振
    (lane_win_rate<0.40)なら1号艇も除外 / 除外後3艇未満になるなら除外しない。"""
    xb, excl = [], set()
    for w in range(1, 7):
        cl = cls_by_w.get(w)
        if cl is not None and int(cl) == 1:
            xb.append([w, "B2"]); excl.add(w)
    if hon < 0.45 and 1 not in excl and lane_win1 is not None and lane_win1 < 0.40:
        xb.append([1, "1号艇不振"]); excl.add(1)
    return [] if 6 - len(excl) < 3 else xb


def make_comment(boats, field_std):
    """viewer.html と同じロジックの予想コメント [line1, line2] を作る。
    boats: [{枠,名,pwin(0-1),win_rank,motor_rank,st_rank,lane_win,lane_n,vown}]"""
    bs = sorted(boats, key=lambda b: -(b["pwin"] or 0))
    t1, t2 = bs[0], bs[1]

    def reasons(b):
        r = []
        if b["枠"] <= 2:
            r.append("イン最有利" if b["枠"] == 1 else "好枠")
        if b["win_rank"] == 1:
            r.append("全国勝率トップ")
        if b["motor_rank"] == 1:
            r.append("機力レース内1位")
        if b["st_rank"] == 1:
            r.append("平均ST最速")
        if b["lane_win"] is not None and b["lane_win"] >= 0.5 and (b["lane_n"] or 0) >= 3:
            r.append("枠成績良好")
        if b["vown"] is not None and b["枠"] == 1 and b["vown"] >= 0.55:
            r.append("当場イン強い")
        if not r:
            r.append("実力上位" if (b["win_rank"] and b["win_rank"] <= 2) else "総合力で上位")
        return r[:2]

    strong = (t1["pwin"] or 0) >= 0.5
    tight = field_std is not None and field_std < 0.7
    line1 = f"◎{t1['枠']}号艇 {t1['名']}：{'・'.join(reasons(t1))}で1着確率{round((t1['pwin'] or 0)*100)}%。"
    line2 = (f"相手本線は{t2['枠']}号艇（{'・'.join(reasons(t2))}）。"
             + ("本命濃厚の構成。" if strong else ("実力拮抗で波乱含み。" if tight else "中穴も一考。")))
    return [line1, line2]


def _pl_prob(s, combo):
    p, rem = 1.0, sum(s)
    for w in combo:
        p *= s[w - 1] / rem
        rem -= s[w - 1]
    return p


def _pl_rank(s, kind, actual, excl=None):
    """actual（枠tuple）の PL 確率順位（1=最尤）。excl=除外枠の集合を渡すと、
    除外枠を含む買い目は数えず（actual が除外枠を含めば的中不能=∞扱い）。"""
    import itertools
    excl = excl or set()
    if any(w in excl for w in actual):
        return 10 ** 9
    idx = [i for i in range(6) if s[i] > 0 and (i + 1) not in excl]
    pa = _pl_prob(s, actual)
    g = 0
    for c in itertools.permutations(idx, kind):
        if _pl_prob(s, [i + 1 for i in c]) > pa:
            g += 1
    return g + 1


def venue_stats(rel, pred, hist, since):
    """since 以降の結果がある全レースから、場別の的中率を集計。
    各場: 本命1着 / 2連単(本命=top1,top3,変動K) / 3連単(本命,top3,変動K)。
    変動K列は当日の買い目と同じ除外（B2/不振1号艇 bet_exclude）を適用。"""
    from collections import defaultdict
    races = {}
    for r in rel:
        if r["日付"] < since:
            continue
        rid = r["race_id"]
        pr = pred.get((rid, r["枠番"]), {})
        h = hist.get((rid, r["枠番"]), {})
        try:
            pm = float(pr.get("p_win"))
        except (TypeError, ValueError):
            pm = None
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            fin = None
        rc = races.setdefault(rid, {"c": r["場コード"], "v": r["会場"],
                                    "d": r["日付"], "b": {}, "cl": {}, "lw": {}})
        w = int(r["枠番"])
        rc["b"][w] = (pm, fin)
        rc["cl"][w] = to_float(r.get("class_ord"))
        rc["lw"][w] = to_float(h.get("lane_win_rate"))
    agg = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0, 0])   # n,win,e1,e3,eK(2連単変動),t1,t3,tK(3連単変動)
    name = {}
    dmin = dmax = None
    for rc in races.values():
        if len(rc["b"]) != 6:
            continue
        s = [rc["b"][w][0] for w in range(1, 7)]
        fins = [rc["b"][w][1] for w in range(1, 7)]
        if any(x is None for x in s):
            continue
        # 完走艇（着順あり）だけで 1-2-3着 を決める。F/失格混在でも 3着まで分かれば集計。
        order = sorted([w for w in range(1, 7)
                        if fins[w - 1] is not None and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < 3 or fins[order[0] - 1] != 1:
            continue
        hm = max(range(6), key=lambda i: s[i]) + 1
        hon = max(s)
        kx, kt = k_ex(hon), k_tri(hon)
        excl = {e[0] for e in bet_exclude(rc["cl"], rc["lw"].get(1), hon)}
        er = _pl_rank(s, 2, tuple(order[:2]))                  # 本命/top3=モデル診断(除外なし)
        tr = _pl_rank(s, 3, tuple(order[:3]))
        erk = _pl_rank(s, 2, tuple(order[:2]), excl)           # 変動K=実買い目(除外あり)
        trk = _pl_rank(s, 3, tuple(order[:3]), excl)
        a = agg[rc["c"]]
        name[rc["c"]] = rc["v"]
        dmin = rc["d"] if dmin is None or rc["d"] < dmin else dmin
        dmax = rc["d"] if dmax is None or rc["d"] > dmax else dmax
        a[0] += 1
        a[1] += (hm == order[0])
        a[2] += er <= 1; a[3] += er <= 3; a[4] += erk <= kx    # 2連単 変動K(除外後)
        a[5] += tr <= 1; a[6] += tr <= 3; a[7] += trk <= kt    # 3連単 変動K(除外後)
    pct = lambda x, n: round(x / n * 100) if n else 0
    rows = []
    for c, a in agg.items():
        n = a[0]
        rows.append([name[c], n, pct(a[1], n), pct(a[2], n), pct(a[3], n),
                     pct(a[4], n), pct(a[5], n), pct(a[6], n), pct(a[7], n)])
    rows.sort(key=lambda r: r[2], reverse=True)
    T = [sum(agg[c][i] for c in agg) for i in range(8)]
    n = T[0]
    allrow = ["全場", n, pct(T[1], n), pct(T[2], n), pct(T[3], n), pct(T[4], n),
              pct(T[5], n), pct(T[6], n), pct(T[7], n)]
    return {"from": dmin, "to": dmax, "n": n, "rows": rows, "all": allrow}


def load_payouts(keep):
    """keep の日付の K-file から race_id -> (2連単配当, 3連単配当)。"""
    yy = {d[2:4] + d[5:7] + d[8:10] for d in keep}   # '2026-06-16' -> '260616'
    payout = {}
    for kp in glob.glob("data/k*.csv"):
        m = re.search(r"k(\d{6})", kp)
        if not m or m.group(1) not in yy:
            continue
        for r in load(kp):
            code = VENUE_CODE.get(r["会場"], "00")
            y, mo, dd = r["日付"].split("/")
            rid = f"{code}{int(y):04d}{int(mo):02d}{int(dd):02d}{int(r['レース']):02d}"
            if rid in payout:
                continue
            try:
                payout[rid] = (int(r["2連単_配当"]), int(r["3連単_配当"]))
            except (ValueError, KeyError):
                pass
    return payout


def hon_ana_result(pred, since_ymd="20260101"):
    """本命確率/穴確率の帯ごとに、実際の結果（的中率）を集計＝購入判断の材料。
    本命確率=モデル1番手(p_win最大)。穴確率=モデル順位4-6のp_win合計。
    本命的中=1着がモデル1番手。穴的中=1着がモデル順位4-6（軽視艇）。
    返り値 {hon, ana}: 各 [[帯ラベル, 予測中央%, 実測%, レース数], ...]。"""
    from collections import defaultdict
    races = defaultdict(dict)
    for r in pred.values():
        if r.get("race_id", "")[2:10] < since_ymd:
            continue
        races[r["race_id"]][int(r["枠番"])] = (r.get("p_win"), r.get("finish_rank"))

    hon_edges = [0, .40, .50, .60, .70, .80, 1.01]
    ana_edges = [0, .08, .12, .16, .20, .25, .30, 1.01]
    honb = [[0, 0, 0.0] for _ in hon_edges[:-1]]      # n, hit, sum_pred
    anab = [[0, 0, 0.0] for _ in ana_edges[:-1]]

    def add(bins, edges, p, hit):
        for i in range(len(edges) - 1):
            if edges[i] <= p < edges[i + 1]:
                bins[i][0] += 1
                bins[i][1] += hit
                bins[i][2] += p
                return

    for b in races.values():
        if len(b) != 6:
            continue
        s, fin, ok = [], {}, True
        for w in range(1, 7):
            try:
                s.append(float(b[w][0]))
            except (TypeError, ValueError):
                ok = False
                break
            fin[w] = b[w][1]
        if not ok or not any(fin[w] == "1" for w in range(1, 7)):
            continue
        order = sorted(range(6), key=lambda i: -s[i])     # モデル順位（0始まり艇index）
        win = next(i for i in range(6) if fin[i + 1] == "1")
        rank = order.index(win) + 1                       # 1=本命
        hon = s[order[0]]
        ana = s[order[3]] + s[order[4]] + s[order[5]]
        add(honb, hon_edges, hon, 1 if rank == 1 else 0)
        add(anab, ana_edges, ana, 1 if rank >= 4 else 0)

    def lab(edges, i):
        lo = int(round(edges[i] * 100))
        if edges[i + 1] > 1:
            return f"{lo}%+"
        return f"{lo}-{int(round(edges[i + 1] * 100))}"

    def fmt(bins, edges):
        return [[lab(edges, i), round(sp / n * 100, 1), round(hit / n * 100, 1), n]
                for i, (n, hit, sp) in enumerate(bins) if n]
    return {"hon": fmt(honb, hon_edges), "ana": fmt(anab, ana_edges)}


def load_kresult(keep):
    """keep の K-file から race_id -> {km:決まり手, shin:{枠:進入}, st:{枠:ST}}。"""
    yy = {d[2:4] + d[5:7] + d[8:10] for d in keep}
    kres = {}
    for kp in glob.glob("data/k*.csv"):
        m = re.search(r"k(\d{6})", kp)
        if not m or m.group(1) not in yy:
            continue
        for r in load(kp):
            if (r.get("status") or "") != "finish":
                continue
            code = VENUE_CODE.get(r["会場"], "00")
            y, mo, dd = r["日付"].split("/")
            rid = f"{code}{int(y):04d}{int(mo):02d}{int(dd):02d}{int(r['レース']):02d}"
            d = kres.setdefault(rid, {"km": r.get("決まり手", ""), "shin": {}, "st": {}})
            try:
                d["shin"][int(r["艇番"])] = int(r["進入コース"])
            except (ValueError, KeyError):
                pass
            d["st"][int(r["艇番"])] = to_float(r.get("スタートタイミング"))
    return kres


def cause_comment(pm, fin, kr):
    """予想と結果のズレの原因を1文で説明。kr=load_kresult の1レース分。的中時は順当コメント。"""
    hm = max(range(6), key=lambda i: pm[i]) + 1            # 本命枠
    order = sorted([w for w in range(1, 7)
                    if fin[w - 1] is not None and fin[w - 1] >= 1],
                   key=lambda w: fin[w - 1])
    if not order:
        return None
    win = order[0]
    km = kr.get("km", "") if kr else ""
    shin = kr.get("shin", {}) if kr else {}
    st = kr.get("st", {}) if kr else {}
    if win == hm:
        return f"順当：本命{hm}号艇が{km or '1着'}で的中。"
    r = []
    # 進入が枠なりから崩れたか
    if any(shin.get(w) and shin[w] != w for w in range(1, 7)):
        r.append("進入が枠なりから変化（前づけ等）")
    # 決まり手ベースの要因
    if km in ("まくり", "まくり差し") and win >= 3:
        r.append(f"{win}号艇の{km}が決まり波乱")
    elif km == "差し":
        r.append(f"{win}号艇の差しで逆転")
    elif km == "抜き":
        r.append("抜きで展開一変")
    elif km == "逃げ" and win == 1:
        r.append("1号艇が逃げ切り（本命評価が届かず）")
    # 本命のスタート遅れ
    if st.get(hm) is not None and st[hm] >= 0.20:
        r.append(f"本命{hm}号艇のスタート遅れ(ST{st[hm]:.2f})")
    # 人気薄の激走
    if pm[win - 1] < 0.12:
        r.append(f"人気薄{win}号艇({round(pm[win-1]*100)}%)の激走")
    if not r:
        r.append(f"{win}号艇の{km or '決着'}で着")
    return "ズレ要因：" + "・".join(r[:2]) + "。"


def recent_stats(out, payout):
    """前日・前々日（結果のある日）の場別 2連単/3連単 的中率・回収率。
    前提ベット: 予想確率で点数変動（2連単=上位 k_ex≦5 / 3連単=上位 k_tri≦20, 各100円）。
    回収率 = Σ配当 / (Σ点数 × 100)。"""
    ag = {}
    dmin = dmax = None
    for r in out:
        fins = [b[2] for b in r["b"]]
        if not any(f == 1 for f in fins):     # 結果のあるレースのみ
            continue
        sv = [b[1] for b in r["b"]]
        order = sorted([w for w in range(1, 7)
                        if fins[w - 1] is not None and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if fins[order[0] - 1] != 1:
            continue
        hon = max(sv) / 1000.0
        kx, kt = k_ex(hon), k_tri(hon)
        # 当日の買い目と同じ除外（B2/不振1号艇）。点数は除外後に実際に買える数。
        excl = {e[0] for e in r.get("xb", [])}
        m = sum(1 for w in range(1, 7) if sv[w - 1] > 0 and w not in excl)
        bet2 = min(kx, m * (m - 1))
        bet3 = min(kt, m * (m - 1) * (m - 2))
        po = payout.get(r["id"], (0, 0))
        a = ag.setdefault(r["c"], {"v": r["v"], "n2": 0, "h2": 0, "p2": 0, "pts2": 0,
                                   "n3": 0, "h3": 0, "p3": 0, "pts3": 0})
        dmin = r["d"] if dmin is None or r["d"] < dmin else dmin
        dmax = r["d"] if dmax is None or r["d"] > dmax else dmax
        if len(order) >= 2 and bet2 > 0:
            a["n2"] += 1
            a["pts2"] += bet2
            if _pl_rank(sv, 2, tuple(order[:2]), excl) <= bet2:
                a["h2"] += 1
                a["p2"] += po[0]
        if len(order) >= 3 and bet3 > 0:
            a["n3"] += 1
            a["pts3"] += bet3
            if _pl_rank(sv, 3, tuple(order[:3]), excl) <= bet3:
                a["h3"] += 1
                a["p3"] += po[1]

    def stat(a):
        return [a["v"], a["n2"],
                round(a["h2"] / a["n2"] * 100) if a["n2"] else 0,
                round(a["p2"] / (a["pts2"] * 100) * 100) if a["pts2"] else 0,
                round(a["h3"] / a["n3"] * 100) if a["n3"] else 0,
                round(a["p3"] / (a["pts3"] * 100) * 100) if a["pts3"] else 0]

    rows = [stat(a) for a in ag.values()]
    rows.sort(key=lambda x: x[3], reverse=True)     # 2連単回収率の降順
    T = {k: sum(a[k] for a in ag.values()) for k in
         ["n2", "h2", "p2", "pts2", "n3", "h3", "p3", "pts3"]}
    T["v"] = "全場"
    return {"from": dmin, "to": dmax, "rows": rows, "all": stat(T)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--hist", default="features_player_history.csv")
    ap.add_argument("--date", default=None, help="基準日（当日）。既定=データ最新日")
    ap.add_argument("--days", type=int, default=3, help="さかのぼる日数（既定3=当日/前日/前々日）")
    ap.add_argument("--stats-from", default="2026-01-01",
                    help="場別成績の集計開始日（既定=2026-01-01。結果のある全レースを集計）")
    ap.add_argument("--out", default="today.html")
    args = ap.parse_args()

    rel = load(args.rel)
    pred = {(r["race_id"], r["枠番"]): r for r in load(args.pred)}
    hist = {(r["race_id"], r["枠番"]): r for r in load(args.hist)}

    all_dates = sorted({r["日付"] for r in rel})
    base = args.date or all_dates[-1]
    keep = [d for d in all_dates if d <= base][-args.days:]
    keep_set = set(keep)

    # race_id -> {d,c,v,no,mz,fs, b{枠:(名,pm,fin)}, feat{枠:{...}}}
    races = {}
    for r in rel:
        if r["日付"] not in keep_set:
            continue
        rid = r["race_id"]
        waku = r["枠番"]
        pr = pred.get((rid, waku), {})
        h = hist.get((rid, waku), {})
        try:
            pm = round(float(pr.get("p_win")) * 1000)
        except (TypeError, ValueError):
            pm = None
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            fin = None
        rc = races.setdefault(rid, {"d": r["日付"], "c": r["場コード"], "v": r["会場"],
                                    "no": int(r["レース"]),
                                    "mz": int(r.get("field_maezuke_flag", 0) or 0),
                                    "fs": to_float(r.get("field_strength_std")),
                                    # 気象（K-file 実況）。当日は中立化済み＝空＝未反映。
                                    "wx_tenki": r.get("天候", "") or "",
                                    "wx_wind": to_float(r.get("風速")),
                                    "wx_wave": to_float(r.get("波高")),
                                    "b": {}, "feat": {}})
        w = int(waku)
        rc["b"][w] = (r["選手名"], pm, fin)
        rc["feat"][w] = {
            "枠": w, "名": r["選手名"],
            "pwin": (pm / 1000) if pm is not None else None,
            "win_rank": to_float(r.get("winrate_rank_in_race")),
            "motor_rank": to_float(r.get("motor_rank_in_race")),
            "motor_rate": to_float(r.get("motor_top2_rate")),
            "recent": to_float(h.get("recent30_winrate")),
            "st_rank": to_float(r.get("st_rank_in_race")),
            "cl": to_float(r.get("class_ord")),       # 級別 A1=4/A2=3/B1=2/B2=1
            "lane_win": to_float(h.get("lane_win_rate")),
            "lane_n": to_float(h.get("lane_n")),
            "vown": to_float(h.get("venue_own_lane_winrate")),
        }

    kres = load_kresult(keep)
    payout = load_payouts(keep)
    out = []
    for rid, rc in races.items():
        if len(rc["b"]) != 6 or any(rc["b"][w][1] is None for w in range(1, 7)):
            continue
        cm = make_comment([rc["feat"][w] for w in range(1, 7)], rc["fs"])
        pm = [rc["b"][w][1] for w in range(1, 7)]
        fin = [rc["b"][w][2] for w in range(1, 7)]
        kr = kres.get(rid)
        km = kr["km"] if kr else ""
        cause = cause_comment([p / 1000 for p in pm], fin, kr) \
            if any(f == 1 for f in fin) else None
        po = payout.get(rid)                          # (2連単配当, 3連単配当)
        # 買い目から除外する枠（検証済ガイドライン: 回収率を保ち賭け金を絞る）。
        xb = bet_exclude({w: rc["feat"][w].get("cl") for w in range(1, 7)},
                         rc["feat"][1].get("lane_win"), max(pm) / 1000)
        out.append({"id": rid, "d": rc["d"], "c": rc["c"], "v": rc["v"],
                    "no": rc["no"], "mz": rc["mz"], "xb": xb,
                    "fs": rc["fs"], "cm": cm, "km": km, "cause": cause,
                    "po": list(po) if po else None,
                    # 気象（当日は空＝未反映）。表示用。tenki/wind(m)/wave(cm)
                    "wx": [rc.get("wx_tenki", ""), rc.get("wx_wind"), rc.get("wx_wave")],
                    "b": [[rc["b"][w][0], rc["b"][w][1], rc["b"][w][2]]
                          for w in range(1, 7)],
                    # 穴候補の判断材料: 枠ごと [モーターレース内順位, 直近30走勝率, ST順位]
                    "ft": [[rc["feat"][w].get("motor_rank"),
                            rc["feat"][w].get("recent"),
                            rc["feat"][w].get("st_rank")]
                           for w in range(1, 7)]})
    out.sort(key=lambda x: (x["d"], x["c"], x["no"]))

    # 日付ラベル（当日/前日/前々日）
    rel_labels = ["当日", "前日", "前々日", "3日前", "4日前", "5日前", "6日前"]
    labels = []
    for i, d in enumerate(reversed(keep)):       # 新しい順
        labels.append([rel_labels[i] if i < len(rel_labels) else d, d])

    vstats = venue_stats(rel, pred, hist, args.stats_from)
    recent = recent_stats(out, payout)
    hra = hon_ana_result(pred)

    payload = {"labels": labels, "base": base, "races": out,
               "vstats": vstats, "recent": recent, "hra": hra}
    html = HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False,
                                               separators=(",", ":")))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"○ 当日予想アプリ: {args.out}")
    print(f"  対象日 {keep}（既定表示={base}）/ レース {len(out)}")
    print(f"  場別成績: {args.stats_from}〜（{vstats['from']}〜{vstats['to']} "
          f"/ {vstats['n']}レース）")


HTML = r"""<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>競艇当日予想</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{font-family:"Yu Gothic UI",system-ui,sans-serif;margin:0 auto;padding:12px 14px 40px;
       background:#0f1115;color:#e6e6e6;max-width:560px}
  h1{font-size:18px;margin:0}
  .meta{font-size:12px;color:#9aa3b2;margin:4px 0 8px;line-height:1.5}
  .dsel{display:flex;gap:6px;margin:0 0 10px}
  .dbtn{flex:1;font-size:14px;padding:8px 6px;border-radius:8px;border:0.5px solid #39404d;
        background:transparent;color:#cdd6e2;cursor:pointer;text-align:center}
  .dbtn.on{background:#2b6cb0;color:#fff;border-color:#2b6cb0}
  .dbtn small{display:block;font-size:10px;color:#9aa3b2;margin-top:1px}
  .dbtn.on small{color:#cfe2ff}
  .vfilter{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 6px}
  .vbtn{font-size:14px;padding:6px 12px;border-radius:8px;border:0.5px solid #39404d;
        background:transparent;color:#cdd6e2;cursor:pointer}
  .vbtn.on{background:#374151;color:#fff;border-color:#4b5563}
  h3{font-size:15px;font-weight:600;margin:14px 0 4px;color:#b9c2d0}
  .row{display:flex;align-items:center;gap:8px;padding:10px 4px;border-bottom:0.5px solid #2a2f3a;cursor:pointer}
  .row:active{background:#12161d}
  .rno{font-size:13px;color:#9aa3b2;min-width:30px;font-weight:600}
  .wk{font-weight:700;border-radius:5px;padding:1px 8px;font-size:14px;min-width:24px;text-align:center;display:inline-block;border:0.5px solid #2a2f3a}
  .nm{font-size:13px;color:#e6e6e6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
  .wn{color:#f0c674;font-size:14px}
  .pw{font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap;color:#9aa3b2}
  .pw.s{color:#ffd54a;font-weight:700}
  .res{font-size:12px;white-space:nowrap;display:flex;align-items:center;gap:4px}
  .reslab{font-size:10px;color:#6b7280}
  .ok{color:#43c59e;font-weight:700}.ng{color:#e06b6b}
  .chev{color:#5b6472;font-size:16px}
  .back{display:inline-flex;align-items:center;gap:4px;font-size:14px;color:#6ea8fe;background:transparent;border:none;cursor:pointer;padding:8px 0}
  .dh{font-size:19px;font-weight:700;margin:2px 0}
  .warn{font-size:12px;color:#f0c674;background:#2a2015;border:1px solid #6b5a1a;border-radius:8px;padding:7px 10px;margin:6px 0;line-height:1.5}
  .cmt{font-size:13px;color:#cdd6e2;background:#141a1f;border-left:3px solid #3b82f6;border-radius:0 6px 6px 0;padding:9px 12px;margin:8px 0;line-height:1.7}
  .cmt .h{color:#8ea0ba;font-size:11px;margin-right:6px}
  .kmlab{font-size:12px;color:#cdd6e2;background:#2a2f3a;border-radius:6px;padding:1px 8px;margin-left:6px}
  .cause{font-size:12px;color:#e0c896;background:#241f15;border-left:3px solid #a8730a;border-radius:0 6px 6px 0;padding:7px 10px;margin:6px 0;line-height:1.6}
  .cause .h{color:#b89a5a;font-size:11px;margin-right:6px}
  .rbar{display:flex;align-items:center;gap:6px;margin:8px 0;flex-wrap:wrap}
  .rlab{font-size:12px;color:#9aa3b2}
  .arr{color:#5b6472;font-size:12px}
  .boat{display:flex;align-items:center;gap:8px;margin:8px 0}
  .fin{font-size:11px;color:#9aa3b2;min-width:26px}
  .fin b{color:#ffd54a}
  .bn{font-size:13px;color:#e6e6e6;min-width:78px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .barw{flex:1;height:15px;background:#1b212b;border-radius:4px;overflow:hidden}
  .bar{height:100%;border-radius:4px}
  .bp{font-size:12px;font-variant-numeric:tabular-nums;color:#cdd6e2;min-width:40px;text-align:right}
  .sec{font-size:13px;font-weight:600;color:#9aa3b2;margin:16px 0 6px;display:flex;align-items:center;gap:8px}
  .tag{font-size:11px;border-radius:8px;padding:1px 7px}
  .tag.h{background:#10362c;color:#43c59e}.tag.m{background:#3a1f1f;color:#e06b6b}
  .kbadge{font-size:10px;color:#7fb2ff;background:#14233a;border:0.5px solid #2b4a6f;border-radius:7px;padding:1px 7px;margin-left:8px;font-weight:600}
  .crow{display:flex;align-items:center;gap:6px;padding:6px 2px;border-bottom:0.5px solid #2a2f3a}
  .crow.hit{background:#10362c;border-radius:5px}
  .crow.evplus{background:#173a1f;border-radius:5px;box-shadow:inset 3px 0 0 #43c59e}
  .ev{font-size:11px;color:#9aa3b2;font-variant-numeric:tabular-nums;white-space:nowrap;min-width:96px;text-align:right}
  .ev b{color:#cdd6e2}.crow.evplus .ev b{color:#7ee0a4}
  .oddsupd{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:12px 0 2px}
  .oddsbtn{font-size:13px;font-weight:700;color:#0b0e14;background:#5dc7e0;border:none;border-radius:8px;padding:8px 14px;cursor:pointer}
  .oddsbtn:disabled{opacity:.5}
  .oddsstat{font-size:11px;color:#9aa3b2}
  .mc{font-size:13px;border-radius:4px;padding:1px 7px;border:0.5px solid #2a2f3a;font-weight:700;display:inline-block}
  .rk{font-size:11px;color:#5b6472;min-width:20px}
  .cp{margin-left:auto;font-size:12px;font-variant-numeric:tabular-nums;color:#9aa3b2;text-align:right}
  .odds{display:block;font-size:11px;color:#e0a93b;font-weight:600}
  .legend{font-size:11px;color:#7e8796;margin:18px 0 0;line-height:1.6}
  .tabs{display:flex;gap:6px;margin:6px 0 10px}
  .tb{flex:1;font-size:14px;padding:8px 6px;border-radius:8px;border:0.5px solid #39404d;background:transparent;color:#cdd6e2;cursor:pointer;text-align:center}
  .tb.on{background:#2b6cb0;color:#fff;border-color:#2b6cb0}
  .swrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:4px}
  table.st{border-collapse:collapse;width:100%;min-width:520px;font-size:13px}
  table.st th,table.st td{border-bottom:0.5px solid #232a36;padding:7px 8px;text-align:right;white-space:nowrap}
  table.st th{color:#8ea0ba;font-weight:600;position:sticky;top:0;background:#12161d}
  table.st td.k,table.st th.k{text-align:left}
  table.st .g2{color:#7fb2ff}.st .g3{color:#ffd082}
  table.st tbody tr:nth-child(odd){background:#12161d}
  table.st tr.all{font-weight:700}
  table.st tr.all td{border-top:1px solid #39404d;color:#fff}
  .num{font-variant-numeric:tabular-nums}
  .scol{color:#9aa3b2}
  .ha2{display:flex;align-items:center;gap:6px;white-space:nowrap}
  .ha2 .hp{font-size:12px;color:#9aa3b2;font-variant-numeric:tabular-nums}
  .ha2 .hp b{color:#ffd54a;font-size:13px}
  .ha2 .hp b.a{color:#e0a93b}
  .ha{display:flex;gap:10px;margin:8px 0}
  .hacell{flex:1;background:#141a1f;border:0.5px solid #2a2f3a;border-radius:8px;padding:8px 10px;text-align:center}
  .hak{font-size:11px;color:#9aa3b2}
  .hav{font-size:24px;font-weight:800;font-variant-numeric:tabular-nums;margin:2px 0;line-height:1.1}
  .hav.hon{color:#ffd54a}.hav.ana{color:#e0a93b}
  .hsub{font-size:12px;color:#cdd6e2}
  .lvl{font-size:11px;font-weight:700;border-radius:8px;padding:2px 9px;display:inline-block}
  .lvl.tetsu{background:#10362c;color:#43c59e}
  .lvl.std{background:#2a2f3a;color:#cdd6e2}
  .lvl.haran{background:#3a1f1f;color:#e06b6b}
  .lvl.honhit{background:#10362c;color:#43c59e}
  .lvl.anahit{background:#3a2f15;color:#e0a93b}
  .lvl.midhit{background:#2a2f3a;color:#cdd6e2}
  .row.done{flex-wrap:wrap;row-gap:5px}
  .resline{flex-basis:100%;display:flex;align-items:center;gap:4px;padding-left:38px;font-size:12px;color:#9aa3b2}
  .resline .rll{color:#6b7280;font-size:11px;margin-right:2px}
  .resline .yen{margin-left:auto;font-variant-numeric:tabular-nums;color:#cdd6e2;font-weight:600}
  .resline .yen.hit{color:#43c59e}
</style></head><body>
<div id="app"></div>
<script>
const D=__DATA__;
const LC={1:['#ffffff','#111111'],2:['#1b1b1b','#ffffff'],3:['#e23b3b','#ffffff'],4:['#2f7fd6','#ffffff'],5:['#f2c025','#111111'],6:['#28a35a','#ffffff']};
let selDate=D.labels[0][1], cur='ALL', sel=null, tab='pred';
const root=document.getElementById('app');
const mmdd=s=>s.slice(5);
function chip(w,cls){const a=LC[w];return '<span class="'+(cls||'wk')+'" style="background:'+a[0]+';color:'+a[1]+'">'+w+'</span>';}
function dayRaces(){return D.races.filter(r=>r.d===selDate);}
function hasResult(r){return r.b.some(x=>x[2]===1);}  // 1着が決まっていれば結果あり（F/失格混在でも可）
function finishOrder(r){return r.b.map((b,i)=>[i+1,b[2]]).filter(x=>x[1]).sort((a,b)=>a[1]-b[1]).map(x=>x[0]);}
function eqArr(a,b){return a&&b&&a.length===b.length&&a.every((v,i)=>v===b[i]);}
// excl={枠:1} を渡すとその枠を含む買い目を除外（確率の分母totは全艇のまま＝必要倍は正しい）。
function plTop(s,kind,k,excl){excl=excl||{};const idx=[0,1,2,3,4,5].filter(i=>s[i]>0&&!excl[i+1]);const tot=s.reduce((a,b)=>a+b,0);const out=[];
  if(kind===2){for(const i of idx)for(const j of idx){if(j===i)continue;out.push([[i+1,j+1],s[i]/tot*s[j]/(tot-s[i])]);}}
  else{for(const i of idx)for(const j of idx){if(j===i)continue;for(const l of idx){if(l===i||l===j)continue;out.push([[i+1,j+1,l+1],s[i]/tot*s[j]/(tot-s[i])*s[l]/(tot-s[i]-s[j])]);}}}
  out.sort((a,b)=>b[1]-a[1]);return out.slice(0,k);}

// 予想確率（本命確率hon=0-1）に応じた買目点数。堅い→少点/荒れ→多点。上限 2連単5/3連単20。
function kEx(hon){return hon>=0.65?2:hon>=0.50?3:hon>=0.40?4:5;}
function kTri(hon){return hon>=0.65?4:hon>=0.50?7:hon>=0.40?10:hon>=0.30?14:20;}
// 本命確率/穴確率/荒れ度/穴筆頭。s=per-mille p_win 配列。
// 本命=モデル1番手(p_win最大)。穴=モデル順位4-6(=軽視された艇)の1着。
// 検証(29,233R OOS): 本命確率はキャリブ良好、穴率はΣp_win(4-6)とほぼ一致(平均13%)。
// モーター/直近はp_winに織り込み済み＝確率を超える穴シグナルは無い→材料は脅威の目安として表示。
function honAna(r){
  const s=r.b.map(x=>x[1]);
  const idx=[0,1,2,3,4,5].sort((a,b)=>s[b]-s[a]);   // モデル順位降順
  const hon=s[idx[0]]/1000;
  const ana=(s[idx[3]]+s[idx[4]]+s[idx[5]])/1000;    // 順位4-6の合計
  const lvl = hon>=0.65?['鉄板','tetsu'] : hon<0.45?['波乱含み','haran'] : ['標準','std'];
  return {hon,ana,lvl:lvl[0],lvlcls:lvl[1],hmLane:idx[0]+1,anaLane:idx[3]+1};
}

// 結果（過去日）の1着が、モデルで本命/中位/穴のどれだったか。
// 本命=モデル1番手が1着 / 中位=2-3番手 / 穴=4-6番手（軽視艇）が1着。
function winKind(r){
  const s=r.b.map(x=>x[1]);
  const win=finishOrder(r)[0];
  const rank=[0,1,2,3,4,5].sort((a,b)=>s[b]-s[a]).indexOf(win-1)+1;   // 1=本命
  if(rank===1)return {lab:'本命',cls:'honhit',rank};
  if(rank>=4)return {lab:'穴',cls:'anahit',rank};
  return {lab:'中位',cls:'midhit',rank};
}

function listView(){
  const rs=dayRaces();
  const lab=D.labels.find(l=>l[1]===selDate);
  let h='<div class="dsel">';
  for(const l of D.labels)h+='<button class="dbtn'+(selDate===l[1]?' on':'')+'" data-d="'+l[1]+'">'+l[0]+'<small>'+mmdd(l[1])+'</small></button>';
  h+='</div>';
  h+='<div class="meta">直前情報なしモデル（朝の出走表のみ）・ '+rs.length+'レース ・ タップで詳細'
    +(hasResult(rs[0]||{b:[]})?' ・ 結果あり（的中=2連単/3連単の変動上位に決着 / 下段に3連単の決着と配当）':'')+'</div>';
  const venues=[];const seen={};for(const r of rs){if(!seen[r.c]){seen[r.c]=1;venues.push([r.c,r.v]);}}
  h+='<div class="vfilter"><button class="vbtn'+(cur==='ALL'?' on':'')+'" data-v="ALL">全場</button>';
  for(const a of venues)h+='<button class="vbtn'+(cur===a[0]?' on':'')+'" data-v="'+a[0]+'">'+a[1]+'</button>';
  h+='</div>';
  rs.forEach((r,gi)=>{
    if(cur!=='ALL'&&cur!==r.c)return;
    if(rs.findIndex(x=>x.c===r.c)===gi)h+='<h3>'+r.v+'</h3>';
    let hm=0;for(let w=1;w<6;w++)if(r.b[w][1]>r.b[hm][1])hm=w;
    const ha=honAna(r);const done=hasResult(r);
    h+='<div class="row'+(done?' done':'')+'" data-i="'+gi+'"><span class="rno">'+r.no+'R</span>'+chip(hm+1)
     +'<span class="nm">'+r.b[hm][0]+'</span>'+(r.mz?'<span class="wn">&#9888;</span>':'');
    h+='<span class="ha2">'+(done?'':'<span class="lvl '+ha.lvlcls+'">'+ha.lvl+'</span>')
      +'<span class="hp">本命<b>'+Math.round(ha.hon*100)+'</b> 穴<b class="a">'+Math.round(ha.ana*100)+'</b></span></span>';
    if(done){
      const s=r.b.map(x=>x[1]);const ord=finishOrder(r);
      const hon=Math.max(...s)/1000;const nEx=kEx(hon),nTri=kTri(hon);
      const xset={};(r.xb||[]).forEach(e=>xset[e[0]]=1);
      const ex=ord.slice(0,2);const tri=ord.slice(0,3);
      const exHit=ex.length>=2&&plTop(s,2,nEx,xset).some(c=>eqArr(c[0],ex));
      const triHit=tri.length>=3&&plTop(s,3,nTri,xset).some(c=>eqArr(c[0],tri));
      const hit=exHit||triHit;                           // 2連単/3連単の変動上位に決着が入れば的中
      const pay=r.po?r.po[1]:null;
      h+='<span class="res">'+(hit?'<span class="ok">的中</span>':'<span class="ng">不的中</span>')+'</span>'
        +'<span class="chev">&rsaquo;</span>';
      h+='<div class="resline"><span class="rll">3連単</span>';
      tri.forEach((w,i)=>{h+=(i?'<span class="arr">&rarr;</span>':'')+chip(w,'mc');});
      if(triHit)h+='<span class="ok" style="font-size:11px">的中</span>';
      h+='<span class="yen'+(triHit?' hit':'')+'">'+(pay!=null?'¥'+pay.toLocaleString():'配当 –')+'</span></div>';
    }else{
      h+='<span class="chev">&rsaquo;</span>';
    }
    h+='</div>';
  });
  return h;
}

function detailView(r){
  const s=r.b.map(x=>x[1]);const mx=Math.max(...s);
  const done=hasResult(r);const ord=done?finishOrder(r):null;
  const actEx=(done&&ord.length>=2)?ord.slice(0,2):null, actTri=(done&&ord.length>=3)?ord.slice(0,3):null;
  let hm=0;for(let w=1;w<6;w++)if(s[w]>s[hm])hm=w;
  let h='<button class="back">&lsaquo; 一覧へ戻る</button>';
  h+='<div class="dh">'+r.v+' '+r.no+'R</div>';
  h+='<div class="meta">'+r.d+' ・ race_id '+r.id+' ・ field_strength_std '+(r.fs!=null?(+r.fs).toFixed(2):'–')+' ・ 直前情報なしモデル（展示/オッズ不使用）</div>';
  // 気象（K-file実況）。当日は中立化＝空なので「天候は当日未反映」と表示。
  if(r.wx){
    const tk=r.wx[0], wd=r.wx[1], wv=r.wx[2];
    if(tk||wd!=null||wv!=null){
      h+='<div class="meta">🌤 '+(tk||'–')+(wd!=null?' ・ 風'+wd+'m':'')+(wv!=null?' ・ 波'+wv+'cm':'')+'<span style="color:#6b7280"> （荒れ度・気象を予想に反映）</span></div>';
    }else{
      h+='<div class="meta" style="color:#6b7280">🌤 天候は当日予想に未反映（場の荒れ度のみ反映・気象は後日反映予定）</div>';
    }
  }
  if(r.mz)h+='<div class="warn">&#9888; 隊形警戒：前づけ常習者がいて枠なりが崩れやすく、本命の信頼度は割り引いて。</div>';
  if(r.cm)h+='<div class="cmt"><span class="h">予想コメント</span>'+r.cm[0]+'<br><span class="h" style="visibility:hidden">予想コメント</span>'+r.cm[1]+'</div>';
  // 本命確率 / 穴確率
  const ha=honAna(r);
  h+='<div class="sec">本命確率 / 穴確率</div>';
  h+='<div class="ha"><div class="hacell"><div class="hak">本命確率</div><div class="hav hon">'
    +Math.round(ha.hon*100)+'%</div><div class="hsub">'+chip(ha.hmLane,'mc')+' '+r.b[ha.hmLane-1][0]+'</div></div>'
    +'<div class="hacell"><div class="hak">穴確率（4-6番手）</div><div class="hav ana">'
    +Math.round(ha.ana*100)+'%</div><div class="hsub"><span class="lvl '+ha.lvlcls+'">'+ha.lvl+'</span></div></div></div>';
  {const al=ha.anaLane, af=r.ft?r.ft[al-1]:null, afin=done?r.b[al-1][2]:null;
   h+='<div class="cause" style="border-left-color:#e0a93b;color:#e0c896"><span class="h" style="color:#b89a5a">穴候補</span>'
     +chip(al,'mc')+' '+r.b[al-1][0]+' … '
     +'モーター'+(af&&af[0]!=null?'レース内'+Math.round(af[0])+'位':'–')
     +' / 直近勝率'+(af&&af[1]!=null?Math.round(af[1]*100)+'%':'–')
     +' / ST'+(af&&af[2]!=null?'レース内'+Math.round(af[2])+'位':'–')
     +'（1着確率'+(r.b[al-1][1]/10).toFixed(1)+'%）'
     +(done?' <b style="color:'+(afin===1?'#43c59e':'#9aa3b2')+'">→ 結果'+(afin===1?'1着！（穴的中）':(afin?afin+'着':'－'))+'</b>':'')
     +'<br><span style="font-size:11px;color:#9aa3b2">※モデルはモーター・直近を織り込み済み（検証で確率を超える穴シグナルは無し）。材料が揃う穴ほど展開次第で1着の目。本命が弱いレースほど荒れやすい（本命&lt;40%で穴率約20%／≥70%で約8%）。</span></div>';}
  if(done){
    h+='<div class="rbar"><span class="rlab">結果</span>';
    ord.forEach((w,i)=>{h+=(i?'<span class="arr">&rarr;</span>':'')+chip(w,'mc');});
    if(r.km)h+='<span class="kmlab">'+r.km+'</span>';
    {const wk=winKind(r);
     h+='<span class="lvl '+wk.cls+'" style="margin-left:6px">'+wk.lab+(wk.rank>1?'（モデル'+wk.rank+'番手）':'的中')+'</span>';}
    h+='</div>';
    if(r.cause)h+='<div class="cause"><span class="h">結果分析</span>'+r.cause+'</div>';
    if(r.po){
      const plOf=c=>{let p=1,rem=s.reduce((a,b)=>a+b,0);for(const w of c){p*=s[w-1]/rem;rem-=s[w-1];}return p;};
      const evrow=(lab,combo,po)=>{if(!combo||po==null)return '';const need=1/plOf(combo),bai=po/100,ok=bai>=need;
        return lab+' '+combo.join('-')+'：配当<b>'+bai.toFixed(1)+'倍</b> / 必要'+need.toFixed(1)+'倍 → '
          +(ok?'<span class="ok">妙味◎(+EV)</span>':'<span style="color:#9aa3b2">届かず(-EV)</span>');};
      h+='<div class="cause" style="border-left-color:#5dc7e0;color:#cdd6e2"><span class="h" style="color:#7fb2ff">買えてた場合の妙味</span>'
        +evrow('2連単',actEx,r.po[0])+'<br>'+evrow('3連単',actTri,r.po[1])+'</div>';
    }
  }
  h+='<div class="sec">1着確率（モデル）</div>';
  r.b.forEach((b,w)=>{const a=LC[w+1];const fin=b[2];
    h+='<div class="boat">'+(done?'<span class="fin">'+(fin===1?'<b>1着</b>':(fin?fin+'着':'<span style="color:#6b7280">－</span>'))+'</span>':'')
     +chip(w+1)+'<span class="bn">'+b[0]+'</span>'
     +'<div class="barw"><div class="bar" style="width:'+Math.max(b[1]/mx*100,2)+'%;background:'+a[0]+'"></div></div>'
     +'<span class="bp">'+(b[1]/10).toFixed(1)+'%</span></div>';});
  // 買い目除外（B2 / 荒れ帯の不振1号艇）。確率は全艇ベース＝必要倍は不変。
  const xset={};(r.xb||[]).forEach(e=>xset[e[0]]=1);
  if(r.xb&&r.xb.length){
    h+='<div class="cause" style="border-left-color:#7f8896;color:#aab2bf"><span class="h" style="color:#9aa3b2">買い目から除外</span>'
      +r.xb.map(e=>chip(e[0],'mc')+' '+r.b[e[0]-1][0]+'（'+(e[1]==='B2'?'B2級':'1号艇 成績不振')+'）').join(' ')
      +'<br><span style="font-size:11px;color:#7e8796">※検証で「回収率を保ったまま賭け金を絞れる」と確認した枠を2連単/3連単の買い目から除外（確率・本命/穴の表示はそのまま）。</span></div>';
  }
  // 2連単 上位（予想確率で点数変動・上限5）
  const honD=Math.max(...s)/1000;const nEx=kEx(honD),nTri=kTri(honD);
  const ex=plTop(s,2,nEx,xset);
  let exHit=actEx?ex.some(c=>eqArr(c[0],actEx)):false;
  h+='<div class="sec">2連単 上位'+nEx+'<span class="kbadge">確率連動</span>'+(actEx?(exHit?'<span class="tag h">的中</span>':'<span class="tag m">圏外</span>'):'')+'</div>';
  ex.forEach((c,i)=>{const hit=actEx&&eqArr(c[0],actEx);
    h+='<div class="crow'+(hit?' hit':'')+'" data-combo="'+c[0].join('-')+'" data-p="'+c[1]+'"><span class="rk">'+(i+1)+'</span>'+chip(c[0][0],'mc')+'<span class="arr">&rarr;</span>'+chip(c[0][1],'mc')
     +(hit?'<span class="ok" style="font-size:11px;margin-left:4px">的中</span>':'')
     +'<span class="cp">'+(c[1]*100).toFixed(1)+'%<span class="odds">必要'+(1/c[1]).toFixed(1)+'倍</span></span><span class="ev"></span></div>';});
  if(actEx&&!exHit){h+='<div class="crow"><span class="rk">実</span>'+chip(actEx[0],'mc')+'<span class="arr">&rarr;</span>'+chip(actEx[1],'mc')+'<span class="cp ng">実際の結果</span></div>';}
  // 3連単 上位（予想確率で点数変動・上限20）
  const tri=plTop(s,3,nTri,xset);
  let triHit=actTri?tri.some(c=>eqArr(c[0],actTri)):false;
  h+='<div class="sec">3連単 上位'+nTri+'<span class="kbadge">確率連動</span>'+(actTri?(triHit?'<span class="tag h">的中</span>':'<span class="tag m">圏外</span>'):'')+'</div>';
  tri.forEach((c,i)=>{const hit=actTri&&eqArr(c[0],actTri);
    h+='<div class="crow'+(hit?' hit':'')+'" data-combo="'+c[0].join('-')+'" data-p="'+c[1]+'"><span class="rk">'+(i+1)+'</span>'+chip(c[0][0],'mc')+'<span class="arr">&rarr;</span>'+chip(c[0][1],'mc')+'<span class="arr">&rarr;</span>'+chip(c[0][2],'mc')
     +(hit?'<span class="ok" style="font-size:11px;margin-left:4px">的中</span>':'')
     +'<span class="cp">'+(c[1]*100).toFixed(1)+'%<span class="odds">必要'+(1/c[1]).toFixed(1)+'倍</span></span><span class="ev"></span></div>';});
  if(actTri&&!triHit){h+='<div class="crow"><span class="rk">実</span>'+chip(actTri[0],'mc')+'<span class="arr">&rarr;</span>'+chip(actTri[1],'mc')+'<span class="arr">&rarr;</span>'+chip(actTri[2],'mc')+'<span class="cp ng">実際の結果</span></div>';}
  // 実オッズ取得ボタンは一旦非表示（必要オッズ表示のみ残す）
  h+='<div class="cause" style="border-left-color:#e0a93b;color:#e0c896;margin-top:10px"><span class="h" style="color:#b89a5a">期待値の見方</span>'
    +'<span class="odds" style="display:inline;color:#e0a93b">必要◯倍</span>＝この買い目が期待値プラスになる最低オッズ（＝1÷確率）。'
    +'発走前の実オッズがこれを超えていれば長期的に勝てる買い目（モデル確率は実測とほぼ一致＝信頼できる）。</div>';
  h+='<div class="legend">※ 確率は朝の出走表のみから算出（展示・オッズ不使用）。本命=1着確率最大の枠。前日・前々日は結果と的中可否を表示。'
    +'買目点数は予想確率に連動（堅い→少点／荒れ→多点、2連単≦5・3連単≦20）＝本命確率'+Math.round(honD*100)+'%で2連単'+nEx+'点/3連単'+nTri+'点。'
    +'実オッズはモデル外なので各自で確認し「必要◯倍」と比較してください。</div>';
  return h;
}

function nav(){
  return '<h1>競艇当日予想</h1><div class="tabs">'
    +'<button class="tb'+(tab==='pred'?' on':'')+'" data-t="pred">予想</button>'
    +'<button class="tb'+(tab==='stats'?' on':'')+'" data-t="stats">場別成績</button></div>';
}
// 棒グラフ（自己完結SVG）。data=[[帯ラベル, 予想中央%, 実測%, レース数], ...]。
// 棒=実測、◇=予想中央。opts.odds で各帯に必要オッズ目安(=100/実測%)を表示。
function barSVG(data,col,opts){
  opts=opts||{};
  if(!data||!data.length)return '';
  const W=330,padL=30,padR=8,padT=16,padB=opts.odds?58:42,H=padT+150+padB;
  let mx=Math.max(...data.flatMap(d=>[d[1],d[2]]),1);
  const M=Math.ceil(mx/(mx>50?20:mx>20?10:5))*(mx>50?20:mx>20?10:5);
  const plotW=W-padL-padR, plotH=H-padT-padB;
  const n=data.length, gap=plotW/n, bw=Math.min(gap*0.56,38);
  const y=v=>padT+plotH-(v/M)*plotH;
  let g='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;max-width:340px">';
  for(let t=0;t<=M+0.01;t+=M/5){
    g+='<line x1="'+padL+'" y1="'+y(t)+'" x2="'+(W-padR)+'" y2="'+y(t)+'" stroke="#222a33"/>';
    g+='<text x="'+(padL-4)+'" y="'+(y(t)+3)+'" fill="#7e8796" font-size="9" text-anchor="end">'+Math.round(t)+'</text>';
  }
  data.forEach((d,i)=>{
    const cx=padL+gap*i+gap/2, x=cx-bw/2, yt=y(d[2]);
    g+='<rect x="'+x+'" y="'+yt+'" width="'+bw+'" height="'+Math.max((d[2]/M)*plotH,1)+'" rx="2" fill="'+col+'"/>';
    g+='<text x="'+cx+'" y="'+(yt-4)+'" fill="#e6e6e6" font-size="10" text-anchor="middle" font-weight="700">'+d[2]+'</text>';
    const yp=y(d[1]);
    g+='<path d="M'+(cx-4)+' '+yp+' L'+cx+' '+(yp-4)+' L'+(cx+4)+' '+yp+' L'+cx+' '+(yp+4)+' Z" fill="none" stroke="#9aa3b2" stroke-width="1.4"/>';
    g+='<text x="'+cx+'" y="'+(H-padB+13)+'" fill="#9aa3b2" font-size="9" text-anchor="middle">'+d[0]+'</text>';
    g+='<text x="'+cx+'" y="'+(H-padB+24)+'" fill="#6b7280" font-size="8" text-anchor="middle">'+d[3]+'R</text>';
    if(opts.odds){const need=d[2]>0?100/d[2]:0;
      g+='<text x="'+cx+'" y="'+(H-padB+37)+'" fill="#e0a93b" font-size="9" text-anchor="middle" font-weight="700">'+need.toFixed(1)+'倍</text>';}
  });
  g+='<text x="'+(padL+plotW/2)+'" y="'+(H-3)+'" fill="#9aa3b2" font-size="9.5" text-anchor="middle">'+(opts.xlab||'')+'</text>';
  g+='</svg>';
  return g;
}
function statsView(){
  // 場別成績は Python 側で全期間（2026年〜）集計済み。ここでは描画のみ。
  const V=D.vstats; let h=nav();
  if(!V||!V.n){return h+'<div class="meta">結果データがまだありません。</div>';}
  const cell=(v,extra)=>'<td class="num'+(extra?' '+extra:'')+'">'+v+'%</td>';
  const row=(a,all)=>'<tr'+(all?' class="all"':'')+'><td class="k">'+a[0]+'</td>'
    +'<td class="num scol">'+a[1]+'</td><td class="num">'+a[2]+'%</td>'
    +cell(a[3],all?'':'g2')+cell(a[4],all?'':'g2')+cell(a[5],all?'':'g2')
    +cell(a[6],all?'':'g3')+cell(a[7],all?'':'g3')+cell(a[8],all?'':'g3')+'</tr>';
  h+='<div class="meta">対象 '+V.from+'〜'+V.to+'（'+V.n+'レース・収集データ全体）・ '
    +'数字=予想上位K通り以内に決着が入った割合。「変動」=予想確率連動の点数（堅い→少点／荒れ→多点）</div>';
  h+='<div class="swrap"><table class="st"><thead><tr>'
    +'<th class="k">会場</th><th>R数</th><th>本命<br>1着</th>'
    +'<th class="g2">2連単<br>本命</th><th class="g2">top3</th><th class="g2">変動<br>≤5</th>'
    +'<th class="g3">3連単<br>本命</th><th class="g3">top3</th><th class="g3">変動<br>≤20</th></tr></thead><tbody>';
  for(const a of V.rows)h+=row(a,false);
  h+=row(V.all,true);
  h+='</tbody></table></div>';
  h+='<div class="legend">※ 収集データ全体（'+V.from+'〜'+V.to+'）の結果から集計。本命=1着確率最大の枠。'
    +'「変動」=予想確率に応じた点数（2連単≤5/3連単≤20）以内に実際の決着が含まれた割合（その点数を買えば当たる割合）。'
    +'※「変動」列のみB2・不振1号艇を除外（本命/top3はモデル診断＝除外なし）。'
    +'※ 4月までは学習期間を含むため的中率はやや高めに出る（5月以降が純粋な検証）。</div>';
  // 前日・前々日の場別 的中率＋回収率
  const RC=D.recent;
  if(RC&&RC.rows&&RC.rows.length){
    const rrow=(a,all)=>'<tr'+(all?' class="all"':'')+'><td class="k">'+a[0]+'</td>'
      +'<td class="num scol">'+a[1]+'</td>'
      +'<td class="num g2">'+a[2]+'%</td><td class="num g2">'+a[3]+'%</td>'
      +'<td class="num g3">'+a[4]+'%</td><td class="num g3">'+a[5]+'%</td></tr>';
    h+='<div class="sec" style="margin-top:22px;color:#cdd6e2;font-size:14px">前日・前々日の的中率・回収率（'+RC.from.slice(5)+'〜'+RC.to.slice(5)+'）</div>';
    h+='<div class="meta">実践的中＝予想確率連動の点数を実際に買った場合の的中率（2連単≤5/3連単≤20点, 各100円）。回収率100%超で利益。'
      +'B2・荒れ帯の不振1号艇を買い目から除外したベース（点数=除外後の実点数）。</div>';
    h+='<div class="swrap"><table class="st"><thead><tr>'
      +'<th class="k">会場</th><th>R数</th>'
      +'<th class="g2">2連単<br>実践的中</th><th class="g2">回収率</th>'
      +'<th class="g3">3連単<br>実践的中</th><th class="g3">回収率</th></tr></thead><tbody>';
    for(const a of RC.rows)h+=rrow(a,false);
    h+=rrow(RC.all,true);
    h+='</tbody></table></div>';
    h+='<div class="legend">※ 直近2日のみ＝サンプル小。回収率は高配当1本で大きく振れる（特に3連単）。'
      +'確率帯別バックテスト(2026全体)ではどの帯も回収率100%未満（控除率約25%の壁）＝確率だけで機械的に買うと負ける。'
      +'各レース詳細の「買えてた場合の妙味」で、実配当が必要オッズを超えたか（＝買えてたら+EVか）を確認できる。</div>';
  }
  // 本命確率・穴確率 と 実際の結果（購入判断の材料）
  const HA=D.hra;
  if(HA&&HA.hon&&HA.hon.length){
    h+='<div class="sec" style="margin-top:22px;color:#cdd6e2;font-size:14px">本命確率・穴確率と実際の結果（2026年〜）</div>';
    h+='<div class="meta">棒=実測（実際にそうなった割合）／<span style="color:#9aa3b2">◇</span>=モデル予想（帯の中央値）。棒が右ほど伸びれば「確率が高い予想ほど当たる」。各帯のレース数（R）も表示。</div>';
    h+='<div style="text-align:center"><div style="font-size:13px;color:#43c59e;margin:8px 0 2px;font-weight:600">① 本命的中率（本命確率の帯別）</div>'
      +barSVG(HA.hon,'#43c59e',{xlab:'本命確率＝モデル1番手の1着確率'})+'</div>';
    h+='<div class="legend">本命確率が高い帯ほど、実際に本命（モデル1番手）が1着になった割合も高い＝高確率の本命ほど信頼できる。'
      +'棒（実測）と◇（予想）がほぼ重なる＝確率は正確。鉄板狙いは本命確率の高い帯のレースを選ぶのが目安。</div>';
    h+='<div style="text-align:center"><div style="font-size:13px;color:#e0a93b;margin:16px 0 2px;font-weight:600">② 穴的中率＋必要オッズ目安（穴確率の帯別）</div>'
      +barSVG(HA.ana,'#e0a93b',{xlab:'穴確率＝モデル4-6番手の1着確率合計',odds:true})+'</div>';
    h+='<div class="legend">穴的中＝モデルが軽視した4-6番手の艇が1着。穴確率が高い帯ほど実際に穴が出やすい。'
      +'<b style="color:#e0a93b">必要オッズ目安＝100÷穴的中率</b>＝その帯の穴を買って長期で勝つのに最低限ほしい配当倍率。'
      +'実オッズがこの倍率を超える穴だけ買うのが目安（穴確率が高い帯ほど必要倍率が下がる＝買いやすい）。'
      +'※モデルはモーター・直近を織込済みで、確率を超える妙味は無い＝勝負所はオッズが必要倍率を上回るかどうか。</div>';
  }
  return h;
}
// 実オッズ取得→EV判定（ボタン押下時のみ。serve_odds.py の /odds 経由）
function updateOdds(btn){
  const id=btn.dataset.id, stat=document.querySelector('.oddsstat');
  btn.disabled=true; stat.textContent=' 取得中…';
  fetch('odds?id='+id).then(r=>{if(!r.ok)throw 0;return r.json();}).then(o=>{
    const map=Object.assign({},o['2t']||{},o['3t']||{});
    let n=0,best=null;
    document.querySelectorAll('.crow[data-combo]').forEach(row=>{
      const cb=row.dataset.combo, p=parseFloat(row.dataset.p), od=map[cb], ev=row.querySelector('.ev');
      row.classList.remove('evplus'); if(!ev)return;
      if(od==null){ev.textContent='実 –';return;}
      const e=p*od; n++; const plus=e>=1;
      ev.innerHTML='実<b>'+od.toFixed(1)+'</b>倍 EV<b>'+e.toFixed(2)+'</b>'+(plus?' ★':'');
      if(plus)row.classList.add('evplus');
      if(best===null||e>best)best=e;
    });
    stat.innerHTML='取得 '+(o.fetched_at||'')+' ／ '+n+'点照合・最大EV '+(best!=null?best.toFixed(2):'–')+'（★=+EV）';
    btn.disabled=false;
  }).catch(()=>{stat.innerHTML='<span style="color:#e06b6b">取得失敗：serve_odds.py 経由で開く（発売前は空のことあり）</span>';btn.disabled=false;});
}
function render(){
  if(tab==='stats'){
    root.innerHTML=statsView();
    document.querySelectorAll('.tb').forEach(b=>b.onclick=()=>{tab=b.dataset.t;sel=null;render();});
    window.scrollTo(0,0);return;
  }
  root.innerHTML = sel===null ? nav()+listView() : detailView(dayRaces()[sel]);
  window.scrollTo(0,0);
  if(sel===null){
    document.querySelectorAll('.tb').forEach(b=>b.onclick=()=>{tab=b.dataset.t;sel=null;render();});
    document.querySelectorAll('.dbtn').forEach(b=>b.onclick=()=>{selDate=b.dataset.d;cur='ALL';render();});
    document.querySelectorAll('.vbtn').forEach(b=>b.onclick=()=>{cur=b.dataset.v;render();});
    document.querySelectorAll('.row').forEach(rw=>rw.onclick=()=>{sel=+rw.dataset.i;render();});
  }else{
    document.querySelector('.back').onclick=()=>{sel=null;render();};
  }
}
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
