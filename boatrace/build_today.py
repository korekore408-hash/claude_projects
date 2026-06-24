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


def _pstdev(vs):
    m = sum(vs) / len(vs)
    return (sum((v - m) ** 2 for v in vs) / len(vs)) ** 0.5


def rival_terciles(pred, since):
    """全レースの「相手（非本命5艇）の p_win ばらつき」の3分位しきい値 [t1,t2]。
    today.html で各レースの相手の差を 小/中/大 に分類するために埋め込む。
    検証(analyze_rival_spread.py, honest OOS): 本命の強さを固定しても相手の差が大きいレースほど
    2連単/3連単が当たる（主に P(2着|1着) が +7〜18pt）。"""
    sincekey = since.replace("-", "")
    byr = {}
    for (rid, _w), pr in pred.items():
        if rid[2:10] < sincekey:
            continue
        try:
            byr.setdefault(rid, []).append(float(pr.get("p_win")))
        except (TypeError, ValueError):
            pass
    sps = []
    for ps in byr.values():
        if len(ps) != 6:
            continue
        ps.sort(reverse=True)
        sps.append(_pstdev(ps[1:]))          # 非本命5艇のばらつき
    sps.sort()
    if len(sps) < 3:
        return [0.04, 0.07]
    return [round(sps[len(sps) // 3], 4), round(sps[2 * len(sps) // 3], 4)]


# 予想確率（本命確率 hon=top1 p_win）に応じた買目点数。堅い→少点 / 荒れ→多点。
# 検証(2026年): 鉄板ほど少点で回収率が高く、大混戦は点数を広げる方が良い。上限は 2連単5 / 3連単20。
def k_ex(hon):     # 2連単（上限5）
    return 2 if hon >= 0.65 else 3 if hon >= 0.50 else 4 if hon >= 0.40 else 5


def k_tri(hon):    # 3連単（上限20）
    return (4 if hon >= 0.65 else 7 if hon >= 0.50 else 10 if hon >= 0.40
            else 14 if hon >= 0.30 else 20)


def bet_exclude(cls_by_w, lane_win1, hon):
    """買い目から除外する枠 [[枠,理由], ...]（検証済ガイドライン）。
    荒れ帯(本命確率<0.45)で1号艇が成績不振(lane_win_rate<0.40)なら1号艇を除外
    / 除外後3艇未満になるなら除外しない。
    ※B2(class_ord==1)の一律除外は廃止（ユーザー要望 2026-06-24）。cls_by_w は
      シグネチャ維持のため残置（現在は未使用）。"""
    xb, excl = [], set()
    if hon < 0.45 and lane_win1 is not None and lane_win1 < 0.40:
        xb.append([1, "1号艇不振"]); excl.add(1)
    return [] if 6 - len(excl) < 3 else xb


CLASS_LABEL = {4: "A1", 3: "A2", 2: "B1", 1: "B2"}   # class_ord → 公式級別
# 各コース(枠)の平均1着率の概算。コース毎の特徴は「この平均をどれだけ上回るか」で測る
# （2〜6コースは構造的に勝率が低いので、絶対値でなく相対で評価しないと不当に低くなる）。
LANE_BASE = {1: 0.50, 2: 0.14, 3: 0.12, 4: 0.10, 5: 0.08, 6: 0.05}
# コース別の3着以内率(3連対率)の基準。全国実績の枠別平均（2026データ実測）。
LANE3_BASE = {1: 0.79, 2: 0.575, 3: 0.543, 4: 0.439, 5: 0.362, 6: 0.262}


def official_rank(cl):
    """class_ord(4/3/2/1) → 公式級別ラベル A1/A2/B1/B2。不明は空。"""
    try:
        return CLASS_LABEL.get(int(cl), "")
    except (TypeError, ValueError):
        return ""


def ai_player_rank(f):
    """AIオリジナル実力ランク S/A/B/C/D。
    『その選手がこの枠に座ったとき 3着以内に来る可能性(推定3連対率 p3)』を5段階評価。
    本人のそのコースでの3着以内率(lane_top3_rate)を核に、コース平均へ小標本補正し、
    級別・直近フォームで微調整した確率を出してランク化する。
      ① 核: 本人の枠別3着以内率 lt3 を コース平均(LANE3_BASE) へ収縮
             p = (lt3*n + base*K0) / (n + K0)  （K0=8。実績無しなら base）
      ② 級別補正: A1 +.05 / A2 +.02 / B1 ±0 / B2 -.03
      ③ 直近フォーム: 0.20*(直近30勝率 - .30) を ±.08 でクリップ
    p3 = clip(① + ② + ③, .05, .97)。
    判定 S≥.70 / A≥.57 / B≥.45 / C≥.33 / D。"""
    cl = f.get("cl")
    if cl is None:
        return ""
    w = f.get("枠")
    base = LANE3_BASE.get(w, 0.45)
    lt3, ln = f.get("lane_top3"), f.get("lane_n") or 0
    K0 = 8.0
    p = (lt3 * ln + base * K0) / (ln + K0) if (lt3 is not None and ln > 0) else base
    p += {4: 0.05, 3: 0.02, 2: 0.0, 1: -0.03}.get(int(cl), 0.0)
    rc = f.get("recent")
    if rc is not None:
        p += max(-0.08, min(0.08, 0.20 * (rc - 0.30)))
    p = max(0.05, min(0.97, p))
    return ("S" if p >= 0.70 else "A" if p >= 0.57 else "B" if p >= 0.45
            else "C" if p >= 0.33 else "D")


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


def venue_stats(rel, pred, hist, payout, since):
    """since 以降の結果がある全レースから、場別の的中率＋変動回収率を集計。
    各場: 本命1着 / 2連単(本命=top1,top3,変動K,変動回収) / 3連単(本命,top3,変動K,変動回収)。
    変動K列・回収率は当日の買い目と同じ除外（B2/不振1号艇 bet_exclude）を適用。
    回収率 = Σ配当 /(Σ点数×100)×100（変動点数を各100円で実際に買った前提・実配当payout）。"""
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
    # n,win,e1,e3,eK(2連単変動),t1,t3,tK(3連単変動),pts2,pay2,pts3,pay3
    agg = defaultdict(lambda: [0] * 12)
    name = {}
    dmin = dmax = None
    for rid, rc in races.items():
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
        # 変動点数（除外後に実際に買える点数）と実配当で回収率を集計。
        m = sum(1 for w in range(1, 7) if s[w - 1] and s[w - 1] > 0 and w not in excl)
        bet2 = min(kx, m * (m - 1))
        bet3 = min(kt, m * (m - 1) * (m - 2))
        po = payout.get(rid, (0, 0))
        a[0] += 1
        a[1] += (hm == order[0])
        a[2] += er <= 1; a[3] += er <= 3
        a[5] += tr <= 1; a[6] += tr <= 3
        if bet2 > 0:
            a[8] += bet2
            if erk <= bet2:                                   # 2連単 変動K(除外後)
                a[4] += 1; a[9] += po[0]
        if bet3 > 0:
            a[10] += bet3
            if trk <= bet3:                                   # 3連単 変動K(除外後)
                a[7] += 1; a[11] += po[1]
    pct = lambda x, n: round(x / n * 100) if n else 0
    ret = lambda pay, pts: round(pay / (pts * 100) * 100) if pts else 0

    def fmt(name_, a):
        n = a[0]
        return [name_, n, pct(a[1], n), pct(a[2], n), pct(a[3], n),
                pct(a[4], n), ret(a[9], a[8]),
                pct(a[5], n), pct(a[6], n), pct(a[7], n), ret(a[11], a[10])]
    rows = [fmt(name[c], a) for c, a in agg.items()]
    rows.sort(key=lambda r: r[2], reverse=True)
    T = [sum(agg[c][i] for c in agg) for i in range(12)]
    return {"from": dmin, "to": dmax, "n": T[0],
            "rows": rows, "all": fmt("全場", T)}


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


def load_bfile_meta(keep, venues_by_date):
    """各日の B-file(出走表 txt, UTF-8) の場ヘッダ行から (グレード, 日目) を抽出。
    返り値 {(date, 会場): (grade, day)}。日目=『第 N 日』(確実)。
    グレードは明示マーカー(SG/G1/G2/G3)がある時のみ判定、無印は'一般'。
    会場名はヘッダ行(NFKC・空白除去)の『ボートレース』直後の先頭一致で突き合わせ。"""
    import unicodedata
    meta = {}
    for d in keep:
        path = f"data/b{d[2:4]}{d[5:7]}{d[8:10]}.txt"
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        vset = venues_by_date.get(d, set())
        for ln in lines:
            if "ボートレース" not in ln:
                continue
            mday = re.search(r"第\s*([0-9０-９]+)\s*日", ln)
            if not mday:
                continue
            day = int(unicodedata.normalize("NFKC", mday.group(1)))
            z = unicodedata.normalize("NFKC", ln)
            grade = ("SG" if "SG" in z else
                     "G3" if ("G3" in z or "GIII" in z) else
                     "G2" if ("G2" in z or "GII" in z) else
                     "G1" if ("G1" in z or "GI" in z) else "一般")
            head = z.replace(" ", "").split("ボートレース", 1)[-1]
            for v in vset:
                if head.startswith(v):
                    meta[(d, v)] = (grade, day)
                    break
    return meta


def regime_result(rel, pred, hist, payout, since):
    """予想の荒れ度（鉄板/標準/穴）ごとに、実際の的中率と回収率を集計。
    荒れ度＝本命確率(top1 p_win): ≥0.65 鉄板 / 0.45-0.65 標準 / <0.45 穴(波乱含み)。
    買い目＝当日と同じ変動点数（2連単 k_ex≤5 / 3連単 k_tri≤20）＋除外（B2・不振1号艇）。
    回収率 = Σ配当 /(Σ点数×100)×100。各100円・実配当(K-file)。
    返り値 [{lab,n,h2,r2,h3,r3}, ...]（鉄板/標準/穴）。"""
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
        rc = races.setdefault(rid, {"b": {}, "cl": {}, "lw": {}})
        w = int(r["枠番"])
        rc["b"][w] = (pm, fin)
        rc["cl"][w] = to_float(r.get("class_ord"))
        rc["lw"][w] = to_float(h.get("lane_win_rate"))

    labs = ["鉄板", "標準", "穴"]
    agg = [{"n": 0, "win": 0, "n2": 0, "h2": 0, "pts2": 0, "p2": 0,
            "n3": 0, "h3": 0, "pts3": 0, "p3": 0} for _ in range(3)]
    for rid, rc in races.items():
        if len(rc["b"]) != 6:
            continue
        s = [rc["b"][w][0] for w in range(1, 7)]
        fins = [rc["b"][w][1] for w in range(1, 7)]
        if any(x is None for x in s):
            continue
        order = sorted([w for w in range(1, 7)
                        if fins[w - 1] is not None and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < 2 or fins[order[0] - 1] != 1:
            continue
        hon = max(s)
        gi = 0 if hon >= 0.65 else (2 if hon < 0.45 else 1)
        kx, kt = k_ex(hon), k_tri(hon)
        excl = {e[0] for e in bet_exclude(rc["cl"], rc["lw"].get(1), hon)}
        m = sum(1 for w in range(1, 7) if s[w - 1] and s[w - 1] > 0 and w not in excl)
        bet2 = min(kx, m * (m - 1))
        bet3 = min(kt, m * (m - 1) * (m - 2))
        po = payout.get(rid, (0, 0))
        hm = max(range(6), key=lambda i: s[i]) + 1     # モデル1番手（本命）の枠
        a = agg[gi]
        a["n"] += 1
        a["win"] += (hm == order[0])                   # 本命艇が1着
        if bet2 > 0:
            a["n2"] += 1
            a["pts2"] += bet2
            if _pl_rank(s, 2, tuple(order[:2]), excl) <= bet2:
                a["h2"] += 1
                a["p2"] += po[0]
        if len(order) >= 3 and bet3 > 0:
            a["n3"] += 1
            a["pts3"] += bet3
            if _pl_rank(s, 3, tuple(order[:3]), excl) <= bet3:
                a["h3"] += 1
                a["p3"] += po[1]

    pct = lambda x, n: round(x / n * 100, 1) if n else 0
    return [{"lab": labs[i], "n": a["n"], "win": pct(a["win"], a["n"]),
             "h2": pct(a["h2"], a["n2"]), "r2": pct(a["p2"], a["pts2"] * 100),
             "h3": pct(a["h3"], a["n3"]), "r3": pct(a["p3"], a["pts3"] * 100)}
            for i, a in enumerate(agg)]


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
            "lane_top3": to_float(h.get("lane_top3_rate")),
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
                           for w in range(1, 7)],
                    # 枠ごと [公式級別 A1/A2/B1/B2, AIオリジナル実力ランク S/A/B/C/D]
                    "rk": [[official_rank(rc["feat"][w].get("cl")),
                            ai_player_rank(rc["feat"][w])]
                           for w in range(1, 7)]})
    out.sort(key=lambda x: (x["d"], x["c"], x["no"]))

    # 会場×日付ごとの グレード/日目（B-file 出走表ヘッダから）。
    vbd = {}
    for o in out:
        vbd.setdefault(o["d"], set()).add(o["v"])
    bmeta = load_bfile_meta(keep, vbd)
    # 当日のグレードは公式インデックスで上書き（B-fileは無印SG/G1を取りこぼすため）。
    official_grade = {}
    try:
        import fetch_grade
        official_grade = fetch_grade.fetch_grades(base.replace("-", ""))
    except Exception as e:
        print(f"  グレード公式取得スキップ: {e}")
    for o in out:
        g, day = bmeta.get((o["d"], o["v"]), ("一般", None))
        if o["d"] == base and o["c"] in official_grade:   # 当日のみ公式で上書き
            g = official_grade[o["c"]]
        o["g"] = g
        o["day"] = day

    # 日付ラベル（当日/前日/前々日）
    rel_labels = ["当日", "前日", "前々日", "3日前", "4日前", "5日前", "6日前"]
    labels = []
    for i, d in enumerate(reversed(keep)):       # 新しい順
        labels.append([rel_labels[i] if i < len(rel_labels) else d, d])

    payout_all = load_payouts(sorted({r["日付"] for r in rel
                                      if r["日付"] >= args.stats_from}))
    vstats = venue_stats(rel, pred, hist, payout_all, args.stats_from)
    recent = recent_stats(out, payout)
    regime = regime_result(rel, pred, hist, payout_all, args.stats_from)

    rsp = rival_terciles(pred, args.stats_from)
    payload = {"labels": labels, "base": base, "races": out,
               "vstats": vstats, "recent": recent, "regime": regime, "rsp": rsp}
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
  .sortbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:6px 0 2px;font-size:12px;color:#8ea0ba}
  .sortb{font-size:12px;padding:4px 11px;border-radius:7px;border:0.5px solid #39404d;background:transparent;color:#cdd6e2;cursor:pointer}
  .sortb.on{background:#374151;color:#fff;border-color:#4b5563}
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
  .rk{display:inline-block;padding:0 5px;border-radius:4px;background:#2a3140;color:#aeb8c6;font-size:11px;font-weight:700;line-height:17px}
  .airk{display:inline-block;min-width:17px;height:17px;line-height:17px;text-align:center;border-radius:4px;font-size:11px;font-weight:800;padding:0 2px}
  .aiS{background:#e0b000;color:#1a1a1a}.aiA{background:#e2552e;color:#fff}.aiB{background:#2f6fe0;color:#fff}
  .aiC{background:#3f4756;color:#dfe6ef}.aiD{background:#222934;color:#8a93a2}
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
  table.st{border-collapse:collapse;width:100%;font-size:11px}
  table.st th,table.st td{border-bottom:0.5px solid #232a36;padding:5px 3px;text-align:right;white-space:nowrap}
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
  .lvl.rdhi{background:#13294a;color:#7db1ff}
  .lvl.rdmid{background:#2a2f3a;color:#9aa3b2}
  .lvl.rdlo{background:#26262a;color:#7e8796}
  .prize{font-size:11px;font-weight:800;border-radius:8px;padding:2px 8px;display:inline-block;
    background:#3a2a4a;color:#d6a8ff;margin-left:4px}
  .chance{font-size:11px;font-weight:800;border-radius:8px;padding:2px 8px;display:inline-block;
    background:#3a2f15;color:#f0c869;margin-left:4px}
  .vmeta{margin-left:8px;font-size:11px;font-weight:400;color:#9aa3b2}
  .vmeta .grd{border-radius:6px;padding:1px 6px;background:#2a2f3a;color:#9aa3b2;margin-right:5px}
  .vmeta .grd.hi{background:#3a2a14;color:#f0b649;font-weight:700}
  .rdbox{margin:8px 0;padding:8px 10px;border-radius:10px;background:#161a22;border:0.5px solid #2a2f3a;
    font-size:12px;color:#cdd6e2;line-height:1.5}
  .rdbox .h{font-weight:700;color:#9aa3b2;margin-right:6px}
  .rdbox.prizebox{background:#241a2e;border-color:#5a3f6a;color:#e6cfff}
  .row.done{flex-wrap:wrap;row-gap:5px}
  .resline{flex-basis:100%;display:flex;align-items:center;gap:4px;padding-left:38px;font-size:12px;color:#9aa3b2}
  .resline .rll{color:#6b7280;font-size:11px;margin-right:2px}
  .resline .yen{margin-left:auto;font-variant-numeric:tabular-nums;color:#cdd6e2;font-weight:600}
  .resline .yen.hit{color:#43c59e}
  .upbtn{font-size:14px;padding:8px 6px;border-radius:8px;border:0.5px solid #2f7a52;
        background:transparent;color:#7ee0a4;cursor:pointer;text-align:center;flex:1}
  .upbtn:active{background:#10362c}
  .upbtn:disabled{opacity:.5}
  .upstat{font-size:11px;color:#9aa3b2;margin:0 0 8px;line-height:1.5;min-height:0}
  .upstat.err{color:#e06b6b}
  .exwx{font-size:12px;color:#cdd6e2;background:#141a1f;border-left:3px solid #5dc7e0;border-radius:0 6px 6px 0;padding:7px 10px;margin:6px 0;line-height:1.6}
  table.ex{border-collapse:collapse;width:100%;font-size:12px;margin:6px 0 2px}
  table.ex th,table.ex td{border-bottom:0.5px solid #232a36;padding:5px 4px;text-align:center;white-space:nowrap}
  table.ex th{color:#8ea0ba;font-weight:600;font-size:11px}
  table.ex td.parts{text-align:left;font-size:10px;color:#9aa3b2;white-space:normal}
  table.ex .extbest{color:#43c59e;font-weight:800}
  table.ex .exin{color:#e0a93b;font-weight:700}
</style></head><body>
<div id="app"></div>
<script>
const D=__DATA__;
const LC={1:['#ffffff','#111111'],2:['#1b1b1b','#ffffff'],3:['#e23b3b','#ffffff'],4:['#2f7fd6','#ffffff'],5:['#f2c025','#111111'],6:['#28a35a','#ffffff']};
let selDate=D.labels[0][1], cur='ALL', sel=null, tab='pred';
// 「更新」ボタンの状態（当日の展示+結果取得）。upMsg=ステータス表示文字列。
let upMsg='', upErr=false, upBusy=false;
// 場別テーブルの並び替え状態（c='e'(2連単)/'t'(3連単), d=1昇順/-1降順, null=既定）。
let vsort={c:null,d:-1}, rsort={c:null,d:-1};
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

// 相手（非本命5艇）の p_win ばらつき＝2・3着の絞りやすさ。s=per-mille p_win 配列。
// 検証(OOS 7,862R): 本命の強さを固定しても「差あり」レースは2連単/3連単が高い
// （主に P(2着|1着) が +7〜18pt）。勝負どころ＝本命が強い × 相手にも差がある。
function rivalDiff(r){
  const s=r.b.map(x=>x[1]).slice().sort((a,b)=>b-a);   // per-mille 降順
  const riv=s.slice(1);                                 // 非本命5艇
  const m=riv.reduce((a,b)=>a+b,0)/riv.length;
  const sp=Math.sqrt(riv.reduce((a,b)=>a+(b-m)*(b-m),0)/riv.length)/1000;  // 0-1
  const t=D.rsp||[0.04,0.07];
  const lv = sp>=t[1]?['大','rdhi'] : sp<t[0]?['小','rdlo'] : ['中','rdmid'];
  const hon=s[0]/1000;
  const prize = hon>=0.50 && sp>=t[1];                  // 本命が強い×相手に差＝勝負どころ
  return {sp,lvl:lv[0],cls:lv[1],prize,hon};
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
    if(rs.findIndex(x=>x.c===r.c)===gi)h+='<h3>'+r.v+'<span class="vmeta"><span class="grd'+(r.g&&r.g!=='一般'?' hi':'')+'">'+(r.g||'一般')+'</span>'+(r.day?'第'+r.day+'日':'')+'</span></h3>';
    let hm=0;for(let w=1;w<6;w++)if(r.b[w][1]>r.b[hm][1])hm=w;
    const ha=honAna(r);const done=hasResult(r);
    h+='<div class="row'+(done?' done':'')+'" data-i="'+gi+'"><span class="rno">'+r.no+'R</span>'+chip(hm+1)
     +'<span class="nm">'+r.b[hm][0]+'</span>'+(r.mz?'<span class="wn">&#9888;</span>':'');
    const chance = ha.hon<0.35 && ha.ana>=0.25;            // 本命弱い×穴厚い＝コメントチャンス
    const shobu = ha.hon>=0.65 && r.ev!=null && r.ev>=1.5;  // 鉄板×EV≥1.5（オッズ取得後に点灯）
    h+='<span class="ha2"><span class="lvl '+ha.lvlcls+'">'+ha.lvl+'</span>'
        +(shobu?'<span class="prize">&#127919;勝負</span>':'')
        +(chance?'<span class="chance">&#10024;チャンス</span>':'')
      +'<span class="hp">本命<b>'+Math.round(ha.hon*100)+'</b> 穴<b class="a">'+Math.round(ha.ana*100)+'</b></span></span>';
    if(done){
      const s=r.b.map(x=>x[1]);const ord=finishOrder(r);
      const hon=Math.max(...s)/1000;const nEx=kEx(hon),nTri=kTri(hon);
      const xset={};(r.xb||[]).forEach(e=>xset[e[0]]=1);
      const ex=ord.slice(0,2);const tri=ord.slice(0,3);
      const exHit=ex.length>=2&&plTop(s,2,nEx,xset).some(c=>eqArr(c[0],ex));
      const triHit=tri.length>=3&&plTop(s,3,nTri,xset).some(c=>eqArr(c[0],tri));
      const hit=exHit||triHit;                           // 2連単/3連単の変動上位に決着が入れば的中
      const pay=r.po?r.po[1]:null;const pay2=r.po?r.po[0]:null;
      h+='<span class="res">'+(hit?'<span class="ok">的中</span>':'<span class="ng">不的中</span>')+'</span>'
        +'<span class="chev">&rsaquo;</span>';
      h+='<div class="resline"><span class="rll">2連単</span>';
      ex.forEach((w,i)=>{h+=(i?'<span class="arr">&rarr;</span>':'')+chip(w,'mc');});
      if(exHit)h+='<span class="ok" style="font-size:11px">的中</span>';
      h+='<span class="yen'+(exHit?' hit':'')+'">'+(pay2!=null?'¥'+pay2.toLocaleString():'配当 –')+'</span></div>';
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
  // 展示(r.ex)取得済みなら気象は exView で実値表示するのでこの行は出さない。
  if(r.wx&&!r.ex){
    const tk=r.wx[0], wd=r.wx[1], wv=r.wx[2];
    if(tk||wd!=null||wv!=null){
      h+='<div class="meta">🌤 '+(tk||'–')+(wd!=null?' ・ 風'+wd+'m':'')+(wv!=null?' ・ 波'+wv+'cm':'')+'<span style="color:#6b7280"> （荒れ度・気象を予想に反映）</span></div>';
    }else{
      h+='<div class="meta" style="color:#6b7280">🌤 天候は当日予想に未反映（場の荒れ度のみ反映・気象は後日反映予定）</div>';
    }
  }
  h+=exView(r);   // 直前情報（展示）。更新で取得済みのときだけ表示。
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
     +(r.rk&&r.rk[w]?'<span class="rk" title="公式級別">'+(r.rk[w][0]||'–')+'</span>'
        +(r.rk[w][1]?'<span class="airk ai'+r.rk[w][1]+'" title="AI 3着以内ランク（この枠で3着以内に来る可能性：枠別3連対率×級別×直近）">'+r.rk[w][1]+'</span>':''):'')
     +'<div class="barw"><div class="bar" style="width:'+Math.max(b[1]/mx*100,2)+'%;background:'+a[0]+'"></div></div>'
     +'<span class="bp">'+(b[1]/10).toFixed(1)+'%</span></div>';});
  h+='<div style="font-size:11px;color:#7e8796;margin:2px 0 0">'
    +'<span class="rk">A1</span> 公式級別　｜　'
    +'<span class="airk aiS">S</span><span class="airk aiA">A</span><span class="airk aiB">B</span>'
    +'<span class="airk aiC">C</span><span class="airk aiD">D</span> '
    +'AI 3着以内ランク＝この枠で3着以内に来る可能性（枠別3連対率×級別×直近フォーム）の5段階評価</div>';
  // 買い目除外（荒れ帯の不振1号艇）。確率は全艇ベース＝必要倍は不変。
  const xset={};(r.xb||[]).forEach(e=>xset[e[0]]=1);
  if(r.xb&&r.xb.length){
    h+='<div class="cause" style="border-left-color:#7f8896;color:#aab2bf"><span class="h" style="color:#9aa3b2">買い目から除外</span>'
      +r.xb.map(e=>chip(e[0],'mc')+' '+r.b[e[0]-1][0]+'（'+(e[1]==='B2'?'B2級':'1号艇 成績不振')+'）').join(' ')
      +'<br><span style="font-size:11px;color:#7e8796">※検証で「回収率を保ったまま賭け金を絞れる」と確認した枠を2連単/3連単の買い目から除外（確率・本命/穴の表示はそのまま）。</span></div>';
  }
  // 相手（非本命5艇）の差＝2・3着の絞りやすさ。勝負どころ＝本命が強い×相手に差。
  const rd=rivalDiff(r);
  h+='<div class="rdbox'+(rd.prize?' prizebox':'')+'">'
    +'<span class="h">相手の差</span><span class="lvl '+rd.cls+'">'+rd.lvl+'</span> '
    +(rd.prize
       ? '&#127919; <b>勝負どころ</b>：本命が強く、相手（2・3着）にも差がある。検証では同じ本命確率でも2連単/3連単の的中が一段高い帯（P(2着&#124;1着)が+7〜18pt）。点数を絞って2連単/3連単に厚く張る価値。'
       : rd.lvl==='大'
         ? '相手（2・3着）は絞りやすいが、本命確率が低め。1着が読みづらいので頭は広めに。'
         : '相手が横一線で2・3着を絞りにくい。本命が強くても2連単/3連単は伸びにくい帯＝点数を欲張らない。')
    +'<br><span style="font-size:11px;color:#7e8796">※「相手の差」＝非本命5艇の1着確率のばらつき（発走前にモデルから分かる量）。1着の精度は本命確率が決め、相手の差はもっぱら2・3着を絞れるかに効く（検証 OOS 7,862R）。</span></div>';
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
    +'<button class="tb'+(tab==='stats'?' on':'')+'" data-t="stats">場別成績</button>'
    +'<button class="upbtn"'+(upBusy?' disabled':'')+' data-act="update">更新</button></div>'
    +'<div class="upstat'+(upErr?' err':'')+'" id="upstat">'+(upMsg||'')+'</div>';
}
// 棒グラフ（自己完結SVG）。data=[[帯ラベル, 予想中央%, 実測%, レース数], ...]。
// 棒=実測、◇=予想中央。opts.odds で各帯に必要オッズ目安(=100/実測%)を表示。
// 荒れ度3区分（鉄板/標準/穴）のグループ棒グラフ。series=[{k,col},...]（2連単/3連単）。
function grpBars(rows,series,opts){
  opts=opts||{};
  if(!rows||!rows.length)return '';
  const W=330,padL=30,padR=8,padT=16,padB=32,H=padT+150+padB;
  let mx=Math.max(...rows.flatMap(r=>series.map(s=>r[s.k]||0)),opts.ref||0,1);
  const step=mx>100?40:mx>50?20:mx>20?10:5;
  const M=Math.ceil(mx/step)*step;
  const plotW=W-padL-padR, plotH=H-padT-padB;
  const n=rows.length, gap=plotW/n;
  const y=v=>padT+plotH-(v/M)*plotH;
  let g='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;max-width:340px">';
  for(let t=0;t<=M+0.01;t+=M/5){
    g+='<line x1="'+padL+'" y1="'+y(t)+'" x2="'+(W-padR)+'" y2="'+y(t)+'" stroke="#222a33"/>';
    g+='<text x="'+(padL-4)+'" y="'+(y(t)+3)+'" fill="#7e8796" font-size="9" text-anchor="end">'+Math.round(t)+'</text>';
  }
  if(opts.ref){
    g+='<line x1="'+padL+'" y1="'+y(opts.ref)+'" x2="'+(W-padR)+'" y2="'+y(opts.ref)+'" stroke="#c0563c" stroke-dasharray="4 3" stroke-width="1.2"/>';
    g+='<text x="'+(W-padR)+'" y="'+(y(opts.ref)-3)+'" fill="#d9745c" font-size="8.5" text-anchor="end">'+opts.ref+'%</text>';
  }
  const bw=Math.min((gap*0.62)/series.length,26);
  rows.forEach((r,i)=>{
    const c0=padL+gap*i+gap/2, tot=series.length*bw+(series.length-1)*3;
    series.forEach((s,j)=>{
      const x=c0-tot/2+j*(bw+3), v=r[s.k]||0, yt=y(v);
      g+='<rect x="'+x+'" y="'+yt+'" width="'+bw+'" height="'+Math.max((v/M)*plotH,1)+'" rx="2" fill="'+s.col+'"/>';
      g+='<text x="'+(x+bw/2)+'" y="'+(yt-3)+'" fill="#e6e6e6" font-size="9" text-anchor="middle" font-weight="700">'+v+'</text>';
    });
    g+='<text x="'+c0+'" y="'+(H-padB+15)+'" fill="#cdd6e2" font-size="11" text-anchor="middle" font-weight="600">'+r.lab+'</text>';
    g+='<text x="'+c0+'" y="'+(H-padB+26)+'" fill="#6b7280" font-size="8" text-anchor="middle">'+r.n+'R</text>';
  });
  g+='</svg>';
  return g;
}
// 場別テーブルを指定列(idx)・方向(dir)でソート（コピーを返す。全場行は呼び出し側で末尾固定）。
function sortRows(rows,idx,dir){return rows.slice().sort((a,b)=>(a[idx]-b[idx])*dir);}
// 並び替えボタン列。tbl='v'(的中率表)/'r'(回収率表), st=その状態。
function sortbar(tbl,st){
  const mk=(c,lab)=>{const on=st.c===c;const ar=on?(st.d>0?' ▲':' ▼'):' ⇅';
    return '<button class="sortb'+(on?' on':'')+'" data-st="'+tbl+'" data-col="'+c+'">'+lab+ar+'</button>';};
  return '<div class="sortbar">並び替え:'+mk('e','2連単')+mk('t','3連単')+'</div>';
}
function statsView(){
  // 場別成績は Python 側で全期間（2026年〜）集計済み。ここでは描画のみ。
  const V=D.vstats; let h=nav();
  if(!V||!V.n){return h+'<div class="meta">結果データがまだありません。</div>';}
  const cell=(v,extra)=>'<td class="num'+(extra?' '+extra:'')+'">'+v+'%</td>';
  const row=(a,all)=>'<tr'+(all?' class="all"':'')+'><td class="k">'+a[0]+'</td>'
    +'<td class="num scol">'+a[1]+'</td><td class="num">'+a[2]+'%</td>'
    +cell(a[3],all?'':'g2')+cell(a[5],all?'':'g2')+cell(a[6],all?'':'g2')
    +cell(a[7],all?'':'g3')+cell(a[9],all?'':'g3')+cell(a[10],all?'':'g3')+'</tr>';
  h+='<div class="meta">対象 '+V.from+'〜'+V.to+'（'+V.n+'レース・収集データ全体）・ '
    +'数字=予想上位K通り以内に決着が入った割合。「変動」=予想確率連動の点数（堅い→少点／荒れ→多点）。'
    +'並び替えは「変動」的中率（2連単≤5／3連単≤20）基準。</div>';
  h+=sortbar('v',vsort);
  const vrows=vsort.c?sortRows(V.rows,vsort.c==='e'?5:9,vsort.d):V.rows;
  h+='<div class="swrap"><table class="st"><thead><tr>'
    +'<th class="k">会場</th><th>R数</th><th>本命<br>1着</th>'
    +'<th class="g2">2連単<br>本命</th><th class="g2">変動<br>≤5</th><th class="g2">変動<br>回収</th>'
    +'<th class="g3">3連単<br>本命</th><th class="g3">変動<br>≤20</th><th class="g3">変動<br>回収</th></tr></thead><tbody>';
  for(const a of vrows)h+=row(a,false);
  h+=row(V.all,true);
  h+='</tbody></table></div>';
  h+='<div class="legend">※ 収集データ全体（'+V.from+'〜'+V.to+'）の結果から集計。本命=1着確率最大の枠。'
    +'「変動」=予想確率に応じた点数（2連単≤5/3連単≤20）以内に実際の決着が含まれた割合（その点数を買えば当たる割合）。'
    +'<b>「変動回収」=その変動点数を各100円で実際に買った場合の回収率（Σ配当÷賭け金）。100%超で利益。</b>'
    +'※「変動」「変動回収」列は荒れ帯の不振1号艇を除外（本命列はモデル診断＝除外なし）。'
    +'※ 4月までは学習期間を含むため的中率・回収率はやや高めに出る（5月以降が純粋な検証）。'
    +'※ 全期間でも回収率は100%未満が基本（控除率約25%の壁）。</div>';
  // 前日・前々日の場別 的中率＋回収率
  const RC=D.recent;
  if(RC&&RC.rows&&RC.rows.length){
    const rrow=(a,all)=>'<tr'+(all?' class="all"':'')+'><td class="k">'+a[0]+'</td>'
      +'<td class="num scol">'+a[1]+'</td>'
      +'<td class="num g2">'+a[2]+'%</td><td class="num g2">'+a[3]+'%</td>'
      +'<td class="num g3">'+a[4]+'%</td><td class="num g3">'+a[5]+'%</td></tr>';
    h+='<div class="sec" style="margin-top:22px;color:#cdd6e2;font-size:14px">前日・前々日の的中率・回収率（'+RC.from.slice(5)+'〜'+RC.to.slice(5)+'）</div>';
    h+='<div class="meta">実践的中＝予想確率連動の点数を実際に買った場合の的中率（2連単≤5/3連単≤20点, 各100円）。回収率100%超で利益。'
      +'荒れ帯の不振1号艇を買い目から除外したベース（点数=除外後の実点数）。並び替えは回収率基準。</div>';
    h+=sortbar('r',rsort);
    const rrows=rsort.c?sortRows(RC.rows,rsort.c==='e'?3:5,rsort.d):RC.rows;
    h+='<div class="swrap"><table class="st"><thead><tr>'
      +'<th class="k">会場</th><th>R数</th>'
      +'<th class="g2">2連単<br>実践的中</th><th class="g2">回収率</th>'
      +'<th class="g3">3連単<br>実践的中</th><th class="g3">回収率</th></tr></thead><tbody>';
    for(const a of rrows)h+=rrow(a,false);
    h+=rrow(RC.all,true);
    h+='</tbody></table></div>';
    h+='<div class="legend">※ 直近2日のみ＝サンプル小。回収率は高配当1本で大きく振れる（特に3連単）。'
      +'確率帯別バックテスト(2026全体)ではどの帯も回収率100%未満（控除率約25%の壁）＝確率だけで機械的に買うと負ける。'
      +'各レース詳細の「買えてた場合の妙味」で、実配当が必要オッズを超えたか（＝買えてたら+EVか）を確認できる。</div>';
  }
  // 鉄板・標準・穴 と予想した場合の 的中率／回収率（2026年〜）
  const RG=D.regime;
  if(RG&&RG.length&&RG.some(r=>r.n)){
    h+='<div class="sec" style="margin-top:22px;color:#cdd6e2;font-size:14px">鉄板・標準・穴 別の的中率と回収率（2026年〜）</div>';
    h+='<div class="meta">予想の荒れ度で3分類：<b>鉄板</b>＝本命確率≥65％ ／ <b>標準</b>＝45–65％ ／ <b>穴</b>＝本命確率&lt;45％（波乱含み）。'
      +'買い目＝確率連動の変動点数（2連単≤5／3連単≤20点・各100円, 荒れ帯の不振1号艇は除外）。</div>';
    h+='<div class="meta" style="text-align:center"><span style="color:#5b9bd5">■</span> 本命1着　'
      +'<span style="color:#43c59e">■</span> 2連単（≤5点）　'
      +'<span style="color:#e0a93b">■</span> 3連単（≤20点）</div>';
    h+='<div style="text-align:center"><div style="font-size:13px;color:#cdd6e2;margin:8px 0 2px;font-weight:600">① 的中率（％）</div>'
      +grpBars(RG,[{k:'win',col:'#5b9bd5'},{k:'h2',col:'#43c59e'},{k:'h3',col:'#e0a93b'}])+'</div>';
    h+='<div style="text-align:center"><div style="font-size:13px;color:#cdd6e2;margin:14px 0 2px;font-weight:600">② 回収率（％・<span style="color:#d9745c">赤破線=100％損益分岐</span>）</div>'
      +grpBars(RG,[{k:'r2',col:'#43c59e'},{k:'r3',col:'#e0a93b'}],{ref:100})+'</div>';
    h+='<div class="legend"><b style="color:#5b9bd5">本命1着</b>＝本命（モデル1番手）が1着に来た割合（着順1つだけ）。鉄板ほど高い。'
      +'2連単（1着+2着）・3連単（1-2-3着）は着順まで当てるぶん下がる。'
      +'回収率は払戻÷賭け金で、本命1着は単勝オッズが無いため対象外＝2連単・3連単のみ。'
      +'いずれの分類も回収率は100％（赤破線）未満が基本＝控除率約25％の壁で、確率だけで機械的に買うと長期では負ける。'
      +'「変動点数」は堅い予想ほど少点／荒れ予想ほど多点に自動調整。※4月までは学習期間を含むため的中・回収はやや高め。</div>';
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
// 風向番号(1..16)→簡易矢印（公式の風向アイコン番号に対応・北基準時計回り）。
const WINDDIR={1:'↑',2:'↗',3:'↗',4:'→',5:'→',6:'↘',7:'↘',8:'↓',9:'↓',10:'↙',11:'↙',12:'←',13:'←',14:'↖',15:'↖',16:'↑'};

// 展示（直前情報）セクション。r.ex（更新で取得）を表で表示。
function exView(r){
  const e=r.ex; if(!e)return '';
  let h='<div class="sec">直前情報（展示）<span class="kbadge">取得済</span></div>';
  const w=e.weather||{};
  if(w.tenki||w.wind!=null||w.wave!=null){
    h+='<div class="exwx">🌤 '+(w.tenki||'–')
      +(w.wind!=null?' ・ 風'+w.wind+'m'+(w.winddir?(WINDDIR[w.winddir]||''):''):'')
      +(w.wave!=null?' ・ 波'+w.wave+'cm':'')
      +(w.temp!=null?' ・ 気温'+w.temp+'℃':'')+'</div>';
  }
  const ts=e.time||[];
  const valid=ts.map((t,i)=>[t,i]).filter(x=>x[0]!=null).sort((a,b)=>a[0]-b[0]);
  const best=valid.length?valid[0][1]:-1;       // 展示タイム最速の枠index
  const fmtSt=st=>st==null?'–':((st<0?'F':'')+'.'+Math.abs(Math.round(st*100)).toString().padStart(2,'0'));
  h+='<table class="ex"><thead><tr><th>枠</th><th>展示T</th><th>チルト</th><th>進入</th><th>展示ST</th><th>部品交換</th></tr></thead><tbody>';
  for(let i=0;i<6;i++){
    const t=ts[i], tl=e.tilt?e.tilt[i]:null, c=e.course?e.course[i]:null, st=e.st?e.st[i]:null, pa=e.parts?e.parts[i]:null;
    h+='<tr><td>'+chip(i+1,'mc')+'</td>'
      +'<td class="'+(i===best?'extbest':'')+'">'+(t!=null?t.toFixed(2):'–')+'</td>'
      +'<td>'+(tl!=null?((tl>0?'+':'')+tl.toFixed(1)):'–')+'</td>'
      +'<td class="'+(c===1?'exin':'')+'">'+(c!=null?c+'c':'–')+'</td>'
      +'<td>'+fmtSt(st)+'</td>'
      +'<td class="parts">'+(pa&&pa.length?pa.join('・'):'–')+'</td></tr>';
  }
  h+='</tbody></table>';
  h+='<div class="legend">※ 展示＝発走直前の情報（「更新」時に公式サイトから取得）。'
    +'<b style="color:#43c59e">緑</b>＝展示タイム最速、<b style="color:#e0a93b">橙</b>＝展示でイン(1c)進入。'
    +'モデルの確率自体は朝の出走表のみで算出（展示は判断材料として表示）。</div>';
  return h;
}

// 「更新」ボタン：当日(base)の展示＋結果を serve_odds.py 経由で取得し D.races へ反映。
// 結果が出たレースは着順/決まり手/配当を入れて前日同様の結果表示に切替わる。
function updateAll(){
  if(upBusy)return;
  // ファイル直開き(file://)では更新サーバに繋げない。サーバ版ページへ移動する
  // （サーバ未起動なら接続不可＝ランチャー/常駐サーバで起動が必要）。
  if(location.protocol==='file:'){location.href='http://localhost:8787/today.html#update';return;}
  upBusy=true; upErr=false;
  upMsg='取得中… 当日の展示・結果を収集しています（開催場数により数十秒かかることがあります）';
  selDate=D.base; tab='pred'; sel=null; render();
  fetch('update?date='+D.base.replace(/-/g,'')+'&odds=1').then(r=>{if(!r.ok)throw 0;return r.json();}).then(o=>{
    const R=o.races||{}; let nres=0,nex=0,nev=0;
    D.races.forEach(r=>{
      if(r.d!==D.base)return;                    // 反映は当日のみ
      const rec=R[r.id]; if(!rec)return;
      if(rec.ex){r.ex=rec.ex; nex++;}
      if(rec.ev!=null){r.ev=rec.ev; nev++;}      // 鉄板×EV≥1.5 で 🎯勝負 点灯
      if(rec.result){
        const rs=rec.result, fin=rs.fin||[];
        for(let w=1;w<=6;w++)r.b[w-1][2]=fin[w-1];   // 着順→前日同様の結果表示
        if(rs.km)r.km=rs.km;
        if(rs.po2!=null||rs.po3!=null)r.po=[rs.po2,rs.po3];
        nres++;
      }
    });
    upBusy=false; upErr=false;
    upMsg='更新 '+(o.fetched_at||'')+' ／ 結果 '+nres+'レース・展示 '+nex+'レース'+(nev?'・EV '+nev+'レース':'')+'を反映';
    render();
  }).catch(()=>{
    upBusy=false; upErr=true;
    upMsg='取得失敗：serve_odds.py 経由で開いてください（py -3.13 serve_odds.py → http://localhost:8787/today.html）。発走前は展示・結果がまだ無いことがあります。';
    render();
  });
}

function render(){
  if(tab==='stats'){
    root.innerHTML=statsView();
    document.querySelectorAll('.tb').forEach(b=>b.onclick=()=>{tab=b.dataset.t;sel=null;render();});
    document.querySelectorAll('.upbtn').forEach(b=>b.onclick=updateAll);
    document.querySelectorAll('.sortb').forEach(b=>b.onclick=()=>{
      const s=b.dataset.st==='v'?vsort:rsort, c=b.dataset.col;
      if(s.c===c){s.d=-s.d;}else{s.c=c;s.d=-1;}   // 同じ列=方向反転／別列=降順から
      render();
    });
    return;
  }
  root.innerHTML = sel===null ? nav()+listView() : detailView(dayRaces()[sel]);
  window.scrollTo(0,0);
  if(sel===null){
    document.querySelectorAll('.tb').forEach(b=>b.onclick=()=>{tab=b.dataset.t;sel=null;render();});
    document.querySelectorAll('.upbtn').forEach(b=>b.onclick=updateAll);
    document.querySelectorAll('.dbtn').forEach(b=>b.onclick=()=>{selDate=b.dataset.d;cur='ALL';render();});
    document.querySelectorAll('.vbtn').forEach(b=>b.onclick=()=>{cur=b.dataset.v;render();});
    document.querySelectorAll('.row').forEach(rw=>rw.onclick=()=>{sel=+rw.dataset.i;render();});
  }else{
    document.querySelector('.back').onclick=()=>{sel=null;render();};
  }
}
// file:// で直接開かれ、かつ更新サーバが起動中ならサーバ版へ自動で切替える
// （更新ボタンは serve_odds.py 経由でないと動かないため）。サーバ停止中は
// no-cors fetch が失敗→何もしない＝そのままオフライン閲覧できる。
if(location.protocol==='file:'){
  fetch('http://localhost:8787/today.html',{mode:'no-cors',cache:'no-store'})
    .then(()=>{location.replace('http://localhost:8787/today.html');})
    .catch(()=>{});
}
render();
// サーバ版へ #update 付きで来たら、自動で更新を1回実行（file://から更新を押した導線）。
if(location.protocol!=='file:'&&location.hash==='#update'){
  history.replaceState(null,'',location.pathname);   // 二重実行防止
  updateAll();
}

</script></body></html>"""


if __name__ == "__main__":
    main()
