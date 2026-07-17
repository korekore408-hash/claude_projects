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
# ★2026-07-06 鉄板の3連単 4→3点: 点別回収が1-3点目83.9/85.9/92.7%に対し4点目69.7%と構造的な崖。
#   カットで鉄板3連単回収85.7→88.9%・総回収85.8→87.4%(backtest 2026上期708R・月別4/6でプラス)。
#   閾値0.65は維持(0.675は帯内で逆に悪化・0.70+はn=85で不安定)。5-8R/場/A1級等のフィルタは
#   交絡・ノイズで不採用。
def k_ex(hon):     # 2連複（上限3・場合に応じて1〜3点）。2026-07-16 上限5→3へ縮小:
    # backtest(honest OOS 33,935R)で回収率は1〜3点が頭打ち(~84%)・4点以降は単調減。
    # 堅い(hon高)ほど本命ペアに確率が集中するので点数を絞り、荒れるほど3点まで広げる。
    return 1 if hon >= 0.65 else 2 if hon >= 0.50 else 3


def k_tri(hon):    # 3連単（上限20）
    return (3 if hon >= 0.65 else 7 if hon >= 0.50 else 10 if hon >= 0.40
            else 14 if hon >= 0.30 else 20)


CLASS_LABEL = {4: "A1", 3: "A2", 2: "B1", 1: "B2"}   # class_ord → 公式級別
# 各コース(枠)の平均1着率の概算。コース毎の特徴は「この平均をどれだけ上回るか」で測る
# （2〜6コースは構造的に勝率が低いので、絶対値でなく相対で評価しないと不当に低くなる）。
LANE_BASE = {1: 0.50, 2: 0.14, 3: 0.12, 4: 0.10, 5: 0.08, 6: 0.05}
# コース別の3着以内率(3連対率)の基準。全国実績の枠別平均（2026データ実測）。
LANE3_BASE = {1: 0.79, 2: 0.575, 3: 0.543, 4: 0.439, 5: 0.362, 6: 0.262}


# ───────────── API予想（勝率＋枠＋機力の簡易合成）─────────────
# 学習モデル(従来予想)とは別系統の「素の予想」。非公式/公式APIが配信する選手データ
# （全国勝率・当地勝率・モーター2連率）と枠(コース)基準だけを透明な式で合成する。
#   score_w = LANE_BASE[w] × (勝率_w / 場平均勝率)^γ × (1 + β×(モーター2連率_w − 場平均)/100)
#   p_win_w = score_w / Σscore   （γ=1.3 で実力差を少し強調・β=0.6 で機力を控えめに加味）
# 「そのまま反映」＝APIの数値をこの式に通すだけで、学習・補正は一切しない。
_API_GAMMA = 1.3
_API_BETA = 0.6


def _api_rate(nat, loc):
    """全国勝率と当地勝率の合成。当地が0/欠損なら全国のみ（その逆も可）。両方無ければ None。"""
    nat = nat if (nat and nat > 0) else None
    loc = loc if (loc and loc > 0) else None
    if nat is not None and loc is not None:
        return 0.5 * nat + 0.5 * loc
    return nat if nat is not None else loc


def api_pwin(race):
    """race: {枠w: {"nat":全国勝率, "loc":当地勝率, "motor":モーター2連率0-100}}
    → {枠w: p_win(float)}。枠(コース)基準 × 勝率の相対 × 機力補正 の簡易合成。"""
    rate = {w: _api_rate(b.get("nat"), b.get("loc")) for w, b in race.items()}
    rv = [v for v in rate.values() if v is not None]
    ravg = sum(rv) / len(rv) if rv else 1.0
    mot = {w: b.get("motor") for w, b in race.items()}
    mv = [m for m in mot.values() if m is not None]
    mavg = sum(mv) / len(mv) if mv else 0.0
    sc = {}
    for w in race:
        r = rate[w] if rate[w] is not None else ravg
        m = mot[w] if mot[w] is not None else mavg
        rel = (r / ravg) if ravg > 0 else 1.0
        f = LANE_BASE.get(w, 0.1) * (rel ** _API_GAMMA) * (1 + _API_BETA * (m - mavg) / 100.0)
        sc[w] = max(f, 0.003)
    tot = sum(sc.values())
    return {w: sc[w] / tot for w in sc} if tot > 0 else {w: 1 / 6 for w in race}


def build_api_scores(rel):
    """features_race_relative の全行から API予想 p_win を算出。{(race_id, 枠int): p_win}。
    6艇そろわないレースは除外（学習モデルと同条件）。"""
    races = {}
    for r in rel:
        try:
            w = int(r["枠番"])
        except (ValueError, KeyError):
            continue
        races.setdefault(r["race_id"], {})[w] = {
            "nat": to_float(r.get("win_rate_national")),
            "loc": to_float(r.get("win_rate_local")),
            "motor": to_float(r.get("motor_top2_rate")),
        }
    out = {}
    for rid, race in races.items():
        if len(race) != 6:
            continue
        for w, p in api_pwin(race).items():
            out[(rid, w)] = p
    return out


def calib_knots(api_map, pred, since="20260101"):
    """API簡易合成の1着確率 p → 実1着率 の単調較正カーブ（isotonic/PAV）の節点。
    検証(2026-07-07 OOS 5-6月): API確率は高域で実力を+11〜12pt過小評価・0.35-0.45は過大評価。
    ★表示専用＝帯判定(0.45/0.65)・点数・買い目・EVは生値のまま（backtest済みルールを不変に保つ）。
    返り値 [[x,y], ...]（x昇順・区分線形補間用）。データ不足時は []（JS側は恒等で縮退）。"""
    from collections import defaultdict
    bins = defaultdict(lambda: [0, 0, 0.0])          # i -> [n, win, sum_p]
    for (rid, w), p in api_map.items():
        if p is None or rid[2:10] < since:
            continue
        pr = pred.get((rid, str(w)))
        if pr is None:
            continue
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            continue
        i = min(int(p * 40), 39)                     # 0.025刻み40ビン
        b = bins[i]
        b[0] += 1
        b[1] += (fin == 1)
        b[2] += p
    seq = [(b[2] / b[0], b[1] / b[0], b[0]) for i, b in sorted(bins.items())
           if b[0] >= 30]                            # 小標本ビンは除外
    if len(seq) < 5:
        return []
    # PAV（重み=ビンn）で単調非減少に整形
    blocks = [[x, y, n] for x, y, n in seq]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][1] > blocks[i + 1][1] + 1e-12:
            a, b = blocks[i], blocks[i + 1]
            n = a[2] + b[2]
            merged = [(a[0] * a[2] + b[0] * b[2]) / n,
                      (a[1] * a[2] + b[1] * b[2]) / n, n]
            blocks[i:i + 2] = [merged]
            i = max(i - 1, 0)
        else:
            i += 1
    knots = [[0.0, 0.0]] + [[round(x, 4), round(y, 4)] for x, y, _ in blocks] \
        + [[1.0, 1.0]]
    return knots


def _cal_interp(knots, p):
    """calib_knots の区分線形補間（ログ表示用・JS calP と同一ロジック）。"""
    if not knots:
        return p
    lo, hi = knots[0], knots[-1]
    for k in knots:
        if k[0] <= p:
            lo = k
        else:
            hi = k
            break
    if hi[0] <= lo[0]:
        return lo[1]
    return lo[1] + (hi[1] - lo[1]) * (p - lo[0]) / (hi[0] - lo[0])


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


def _pl_topk(s, kind, k):
    """PL確率 上位k の買い目（枠tuple のリスト）。返還点数の判定に使う。"""
    import itertools
    idx = [i for i in range(6) if s[i] and s[i] > 0]
    combos = [tuple(i + 1 for i in c) for c in itertools.permutations(idx, kind)]
    combos.sort(key=lambda c: _pl_prob(s, list(c)), reverse=True)
    return combos[:k]


# ── 2連複（順不同ペア）。2026-07-07 に2連単から切替: 同じ点数(k_ex)で
#    回収78.4→82.1%(+3.7pt)・的中52→68%(backtest_fukushiki 26,843R・model系統)。
def _pf_prob(s, pair):
    """2連複ペアのPL確率 = 両順序の和。pair=(a,b) 枠番。"""
    a, b = pair
    return _pl_prob(s, [a, b]) + _pl_prob(s, [b, a])


def _pf_topk(s, k):
    """2連複: PL確率上位k のペア（枠tuple 昇順）。全15点から選ぶ。"""
    idx = [i + 1 for i in range(6) if s[i] and s[i] > 0]
    pairs = [(a, b) for i, a in enumerate(idx) for b in idx[i + 1:]]
    pairs.sort(key=lambda p: _pf_prob(s, p), reverse=True)
    return pairs[:k]


def _pf_rank(s, actual):
    """actual（枠tuple・順不同）の2連複PL順位（1=最尤）。"""
    act = tuple(sorted(actual))
    idx = [i + 1 for i in range(6) if s[i] > 0]
    pa = _pf_prob(s, act)
    g = sum(1 for i, a in enumerate(idx) for b in idx[i + 1:]
            if _pf_prob(s, (a, b)) > pa)
    return g + 1


# ── 以下はサイト本体（JS の laneRankMap/comboKind/triBuyList/allocYen）の Python 版。
#    場別成績(recent_stats)を「実際に買った場合」＝買い目UI/上部サマリーと同一ポリシーで集計する。
def _lane_rank_map(s):
    """枠 -> 予想順位（1=スコア最大）。"""
    order = sorted(range(6), key=lambda i: s[i], reverse=True)
    return {i + 1: rk + 1 for rk, i in enumerate(order)}


def _tri_buy_list(combos, k, hon, rank_map):
    """3連単の購入買い目: 標準帯(0.45-0.65)は穴型(含む枠の最下位順位≥5)を除外して上位k。"""
    if 0.45 <= hon < 0.65:
        combos = [c for c in combos if max(rank_map[w] for w in c) < 5]
    return combos[:k]


def _meri_w(probs, hon):
    """買い目配分の「爆発重視」重み。JS meriW と同一。
    全帯 weight ∝ p^-1（EVフラット）＝当たれば各点ほぼ同額回収まで薄目に振り切る。
    合成回収は現行(妙味寄せ)とほぼ不変(-0.1pt)だが、≥5倍の爆発頻度と単レース最大配当が
    大きく増える（穴2連複は≥5倍が約11倍・穴帯のみ回収-2.5pt/[[boatrace-bet-points]]）。"""
    g = -1.0
    return [max(p, 1e-12) ** g for p in probs]


def _alloc_yen(probs, budget=2000, unit=100):
    """予算を確率比例で配分（各点最低1ユニット＝¥100・合計=budget）。JS allocYen と同一。"""
    n = len(probs)
    if not n:
        return []
    units = [1] * n
    rest = round(budget / unit) - n
    if rest > 0:
        tot = sum(probs) or 1
        raw = [p / tot * rest for p in probs]
        add = [int(x) for x in raw]
        r = rest - sum(add)
        order = sorted(range(n), key=lambda i: raw[i] - int(raw[i]), reverse=True)
        for kk in range(r):
            add[order[kk % n]] += 1
        units = [units[i] + add[i] for i in range(n)]
    return [u * unit for u in units]


def _ana_cand_ref(ab):
    """穴目（買わない参考）＝穴候補(API4番人気)アタマ×上位3艇の2-3着流し6点。JS anaCandRef と同一。
    ab=API per-mille 配列（長さ6）。"""
    idx = sorted(range(6), key=lambda i: ab[i], reverse=True)
    c = idx[3] + 1
    T = [idx[0] + 1, idx[1] + 1, idx[2] + 1]
    return [(c, a, b) for a in T for b in T if a != b]


def _taikou_ref(sv):
    """穴予想（波乱帯・帯別方式）＝対抗(2番手)アタマ×本命/3番手/4番手の2-3着流し6点。
    JS taikouRef と同一（展示反映が無い履歴では betScore=学習モデル score）。
    sv=学習モデル score 配列（長さ6）。backtest 波乱帯 対抗6点=回収88.2%（穴候補79.7%より上位）。"""
    idx = sorted(range(6), key=lambda i: sv[i], reverse=True)
    head = idx[1] + 1
    T = [idx[0] + 1, idx[2] + 1, idx[3] + 1]
    return [(head, a, b) for a in T for b in T if a != b]


def venue_stats(rel, pred, score_map, hist, payout, since, hon_map=None):
    """since 以降の結果がある全レースから、場別の的中率＋変動回収率を集計。
    score_map={(race_id,枠int):p_win} で予想系統を差し替え（従来モデル/API予想 共用）。
    hon_map={race_id:本命確率} を渡すと荒れ度・点数をその確率（＝従来モデル基準）で
    レース共通に固定し、系統別 score は順位付け（どの買い目か）と本命1着判定にのみ使う。
    各場: 本命1着 / 2連複(本命=top1,top3,変動K,変動回収) / 3連単(本命,top3,変動K,変動回収)。
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
            pm = score_map.get((rid, int(r["枠番"])))
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
    # n,win,e1,e3,eK(2連複変動),t1,t3,tK(3連単変動),pts2,pay2,pts3,pay3
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
        # 荒れ度・点数はレース固有（API本命確率 hon_map）で共通化。系統別 score は順位付けのみ。
        hon = hon_map.get(rid) if hon_map is not None else max(s)
        if hon is None:
            continue
        kx, kt = k_ex(hon), k_tri(hon)
        er = _pf_rank(s, tuple(order[:2]))                     # 2連複 本命/top3=モデル診断
        tr = _pl_rank(s, 3, tuple(order[:3]))
        a = agg[rc["c"]]
        name[rc["c"]] = rc["v"]
        dmin = rc["d"] if dmin is None or rc["d"] < dmin else dmin
        dmax = rc["d"] if dmax is None or rc["d"] > dmax else dmax
        # 変動点数（実際に買える点数）と実配当で回収率を集計。
        m = sum(1 for w in range(1, 7) if s[w - 1] and s[w - 1] > 0)
        bet2 = min(kx, m * (m - 1) // 2)
        bet3 = min(kt, m * (m - 1) * (m - 2))
        po = payout.get(rid, (0, 0, 0))
        a[0] += 1
        a[1] += (hm == order[0])
        a[2] += er <= 1; a[3] += er <= 3
        a[5] += tr <= 1; a[6] += tr <= 3
        if bet2 > 0:
            a[8] += bet2
            if er <= bet2:                                     # 2連複 変動K
                a[4] += 1; a[9] += (po[2] if len(po) > 2 else 0)
        if bet3 > 0:
            a[10] += bet3
            if tr <= bet3:                                     # 3連単 変動K
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
    """keep の日付の K-file から race_id -> (2連単配当, 3連単配当, 2連複配当)。
    po[2]=2連複 は 2026-07-07 の券種切替で追加（後方互換のため末尾に追加）。"""
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
                po2, po3 = int(r["2連単_配当"]), int(r["3連単_配当"])
            except (ValueError, KeyError):
                continue
            try:
                pof = int(r["2連複_配当"])
            except (ValueError, KeyError):
                pof = 0
            payout[rid] = (po2, po3, pof)
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


def regime_result(rel, pred, score_map, hist, payout, since, hon_map=None):
    """予想の荒れ度（鉄板/標準/穴）ごとに、実際の的中率と回収率を集計。
    score_map={(race_id,枠int):p_win} で予想系統を差し替え（従来モデル/API予想 共用）。
    hon_map={race_id:本命確率} を渡すと荒れ度区分・点数・除外をその確率（従来モデル基準）で
    レース共通に固定（系統別 score は順位付けと本命1着判定のみ）。
    荒れ度＝本命確率(top1 p_win): ≥0.65 鉄板 / 0.45-0.65 標準 / <0.45 穴(波乱含み)。
    買い目＝当日と同じ変動点数（2連複 k_ex≤3 / 3連単 k_tri≤20）。
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
            pm = score_map.get((rid, int(r["枠番"])))
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
        hon = hon_map.get(rid) if hon_map is not None else max(s)
        if hon is None:
            continue
        gi = 0 if hon >= 0.65 else (2 if hon < 0.45 else 1)
        kx, kt = k_ex(hon), k_tri(hon)
        m = sum(1 for w in range(1, 7) if s[w - 1] and s[w - 1] > 0)
        bet2 = min(kx, m * (m - 1) // 2)
        bet3 = min(kt, m * (m - 1) * (m - 2))
        po = payout.get(rid, (0, 0, 0))
        hm = max(range(6), key=lambda i: s[i]) + 1     # モデル1番手（本命）の枠
        a = agg[gi]
        a["n"] += 1
        a["win"] += (hm == order[0])                   # 本命艇が1着
        if bet2 > 0:
            a["n2"] += 1
            a["pts2"] += bet2
            if _pf_rank(s, tuple(order[:2])) <= bet2:              # 2連複
                a["h2"] += 1
                a["p2"] += (po[2] if len(po) > 2 else 0)
        if len(order) >= 3 and bet3 > 0:
            a["n3"] += 1
            a["pts3"] += bet3
            if _pl_rank(s, 3, tuple(order[:3])) <= bet3:
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


def makuri_rates(glob_pat="data/k*.csv", min_wins=5):
    """全K-fileから 登番 → まくり率（勝ち星のうち まくり/まくり差し の割合）。
    「対抗1艇（穴）」の根拠タグ『捲り屋』表示用。検証(残差テスト OOS)では
    まくり傾向×モーターは p_win に織り込み済み＝的中を超える力は無いが、
    なぜこの艇が対抗かの説明材料として出す。勝ち星 min_wins 未満は None 扱い。"""
    win, mak = {}, {}
    for p in sorted(glob.glob(glob_pat)):
        try:
            for r in load(p):
                if (r.get("着順") or "").strip() != "1":
                    continue
                reg = (r.get("登番") or "").strip()
                if not reg:
                    continue
                win[reg] = win.get(reg, 0) + 1
                if (r.get("決まり手") or "").strip() in ("まくり", "まくり差し"):
                    mak[reg] = mak.get(reg, 0) + 1
        except OSError:
            continue
    return {reg: round(mak.get(reg, 0) / w, 3)
            for reg, w in win.items() if w >= min_wins}


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


def recent_stats(out, payout, pmkey="b"):
    """前日・前々日（結果のある日）の場別 2連複/3連単 的中率・回収率。
    pmkey で順位付けスコアの系統を切替（"b"=従来モデル / "ab"=API予想）。
    ★買い目はサイト本体（買い目UI／上部サマリー daySummary）と同一ポリシーで集計＝「実際に買った場合」:
      - 荒れ度・点数はレース共通＝API本命確率(r.ab)で固定。
      - 2連複＝確率上位 k_ex（全帯）。3連単＝確率上位 k_tri、ただし
        **穴帯(本命<0.45)は買わない**・**標準帯(0.45-0.65)は穴型(5-6番手絡み)を除外**(triBuyList)。
      - 各券種に¥2,000を確率比例配分(allocYen)。**非完走(F等)艇を含む買い目は賭け金を投資から除外(返還)**。
    回収率 = Σ(配当×賭け金/100) / Σ賭け金 ×100。"""
    ag = {}
    dmin = dmax = None
    for r in out:
        fins = [b[2] for b in r["b"]]
        if not any(f == 1 for f in fins):     # 結果のあるレースのみ
            continue
        sv = [b[1] for b in r["b"]] if pmkey == "b" else r[pmkey]
        order = sorted([w for w in range(1, 7)
                        if fins[w - 1] is not None and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if fins[order[0] - 1] != 1:
            continue
        # 荒れ度・点数はレース共通＝API本命確率で固定（系統別は sv の順位のみ）。
        hon = max(r["ab"]) / 1000.0
        kx, kt = k_ex(hon), k_tri(hon)
        po = payout.get(r["id"], (0, 0, 0))
        # 非完走（フライング等）艇＝着順なし。その艇を含む買い目は返還＝賭け金から除外。
        fly = {w for w in range(1, 7) if not fins[w - 1]}
        a = ag.setdefault(r["c"], {"v": r["v"], "n2": 0, "h2": 0, "inv2": 0, "ret2": 0,
                                   "n3": 0, "h3": 0, "inv3": 0, "ret3": 0, "nf": 0})
        dmin = r["d"] if dmin is None or r["d"] < dmin else dmin
        dmax = r["d"] if dmax is None or r["d"] > dmax else dmax
        if fly:
            a["nf"] += 1
        # 2連複（全帯・確率上位 k_ex・¥2,000配分・F返還）
        if len(order) >= 2:
            buy2 = _pf_topk(sv, kx)
            if buy2:
                yen2 = _alloc_yen(_meri_w([_pf_prob(sv, c) for c in buy2], hon))
                a["n2"] += 1
                act2 = tuple(sorted(order[:2]))
                for c, y in zip(buy2, yen2):
                    if not any(w in fly for w in c):
                        a["inv2"] += y                        # 返還ぶんは投資から除外
                    if c == act2:
                        a["ret2"] += round((po[2] if len(po) > 2 else 0) * y / 100)
                        a["h2"] += 1
        # 3連単（穴帯<0.45は買わない・標準帯は穴型除外・¥2,000配分・F返還）
        if len(order) >= 3 and hon >= 0.45:
            buy3 = _tri_buy_list(_pl_topk(sv, 3, 200), kt, hon, _lane_rank_map(sv))
            if buy3:
                yen3 = _alloc_yen(_meri_w([_pl_prob(sv, c) for c in buy3], hon))
                a["n3"] += 1
                act3 = tuple(order[:3])
                for c, y in zip(buy3, yen3):
                    if not any(w in fly for w in c):
                        a["inv3"] += y
                    if c == act3:
                        a["ret3"] += round(po[1] * y / 100)
                        a["h3"] += 1

    def stat(a):
        return [a["v"], a["n2"],
                round(a["h2"] / a["n2"] * 100) if a["n2"] else 0,
                round(a["ret2"] / a["inv2"] * 100) if a["inv2"] else 0,
                round(a["h3"] / a["n3"] * 100) if a["n3"] else 0,
                round(a["ret3"] / a["inv3"] * 100) if a["inv3"] else 0]

    rows = [stat(a) for a in ag.values()]
    rows.sort(key=lambda x: x[3], reverse=True)     # 2連単回収率の降順
    T = {k: sum(a[k] for a in ag.values()) for k in
         ["n2", "h2", "ret2", "inv2", "n3", "h3", "ret3", "inv3"]}
    T["v"] = "全場"
    nf = sum(a.get("nf", 0) for a in ag.values())
    return {"from": dmin, "to": dmax, "rows": rows, "all": stat(T), "nf": nf}


def game_ledger(rel, pred, model_map, hon_canon, payout, start_date,
                start_balance=1_000_000, base=None):
    """仮想100万円チャレンジ: start_date から日々、AI が選んだレースに投票し残高を転がす。
    ★AI運用方針（実験）:
      - 対象＝鉄板レース（本命確率 hon≥0.65）のみ厳選（かけない日・レースがあってよい）。
      - 1レースあたり残高の約 F_RACE を、そのレースのサイト買い目
        （2連複＝上位k_ex／3連単＝上位k_tri・確率比例配分 allocYen）に投票。
      - 1日の投票上限＝残高（最大 start_balance）。超過時は各レースを比例縮小。
      - 実配当で精算・フライング（非完走）を含む買い目は返還。残高 <¥100 で終了。
    ★ステートレス: 毎ビルドで start_date〜前日の実結果から丸ごと再計算（永続化不要・自己修復）。
    base 当日は結果待ち＝残高に反映せず「運用中」プレビューのみ返す。"""
    from collections import defaultdict
    F_RACE = 0.12                     # 1レース当たり資金比率（強気運用）
    HON_TETSU = 0.65
    # レースを日付ごとに束ねる
    races = {}
    for r in rel:
        d = r["日付"]
        if d < start_date:
            continue
        rid = r["race_id"]
        try:
            w = int(r["枠番"])
        except (ValueError, TypeError):
            continue
        pr = pred.get((rid, r["枠番"]), {})
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            fin = None
        rc = races.setdefault(rid, {"d": d, "b": {}})
        rc["b"][w] = (model_map.get((rid, w)), fin)
    by_date = defaultdict(list)
    for rid, rc in races.items():
        by_date[rc["d"]].append((rid, rc))

    def tetsu_picks(day_races):
        """その日の鉄板レース（sv完備・hon≥0.65）を (rid, sv, fins, hon, settled) で返す。"""
        out = []
        for rid, rc in day_races:
            if len(rc["b"]) != 6:
                continue
            sv = [rc["b"][w][0] for w in range(1, 7)]
            fins = [rc["b"][w][1] for w in range(1, 7)]
            if any(x is None for x in sv):
                continue
            hon = hon_canon.get(rid)
            if hon is None or hon < HON_TETSU:
                continue
            settled = any(f == 1 for f in fins)
            out.append((rid, sv, fins, hon, settled))
        return out

    bal = float(start_balance)
    peak = float(start_balance)
    rows = []
    busted = False
    for d in sorted(by_date):
        if d == base:
            continue                       # 当日は下でプレビュー
        if bal < 100:
            busted = True
            break
        picks = [p for p in tetsu_picks(by_date[d]) if p[4]]   # 精算済みのみ
        if not picks:
            continue
        day_cap = min(bal, start_balance)
        raw = [bal * F_RACE for _ in picks]
        tot = sum(raw) or 1
        scale = min(1.0, day_cap / tot)
        staked = returned = 0.0
        nbet = nhit = 0
        for (rid, sv, fins, hon, _), rb in zip(picks, raw):
            rbud = rb * scale
            order = sorted([w for w in range(1, 7)
                            if fins[w - 1] and fins[w - 1] >= 1],
                           key=lambda w: fins[w - 1])
            if not order or fins[order[0] - 1] != 1:
                continue
            fly = {w for w in range(1, 7) if not fins[w - 1]}
            po = payout.get(rid, (0, 0, 0))
            kx, kt = k_ex(hon), k_tri(hon)
            hit = False
            # 2連複（券種予算＝レース予算の半分）
            buy2 = _pf_topk(sv, kx)
            b2 = round(rbud * 0.5 / 100) * 100
            if buy2 and b2 >= len(buy2) * 100:
                yen2 = _alloc_yen(_meri_w([_pf_prob(sv, c) for c in buy2], hon), budget=b2)
                act2 = tuple(sorted(order[:2])) if len(order) >= 2 else None
                for c, y in zip(buy2, yen2):
                    if any(w in fly for w in c):
                        continue                      # 返還
                    staked += y
                    if c == act2:
                        returned += round((po[2] if len(po) > 2 else 0) * y / 100)
                        hit = True
            # 3連単（鉄板は穴帯・穴型除外に非該当）
            buy3 = _tri_buy_list(_pl_topk(sv, 3, 200), kt, hon, _lane_rank_map(sv))
            b3 = round(rbud * 0.5 / 100) * 100
            if buy3 and b3 >= len(buy3) * 100 and len(order) >= 3:
                yen3 = _alloc_yen(_meri_w([_pl_prob(sv, c) for c in buy3], hon), budget=b3)
                act3 = tuple(order[:3])
                for c, y in zip(buy3, yen3):
                    if any(w in fly for w in c):
                        continue
                    staked += y
                    if c == act3:
                        returned += round(po[1] * y / 100)
                        hit = True
            nbet += 1
            nhit += 1 if hit else 0
        bal = bal - staked + returned
        peak = max(peak, bal)
        rows.append({"d": d, "n": len(picks), "nbet": nbet, "nhit": nhit,
                     "stake": round(staked), "ret": round(returned),
                     "pl": round(returned - staked), "bal": round(bal)})
        if bal < 100:
            busted = True
            break

    # 当日プレビュー（結果待ち・残高不変）
    pending = None
    if base and not busted and bal >= 100 and base in by_date:
        bp = tetsu_picks(by_date[base])
        if bp:
            n = len(bp)
            stake = min(n * bal * F_RACE, min(bal, start_balance))
            pending = {"d": base, "n": n, "stake": round(stake)}

    return {"start": start_balance, "bal": round(bal), "peak": round(peak),
            "busted": busted, "rows": rows, "pending": pending,
            "from": start_date}


def daily_recovery(rel, pred, model_map, api_map, hon_canon, payout, base, ndays=30):
    """今日から ndays 日さかのぼる「日別」回収率の時系列（券種別）。
    ★買い目＝サイト本体／上部サマリー(daySummary)と同一ポリシー（順位付け＝学習モデル、
      荒れ度・点数＝API本命確率）。折れ線グラフ用に各日の 2連複／3連単／穴目 を集計。
      - 2連複＝確率上位 k_ex（全帯）・各¥2,000確率比例配分・F返還。
      - 3連単＝triOn(hon≥0.45)のみ・確率上位 k_tri（標準帯は穴型除外）・¥2,000配分・F返還。
      - 穴目＝**買わない参考**。穴帯(hon<0.45)で対抗アタマ6点(_taikou_ref／帯別方式・波乱帯)を各¥100フル。
        購入はしないが「的中していれば穴予想的中」が分かるよう的中率／回収率を出す。
    各券種 [n(対象R), hit(的中R), inv(投資円), ret(払戻円)]。回収率＝ret/inv。"""
    from collections import defaultdict
    races = {}
    for r in rel:
        rid = r["race_id"]
        try:
            w = int(r["枠番"])
        except (ValueError, TypeError):
            continue
        pr = pred.get((rid, r["枠番"]), {})
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            fin = None
        rc = races.setdefault(rid, {"d": r["日付"], "b": {}, "ab": {}})
        rc["b"][w] = (model_map.get((rid, w)), fin)
        rc["ab"][w] = api_map.get((rid, w))
    # 日別 [n,hit,inv,ret]
    day = defaultdict(lambda: {"ex": [0, 0, 0, 0], "tri": [0, 0, 0, 0],
                               "ana_h": [0, 0, 0, 0], "ana_s": [0, 0, 0, 0]})
    for rid, rc in races.items():
        if len(rc["b"]) != 6:
            continue
        sv0 = [rc["b"][w][0] for w in range(1, 7)]
        fins = [rc["b"][w][1] for w in range(1, 7)]
        ab = [round((rc["ab"].get(w) or 0) * 1000) for w in range(1, 7)]
        if any(x is None for x in sv0):
            continue
        # ★順位付けは客側 r.b[i][1]=round(p_win*1000) と同じ per-mille 整数で行う。
        # 生の float で並べると丸め由来の同点順位が客側とズレ、対抗(2番手)が入れ替わって
        # 上部サマリー(daySummary)と穴目回収率が食い違う（例: 平和島1R の 199 同点）。
        sv = [round(x * 1000) for x in sv0]
        order = sorted([w for w in range(1, 7)
                        if fins[w - 1] is not None and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < 2 or fins[order[0] - 1] != 1:
            continue
        hon = hon_canon.get(rid)
        if hon is None:
            continue
        po = payout.get(rid, (0, 0, 0))
        fly = {w for w in range(1, 7) if not fins[w - 1]}
        act2 = tuple(sorted(order[:2]))
        act3 = tuple(order[:3]) if len(order) >= 3 else None
        D = day[rc["d"]]
        # 2連複（全帯）
        buy2 = _pf_topk(sv, k_ex(hon))
        if buy2:
            yen2 = _alloc_yen(_meri_w([_pf_prob(sv, c) for c in buy2], hon))
            D["ex"][0] += 1
            for c, y in zip(buy2, yen2):
                if not any(w in fly for w in c):
                    D["ex"][2] += y
                if c == act2:
                    D["ex"][3] += round((po[2] if len(po) > 2 else 0) * y / 100)
                    D["ex"][1] += 1
        # 3連単（triOn＝hon≥0.45のみ・標準帯は穴型除外）
        if act3 and hon >= 0.45:
            buy3 = _tri_buy_list(_pl_topk(sv, 3, 200), k_tri(hon), hon, _lane_rank_map(sv))
            if buy3:
                yen3 = _alloc_yen(_meri_w([_pl_prob(sv, c) for c in buy3], hon))
                D["tri"][0] += 1
                for c, y in zip(buy3, yen3):
                    if not any(w in fly for w in c):
                        D["tri"][2] += y
                    if c == act3:
                        D["tri"][3] += round(po[1] * y / 100)
                        D["tri"][1] += 1
        # 穴目（買わない参考・帯別方式・各¥100フル）。波乱帯(<0.45)=対抗6点／標準帯(0.45-0.65)=穴候補6点。
        # グラフ・券種別テーブルで 波乱/標準/合算 を選択表示できるよう帯別に分けて集計。
        if act3 and hon < 0.45:
            D["ana_h"][0] += 1
            for c in _taikou_ref(sv):
                if not any(w in fly for w in c):
                    D["ana_h"][2] += 100
                if c == act3:
                    D["ana_h"][3] += po[1]
                    D["ana_h"][1] += 1
        elif act3 and hon < 0.65:
            D["ana_s"][0] += 1
            for c in _ana_cand_ref(ab):
                if not any(w in fly for w in c):
                    D["ana_s"][2] += 100
                if c == act3:
                    D["ana_s"][3] += po[1]
                    D["ana_s"][1] += 1
    days = [d for d in sorted(day) if d <= base][-ndays:]

    def pack(a):
        return {"n": a[0], "h": a[1], "inv": a[2], "ret": a[3]}
    keys = ("ex", "tri", "ana_h", "ana_s")
    ser = [{"d": d, **{k: pack(day[d][k]) for k in keys}} for d in days]
    tot = {k: [0, 0, 0, 0] for k in keys}
    for d in days:
        for k in keys:
            for i in range(4):
                tot[k][i] += day[d][k][i]
    return {"days": ser, "tot": {k: pack(v) for k, v in tot.items()},
            "from": days[0] if days else None, "to": days[-1] if days else None}


def game_ledger_ana(rel, pred, model_map, api_map, hon_canon, payout, start_date,
                    start_balance=1_000_000, base=None):
    """仮想100万円チャレンジ【穴帯バージョン】: 穴帯(本命確率<0.45)レースだけを狙い、
    穴予想＝対抗(学習モデル2番手)アタマ6点(_taikou_ref／帯別方式・波乱帯)を3連単でフル購入して
    残高を転がす実験。※backtest 波乱帯 対抗6点=回収88.2%（穴候補6点79.7%より上位）で統一。
    ★本家(鉄板)との対比＝『穴を追い続けたら100万はどうなるか』。
      - 対象＝その日の穴帯レース全て。1レースあたり残高の約 F_RACE を6点に均等配分(¥100単位)。
      - 1日の投票上限＝残高（最大 start_balance）。超過時は比例縮小。実配当で精算・F返還。残高<¥100で終了。
    ★ステートレス（毎ビルド再計算）。base当日は結果待ち＝残高不変のプレビュー。"""
    from collections import defaultdict
    F_RACE = 0.01                      # 穴は高分散・対象レースが多い→1レース控えめ
    HON_ANA = 0.45
    races = {}
    for r in rel:
        d = r["日付"]
        if d < start_date:
            continue
        rid = r["race_id"]
        try:
            w = int(r["枠番"])
        except (ValueError, TypeError):
            continue
        pr = pred.get((rid, r["枠番"]), {})
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            fin = None
        rc = races.setdefault(rid, {"d": d, "b": {}, "ab": {}})
        rc["b"][w] = (model_map.get((rid, w)), fin)
        rc["ab"][w] = api_map.get((rid, w))
    by_date = defaultdict(list)
    for rid, rc in races.items():
        by_date[rc["d"]].append((rid, rc))

    def ana_picks(day_races):
        out = []
        for rid, rc in day_races:
            if len(rc["b"]) != 6:
                continue
            sv = [rc["b"][w][0] for w in range(1, 7)]
            fins = [rc["b"][w][1] for w in range(1, 7)]
            ab = [round((rc["ab"].get(w) or 0) * 1000) for w in range(1, 7)]
            if any(x is None for x in sv):
                continue
            hon = hon_canon.get(rid)
            if hon is None or hon >= HON_ANA:
                continue
            settled = any(f == 1 for f in fins)
            out.append((rid, ab, sv, fins, settled))
        return out

    bal = float(start_balance)
    peak = float(start_balance)
    rows = []
    busted = False
    for d in sorted(by_date):
        if d == base:
            continue
        if bal < 100:
            busted = True
            break
        picks = [p for p in ana_picks(by_date[d]) if p[4]]
        if not picks:
            continue
        day_cap = min(bal, start_balance)
        raw = [bal * F_RACE for _ in picks]
        tot = sum(raw) or 1
        scale = min(1.0, day_cap / tot)
        staked = returned = 0.0
        nbet = nhit = 0
        for (rid, ab, sv, fins, _), rb in zip(picks, raw):
            rbud = rb * scale
            order = sorted([w for w in range(1, 7)
                            if fins[w - 1] and fins[w - 1] >= 1],
                           key=lambda w: fins[w - 1])
            if len(order) < 3 or fins[order[0] - 1] != 1:
                continue
            fly = {w for w in range(1, 7) if not fins[w - 1]}
            po = payout.get(rid, (0, 0))
            cand = _taikou_ref(sv)
            per = round(rbud / len(cand) / 100) * 100      # 6点に均等・¥100単位
            if per < 100:
                continue
            act3 = tuple(order[:3])
            hit = False
            for c in cand:
                if any(w in fly for w in c):
                    continue                                # 返還
                staked += per
                if c == act3:
                    returned += round(po[1] * per / 100)
                    hit = True
            nbet += 1
            nhit += 1 if hit else 0
        bal = bal - staked + returned
        peak = max(peak, bal)
        rows.append({"d": d, "n": len(picks), "nbet": nbet, "nhit": nhit,
                     "stake": round(staked), "ret": round(returned),
                     "pl": round(returned - staked), "bal": round(bal)})
        if bal < 100:
            busted = True
            break

    pending = None
    if base and not busted and bal >= 100 and base in by_date:
        bp = ana_picks(by_date[base])
        if bp:
            n = len(bp)
            stake = min(n * bal * F_RACE, min(bal, start_balance))
            pending = {"d": base, "n": n, "stake": round(stake)}

    return {"start": start_balance, "bal": round(bal), "peak": round(peak),
            "busted": busted, "rows": rows, "pending": pending,
            "from": start_date}


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

    # 予想スコア: api_map=API予想(簡易合成・主系統)。荒れ度/割合/穴/点数の基準。
    api_map = build_api_scores(rel)
    # 荒れ度（鉄板/標準/穴）・割合・穴・点数・除外はすべて API本命確率で1回だけ判定し両系統共通化
    # （ユーザー決定 2026-06-27: 割合・帯分けともAPIに統一）。従来モデルは比較用の別系統スコア。
    hon_canon = {}
    for (rid, w), v in api_map.items():
        if v is not None and v > hon_canon.get(rid, -1.0):
            hon_canon[rid] = v
    # 学習モデル(predict_win.csv)の p_win マップ。本命/順位/買い目/1着確率・成績集計の主系統
    # （2026-06-29: 学習モデルを予想の主役に復帰。本命1着的中 56.7%>API 54.9%。
    #   荒れ度/点数/除外は hon_canon=API のまま＝backtestで回収率75.8%>フル切替74.3%）。
    model_map = {}
    for (rid, w), pr in pred.items():
        v = to_float(pr.get("p_win"))
        if v is not None:
            try:
                model_map[(rid, int(w))] = v
            except (ValueError, TypeError):
                pass

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
            "reg": (r.get("登番") or "").strip(),      # 対抗1艇の『捲り屋』タグ用
        }

    kres = load_kresult(keep)
    mk_map = makuri_rates()                          # 登番→まくり率（根拠タグ用）
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
        po = payout.get(rid)                          # (2連単配当, 3連単配当, 2連複配当)
        # API予想（簡易合成）の per-mille 配列。割合・荒れ度・点数はこのAPI確率で共通判定。
        ab = [round((api_map.get((rid, w)) or 0) * 1000) for w in range(1, 7)]
        out.append({"id": rid, "d": rc["d"], "c": rc["c"], "v": rc["v"],
                    "no": rc["no"], "mz": rc["mz"],
                    "ab": ab,
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
                    # 対抗1艇の『捲り屋』タグ用: 枠ごと まくり率（該当なしは null）
                    "mk": [mk_map.get(rc["feat"][w].get("reg"))
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

    # 発走時刻（締切時刻）を非公式OpenAPIから補完（④）。公式LZHに時刻が無いため。
    # today.json は当日分のみ＝base日のレースに付く。取得失敗時は空でグレースフル。
    start_times = {}
    try:
        import fetch_openapi
        start_times = fetch_openapi.fetch_start_times(base.replace("-", ""))
    except Exception as e:
        print(f"  発走時刻取得スキップ: {e}")
    for o in out:
        o["tm"] = start_times.get(o["id"], "")

    # 日付ラベル（当日/前日/前々日）
    rel_labels = ["当日", "前日", "前々日", "3日前", "4日前", "5日前", "6日前"]
    labels = []
    for i, d in enumerate(reversed(keep)):       # 新しい順
        labels.append([rel_labels[i] if i < len(rel_labels) else d, d])

    payout_all = load_payouts(sorted({r["日付"] for r in rel
                                      if r["日付"] >= args.stats_from}))
    # 学習モデル（主系統）の全履歴から場別成績・荒れ度別・直近を算出。
    # 順位付け＝学習モデル(model_map/"b")、荒れ度・点数の基準＝API本命確率(hon_canon)で共通。
    vstats_api = venue_stats(rel, pred, model_map, hist, payout_all, args.stats_from, hon_canon)
    recent_api = recent_stats(out, payout, "b")
    regime_api = regime_result(rel, pred, model_map, hist, payout_all, args.stats_from, hon_canon)

    rsp = rival_terciles(pred, args.stats_from)
    # 仮想100万円チャレンジ（7/1〜）: 全履歴からステートレスに毎回再計算（永続化不要）。
    game = game_ledger(rel, pred, model_map, hon_canon, payout_all,
                       "2026-07-01", 1_000_000, base)
    print(f"  100万円チャレンジ: 残高 ¥{game['bal']:,} "
          f"（精算 {len(game['rows'])}日・{'BUST' if game['busted'] else 'OK'}）")
    game_ana = game_ledger_ana(rel, pred, model_map, api_map, hon_canon, payout_all,
                               "2026-07-01", 1_000_000, base)
    print(f"  100万円チャレンジ【穴】: 残高 ¥{game_ana['bal']:,} "
          f"（精算 {len(game_ana['rows'])}日・{'BUST' if game_ana['busted'] else 'OK'}）")
    # 直近30日の日別回収率（券種別：2連単／3連単／穴目）。折れ線グラフ用。
    daily_rec = daily_recovery(rel, pred, model_map, api_map, hon_canon,
                               payout_all, base, 30)
    print(f"  日別回収率: {daily_rec['from']}〜{daily_rec['to']}"
          f"（{len(daily_rec['days'])}日）")
    # API確率→実1着率の較正カーブ（表示専用・帯/点数/買い目は生値のまま）
    cal = calib_knots(api_map, pred)
    print(f"  較正カーブ: 節点{len(cal)}個" + (f"（例 0.65→{_cal_interp(cal, 0.65):.2f}）" if cal else "（データ不足＝恒等）"))
    payload = {"labels": labels, "base": base, "races": out,
               "vstats_api": vstats_api, "recent_api": recent_api,
               "regime_api": regime_api, "rsp": rsp, "game": game,
               "game_ana": game_ana, "daily_rec": daily_rec, "cal": cal}
    html = HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False,
                                               separators=(",", ":")))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"○ 当日予想アプリ: {args.out}")
    print(f"  対象日 {keep}（既定表示={base}）/ レース {len(out)}")
    print(f"  場別成績: {args.stats_from}〜（{vstats_api['from']}〜{vstats_api['to']} "
          f"/ {vstats_api['n']}レース）")


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
  .sumbar{border:0.5px solid #2c3340;border-radius:10px;padding:8px 10px;margin:6px 0 12px;background:#12151c}
  .sumttl{font-size:11px;color:#9aa3b2;margin:0 0 6px}
  .sumt{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
  .sumt th{font-size:12px;font-weight:700;color:#cdd6e2;text-align:right;padding:2px 4px}
  .sumt th small{display:block;font-size:9px;color:#6b7280;font-weight:400}
  .sumt td{font-size:13px;text-align:right;padding:3px 4px;color:#e6e6e6}
  .sumt td.rl{text-align:left;color:#9aa3b2;font-size:12px}
  .sumt td small{font-size:9px;color:#6b7280;margin-left:3px}
  .sumt tr+tr td{border-top:0.5px solid #20262f}
  .sumt b.rok{color:#43c59e}.sumt b.ramb{color:#e0a93b}.sumt b.rng{color:#7e8796}
  .sumf{font-size:10.5px;color:#8a93a3;margin:6px 0 0}
  .sumf b{color:#cdd6e2;font-weight:700}
  .anascope{display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:11px;color:#9aa3b2;margin:8px 0 0}
  .asb{font-size:12px;padding:4px 12px;border-radius:7px;border:0.5px solid #39404d;
       background:#171b23;color:#cdd6e2;cursor:pointer}
  .asb.on{background:#2a2415;border-color:#5a4a23;color:#e8c08a;font-weight:700}
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
  .lvbtn{font-size:12px;font-weight:600;padding:5px 13px;border-radius:14px;border:0.5px solid #39404d;
         background:transparent;color:#9aa3b2;cursor:pointer}
  .lvbtn.on{background:#374151;color:#fff;border-color:#4b5563}
  .lvbtn.tetsu.on{background:#10362c;color:#43c59e;border-color:#1d6b52}
  .lvbtn.haran.on{background:#3a1f1f;color:#e06b6b;border-color:#7a3a3a}
  .sortbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:6px 0 2px;font-size:12px;color:#8ea0ba}
  .sortb{font-size:12px;padding:4px 11px;border-radius:7px;border:0.5px solid #39404d;background:transparent;color:#cdd6e2;cursor:pointer}
  .sortb.on{background:#374151;color:#fff;border-color:#4b5563}
  h3{font-size:15px;font-weight:600;margin:14px 0 4px;color:#b9c2d0}
  .row{display:flex;align-items:center;gap:8px;padding:10px 4px;border-bottom:0.5px solid #2a2f3a;cursor:pointer;flex-wrap:wrap;row-gap:4px}
  .row:active{background:#12161d}
  .rno{font-size:13px;color:#9aa3b2;min-width:30px;font-weight:600}
  .rtm{font-size:11px;color:#6b7280;font-variant-numeric:tabular-nums;min-width:34px}
  .vbtn.rt.on{background:#2b2f1c;color:#f2d98a;border-color:#5a6472}
  .livedot{width:7px;height:7px;border-radius:50%;background:#43c59e;display:inline-block;margin-right:4px;vertical-align:-1px}
  .rrow .rtm2{font-size:13px;color:#cdd6e2;font-variant-numeric:tabular-nums;min-width:38px;font-weight:600}
  .vpill{font-size:11px;padding:1px 7px;border-radius:5px;border:0.5px solid;white-space:nowrap}
  .rrow.past{opacity:.5}
  .rrow.nowrow{background:rgba(224,169,59,.07);border-left:3px solid #e0a93b;border-radius:0}
  .rrt{margin-left:auto;display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:flex-end}
  .nowline{display:flex;align-items:center;gap:8px;padding:7px 2px}
  .nowline .l{flex:1;height:0;border-top:1.5px dashed #e0a93b}
  .nowline b{font-size:11px;color:#e0a93b;font-weight:600;white-space:nowrap}
  .soon{font-size:10px;color:#1a1d12;background:#e0a93b;padding:1px 6px;border-radius:4px;font-weight:700}
  .dhtm{font-size:13px;color:#9aa3b2;font-weight:600}
  .apiline{flex-basis:100%;display:flex;align-items:center;gap:6px;padding-left:38px;font-size:12px;color:#9aa3b2}
  .apilab{font-size:10px;font-weight:700;color:#7fb2ff;background:#14233a;border:0.5px solid #2b4a6f;border-radius:6px;padding:1px 6px}
  .anm{font-size:12px;color:#cdd6e2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:120px}
  .apidiff{font-size:10px;color:#e0a93b;background:#2a2415;border-radius:6px;padding:1px 6px}
  .apilab.ref{color:#cfe0b8;background:#1f2a18;border-color:#4a5a36}
  .predhdr{font-size:15px;font-weight:800;margin:18px 0 2px;padding:6px 10px;border-radius:8px;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
  .predhdr.model{background:#1f2a18;color:#cfe0b8;border-left:4px solid #ffd54a}
  .predhdr.api{background:#15233a;color:#bcd3f2;border-left:4px solid #5dc7e0}
  .predhdr.ref{background:#1f2a18;color:#cfe0b8;border-left:4px solid #ffd54a;margin-top:26px}
  .ctag{font-size:10px;font-weight:700;border-radius:5px;padding:1px 6px;margin-left:5px}
  .tg-hon{color:#bcd3f2;background:#15233a;border:0.5px solid #2b4a6f}
  .tg-std{color:#cfe0b8;background:#1f2a18;border:0.5px solid #4a5a36}
  .tg-ana{color:#e8c08a;background:#2a2415;border:0.5px solid #5a4a23}
  .psub{font-size:11px;font-weight:400;color:#9aa3b2}
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
  .dnav{position:sticky;top:0;z-index:30;display:flex;align-items:center;justify-content:space-between;gap:8px;background:#0f1115;padding:4px 0 6px;margin-bottom:2px;border-bottom:1px solid #1c2029}
  .dnav-r{display:flex;gap:6px;flex:none}
  .pnav{font-size:13px;color:#cdd6e2;background:#1a1f28;border:1px solid #2a2f3a;border-radius:8px;padding:6px 11px;cursor:pointer;white-space:nowrap}
  .pnav:not(:disabled):active{background:#232935}
  .pnav:disabled{opacity:.32;cursor:default}
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
  .tkt{display:inline-block;font-size:11px;font-weight:700;padding:1px 8px;margin:2px 5px 0 0;border-radius:10px;background:#241a33;color:#c9a9f0;border:0.5px solid #4a326e}
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
  .stake{margin-left:8px;font-size:13px;font-weight:800;font-variant-numeric:tabular-nums;color:#43c59e;white-space:nowrap}
  .hitpay{margin-left:6px;font-size:11px;font-weight:700;font-variant-numeric:tabular-nums;color:#43c59e;white-space:nowrap}
  .recpct{margin-left:8px;font-size:12px;font-weight:800;font-variant-numeric:tabular-nums}
  .recpct.rok{color:#43c59e}.recpct.ramb{color:#e0a93b}.recpct.rng{color:#7e8796}
  .st b.rok{color:#43c59e}.st b.ramb{color:#e0a93b}.st b.rng{color:#7e8796}
  .kbadge.bud{color:#43c59e;background:#10241c;border-color:#2f6f55}
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
  .skipb{font-size:11px;font-weight:800;border-radius:8px;padding:2px 8px;display:inline-block;
    background:#3a1a1a;color:#f09595;margin-left:4px}
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
  .tjp{background:#16212b;border:1px solid #2b3a48;border-radius:8px;padding:9px 11px;margin:6px 0 2px;font-size:13px;line-height:1.7}
  .tjp .tjhon{font-weight:800;color:#eef3f8}
  .tjp .tchg{color:#ef7a27;font-weight:700;font-size:12px}
  .tjp .tsame{color:#8ea0ba;font-weight:600;font-size:12px}
  .tjp .tjrk{color:#aebacb;font-size:12px;margin-top:4px}
  /* 常時 左下に小さく表示する「1番上へ戻る」ボタン */
  #toTop{position:fixed;left:12px;bottom:14px;z-index:60;display:inline-flex;align-items:center;gap:3px;
    font-size:11px;font-weight:800;line-height:1;color:#cdd6e2;background:rgba(28,34,44,.86);
    border:0.5px solid #3a4250;border-radius:16px;padding:8px 11px;cursor:pointer;
    -webkit-backdrop-filter:blur(4px);backdrop-filter:blur(4px);box-shadow:0 2px 8px rgba(0,0,0,.45)}
  #toTop:active{background:#2b3441;transform:translateY(1px)}
  #toTop span{font-size:10px;letter-spacing:.5px}
</style></head><body>
<div id="app"></div>
<button id="toTop" type="button" title="1番上へ戻る" aria-label="1番上へ戻る">▲<span>TOP</span></button>
<script>
const D=__DATA__;
const LC={1:['#ffffff','#111111'],2:['#1b1b1b','#ffffff'],3:['#e23b3b','#ffffff'],4:['#2f7fd6','#ffffff'],5:['#f2c025','#111111'],6:['#28a35a','#ffffff']};
let selDate=D.labels[0][1], cur='ALL', sel=null, tab='pred';
let listY=0, backY=null;   // listY=詳細を開く直前の一覧スクロール位置／backY!=null=戻る時に復元する位置
let anaScope='all';   // 穴目回収率の対象: 'all'=合算 / 'haran'=波乱帯 / 'std'=標準帯
let lvlFilter='all';  // 一覧の帯フィルタ: 'all'=すべて / 'tetsu'=鉄板のみ / 'haran'=波乱のみ（全場/場別/リアル共通）
// ライブ更新サーバ(serve_odds.py)がある環境か。file://(→サーバへ誘導)・localhost・LANはtrue。
// クラウド配信(pages.dev等)はfalse＝/updateが無いので「更新」ボタンは隠す（毎朝の自動更新のみ）。
const IS_LIVE=location.protocol==='file:'||/^(localhost$|127\.|0\.0\.0\.0$|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(location.hostname);
// クラウド配信(pages.dev等の http/https かつ非ローカル)。更新ボタンは収集サーバが無いため
// /api/refresh(Pages Function)経由で GitHub Actions(boatrace-update) を起動し、生成される update.json をポーリング反映する。
const IS_CLOUD=(location.protocol==='http:'||location.protocol==='https:')&&!IS_LIVE;
// 「更新」ボタンの状態（当日の展示+結果取得）。upMsg=ステータス表示文字列。
let upMsg='', upErr=false, upBusy=false, upFetched='';
// 場別テーブルの並び替え状態（c='e'(2連単)/'t'(3連単), d=1昇順/-1降順, null=既定）。
let vsort={c:null,d:-1}, rsort={c:null,d:-1};
const root=document.getElementById('app');
// 左下「TOP」ボタン: #app の外にあるので再描画で消えない。1回だけ配線。
// 即時スクロール（smoothは一部環境で無効なため確実な方を使う）。html/body 両対応で0へ。
{const _tt=document.getElementById('toTop');
 if(_tt)_tt.addEventListener('click',()=>{try{window.scrollTo(0,0);}catch(e){}
   document.documentElement.scrollTop=0;document.body.scrollTop=0;});}
const mmdd=s=>s.slice(5);
function chip(w,cls){const a=LC[w];return '<span class="'+(cls||'wk')+'" style="background:'+a[0]+';color:'+a[1]+'">'+w+'</span>';}
// 展示反映後の予想: 朝の p_win(r.b[i][1]/1000) に展示タイム/展示STを軽くブレンド。
// backtest(1032R,7日)で本命1着 55.9%→約57%(+0.5〜1.4pt)。係数は控えめ固定(過適合回避)・
// 大きいβは逆効果を実測。展示が無ければ null（朝予想のまま）。
function tenjiPred(r){
  const e=r&&r.ex; if(!e||!e.time)return null;
  const ts=e.time, st=e.st||[];
  if(!ts.some(x=>x!=null))return null;
  function zs(arr){
    const xs=arr.filter(x=>x!=null&&isFinite(x));
    if(xs.length<2)return arr.map(()=>0);
    const m=xs.reduce((a,b)=>a+b,0)/xs.length;
    const sd=Math.sqrt(xs.reduce((a,b)=>a+(b-m)*(b-m),0)/xs.length)||1;
    return arr.map(x=>(x!=null&&isFinite(x))?(x-m)/sd:0);
  }
  const stEff=st.map(x=>(x!=null&&x<0)?0.30:x);   // 展示ST: F(負)は遅い扱い
  const zt=zs(ts), zst=zs(stEff), BT=0.2, BS=0.1;
  let sc=[],mx=-1e9;
  for(let i=0;i<6;i++){
    const p=((r.b[i]?r.b[i][1]:0)||1)/1000;
    sc[i]=Math.log(Math.max(p,1e-9))-BT*zt[i]-BS*zst[i];
    if(sc[i]>mx)mx=sc[i];
  }
  const e2=sc.map(s=>Math.exp(s-mx)), sm=e2.reduce((a,b)=>a+b,0)||1;
  const prob=e2.map(x=>x/sm);
  const order=prob.map((p,i)=>[p,i]).sort((a,b)=>b[0]-a[0]).map(x=>x[1]);
  return {prob:prob, order:order, top:order[0]};
}
// 買い目スコア: 展示(r.ex)があれば展示反映後の確率(tenjiPred)、無ければ朝の学習モデル p_win(per-mille)。
// backtest(全K-file展示タイム・27,660R)で買い目を再ランクすると総回収+0.9〜1.3pt(OOSでも正・過適合なし)。
// ヘッドラインの本命/順位/1着確率は朝予想のまま（直下の exView が展示後本命を別途表示）。
function betScore(r){
  const tp=tenjiPred(r);
  return (tp&&tp.prob)?tp.prob.slice():r.b.map(x=>x[1]);
}
function dayRaces(){return D.races.filter(r=>r.d===selDate);}
function hasResult(r){return r.b.some(x=>x[2]===1);}  // 1着が決まっていれば結果あり（F/失格混在でも可）
function finishOrder(r){return r.b.map((b,i)=>[i+1,b[2]]).filter(x=>x[1]).sort((a,b)=>a[1]-b[1]).map(x=>x[0]);}
function eqArr(a,b){return a&&b&&a.length===b.length&&a.every((v,i)=>v===b[i]);}
function plTop(s,kind,k){const idx=[0,1,2,3,4,5].filter(i=>s[i]>0);const tot=s.reduce((a,b)=>a+b,0);const out=[];
  if(kind===2){for(const i of idx)for(const j of idx){if(j===i)continue;out.push([[i+1,j+1],s[i]/tot*s[j]/(tot-s[i])]);}}
  else{for(const i of idx)for(const j of idx){if(j===i)continue;for(const l of idx){if(l===i||l===j)continue;out.push([[i+1,j+1,l+1],s[i]/tot*s[j]/(tot-s[i])*s[l]/(tot-s[i]-s[j])]);}}}
  out.sort((a,b)=>b[1]-a[1]);return out.slice(0,k);}

// 2連複: PL確率を順不同ペアに畳んだ上位k点。[[a,b](昇順), prob] の配列。
// 2026-07-07 券種切替（2連単→2連複・点数kExは同一）: backtest 26,843R で
// 回収78.4→82.1%(+3.7pt)・的中52→68%。同回収帯で分散が小さい買い方。
function plTopF(s,k){
  const m={};
  plTop(s,2,30).forEach(c=>{const a=Math.min(c[0][0],c[0][1]),b=Math.max(c[0][0],c[0][1]);m[a+'-'+b]=(m[a+'-'+b]||0)+c[1];});
  const out=Object.keys(m).map(key=>[key.split('-').map(Number),m[key]]);
  out.sort((x,y)=>y[1]-x[1]);
  return out.slice(0,k);
}
// 順不同ペアの一致（2連複の的中判定）。c/act は [枠,枠]。
function eqPair(c,act){return c&&act&&c.length===2&&act.length===2&&((c[0]===act[0]&&c[1]===act[1])||(c[0]===act[1]&&c[1]===act[0]));}
// API確率→実1着率の較正（表示専用・区分線形）。D.cal はビルド時 isotonic/PAV の節点。
// 検証(OOS 5-6月): API確率は高域+11〜12pt過小・0.35-0.45は過大。帯判定/点数/買い目は生値のまま。
function calP(p){
  const K=D.cal; if(!K||!K.length)return p;
  let lo=K[0],hi=K[K.length-1];
  for(let i=0;i<K.length;i++){ if(K[i][0]<=p)lo=K[i]; else {hi=K[i];break;} }
  if(hi[0]<=lo[0])return lo[1];
  return lo[1]+(hi[1]-lo[1])*(p-lo[0])/(hi[0]-lo[0]);
}
// 予想確率（本命確率hon=0-1・生値）に応じた買目点数。堅い→少点/荒れ→多点。上限 2連複3/3連単20。
// 2連複は2026-07-16に上限5→3へ縮小（backtestで回収率は1〜3点が頭打ち・4点以降は単調減）。
function kEx(hon){return hon>=0.65?1:hon>=0.50?2:3;}
function kTri(hon){return hon>=0.65?3:hon>=0.50?7:hon>=0.40?10:hon>=0.30?14:20;}  // 鉄板=3点(2026-07-06 4点目カット・回収+1.6pt)
// 穴帯(本命<0.45)は3連単を買わない。穴の3連単は回収72.9%(資金を溶かす主犯)、2連単は83.4%で
// 下支え。穴3連単のみ停止で全体回収率 77.4%→78.2%(+0.8pt)・賭け金▲16%(backtest no_ana_tri)。
function triOn(hon){return hon>=0.45;}
// 本命確率/穴確率/荒れ度/穴筆頭。s=per-mille p_win 配列。
// 本命=モデル1番手(p_win最大)。穴=モデル順位4-6(=軽視された艇)の1着。
// 検証(29,233R OOS): 本命確率はキャリブ良好、穴率はΣp_win(4-6)とほぼ一致(平均13%)。
// モーター/直近はp_winに織り込み済み＝確率を超える穴シグナルは無い→材料は脅威の目安として表示。
// s=per-mille p_win 配列（従来 r.b の[1] でも API r.ab でも共用）。
function honAnaS(s){
  const idx=[0,1,2,3,4,5].sort((a,b)=>s[b]-s[a]);   // 予想順位降順
  const hon=s[idx[0]]/1000;
  const ana=(s[idx[3]]+s[idx[4]]+s[idx[5]])/1000;    // 順位4-6の合計
  const lvl = hon>=0.65?['鉄板','tetsu'] : hon<0.45?['波乱含み','haran'] : ['標準','std'];
  // 表示用の較正値（実測1着率ベース）。帯判定(lvl)・点数は生値 hon のまま。
  const honC=Math.min(1,calP(hon));
  const anaC=Math.min(1,calP(s[idx[3]]/1000)+calP(s[idx[4]]/1000)+calP(s[idx[5]]/1000));
  return {hon,ana,honC,anaC,lvl:lvl[0],lvlcls:lvl[1],hmLane:idx[0]+1,anaLane:idx[3]+1};
}
function honAna(r){return honAnaS(r.ab);}            // 主系統＝API（割合・荒れ度・穴の基準）
// 対抗1艇（穴）＝本命を食う可能性が最も高い1艇＋その根拠タグ。
// ★検証(残差テスト OOS 6,497R): 隣接艇の弱さ・隣ST・捲り屋×高機は【すべて p_win に織込済】＝残差≈0。
//   よって対抗の最尤艇＝betScore（展示反映後）の2番手で、追加で当てる力は無い（本命飛び時44%が1着）。
//   タグは「なぜ対抗か」の説明であって独立した予測力ではない（回収率は本命軸に劣る夢枠）。
function taikou(r){
  const s=betScore(r);
  const order=s.map((p,i)=>[p,i]).sort((a,b)=>b[0]-a[0]).map(x=>x[1]);
  const fav=order[0], ci=order[1];
  const tot=s.reduce((a,b)=>a+b,0)||1, prob=s[ci]/tot;
  const tags=[];
  const ft=r.ft?r.ft[ci]:null;
  if(ft&&ft[0]!=null&&ft[0]<=2)tags.push(['高機','モーターがレース内'+Math.round(ft[0])+'位']);
  if(ft&&ft[2]!=null&&ft[2]<=2)tags.push(['ST速','スタート評価がレース内'+Math.round(ft[2])+'位']);
  // 隣接（枠 ci-1 / ci+1）が両方とも格下（API 1着確率で下位）＝両サイドが弱い
  const abrank={};r.ab.map((p,i)=>[p,i]).sort((a,b)=>b[0]-a[0]).forEach((x,k)=>{abrank[x[1]]=k+1;});
  const neigh=[ci-1,ci+1].filter(i=>i>=0&&i<6);
  if(neigh.length&&neigh.every(i=>abrank[i]>=4))tags.push(['隣弱','両隣が格下（'+neigh.map(i=>(i+1)+'号艇').join('・')+'）']);
  // 捲り屋（勝ち星の35%以上がまくり系）
  if(r.mk&&r.mk[ci]!=null&&r.mk[ci]>=0.35)tags.push(['捲り屋','勝ち星の'+Math.round(r.mk[ci]*100)+'%がまくり系']);
  // 展示（当日 previews のみ）
  let exUsed=false;
  if(r.ex&&r.ex.time){
    const ts=r.ex.time, valid=ts.map((t,i)=>[t,i]).filter(x=>x[0]!=null);
    if(valid.length>=2){exUsed=true;
      const trank={};valid.slice().sort((a,b)=>a[0]-b[0]).forEach((x,k)=>{trank[x[1]]=k+1;});
      if(trank[ci]&&trank[ci]<=2)tags.push(['展示良','展示タイムがレース内'+trank[ci]+'位']);
    }
    if(r.ex.tilt&&r.ex.tilt[ci]!=null&&r.ex.tilt[ci]>=0.5)tags.push(['チルト↑','チルト'+r.ex.tilt[ci]+'（跳ね・参考）']);
  }
  return {fav:fav+1, lane:ci+1, name:r.b[ci][0], prob:prob, tags:tags, ex:exUsed};
}
// 買い目配分の「爆発重視」重み。全帯 weight∝p^-1（EVフラット）で薄い高配当目に振り切る。
// 合成回収はほぼ不変(-0.1pt)だが≥5倍の爆発頻度・単レース最大配当が大きく増える（穴帯のみ回収-2.5pt）。
function meriW(probs,hon){const g=-1;return probs.map(p=>Math.pow(Math.max(p,1e-12),g));}
// 予算budget円を買い目に重み比例で配分（¥100単位・各点最低¥100・合計=budget）。
function allocYen(probs,budget){
  const unit=100,n=probs.length; if(!n)return [];
  let units=new Array(n).fill(1);                 // 各点 最低1ユニット(¥100)
  let rest=Math.round(budget/unit)-n;             // 残りユニットを確率比例で上積み
  if(rest>0){
    const tot=probs.reduce((a,b)=>a+b,0)||1;
    const raw=probs.map(p=>p/tot*rest);
    const add=raw.map(x=>Math.floor(x));
    let r=rest-add.reduce((a,b)=>a+b,0);
    const ord=raw.map((x,i)=>[i,x-Math.floor(x)]).sort((a,b)=>b[1]-a[1]);
    for(let k=0;k<r;k++)add[ord[k%n][0]]++;        // 端数は小数部の大きい順
    for(let i=0;i<n;i++)units[i]+=add[i];
  }
  return units.map(u=>u*unit);
}
// 枠 -> 予想順位（1=最上位）。穴型判定に使う。sArr=per-mille配列。
function laneRankMap(sArr){const o=[0,1,2,3,4,5].slice().sort((a,b)=>sArr[b]-sArr[a]);const m={};o.forEach((i,rk)=>m[i+1]=rk+1);return m;}
// 買い目の型: 含む枠の最下位順位で 本命型(≤3)/標準型(=4)/穴型(≥5)。標準帯の3タイプ提示に使う。
function comboKind(combo,rankMap){const mx=Math.max.apply(null,combo.map(w=>rankMap[w]));return mx>=5?['穴型','tg-ana']:mx>=4?['標準型','tg-std']:['本命型','tg-hon'];}
// 3連単の購入買い目: 標準帯(0.45-0.65)は穴型(5-6番手絡み)を購入対象から外す（穴型は参考表示のみ・買わない）。
function triBuyList(allCombos,k,hon,rankMap){
  if(hon>=0.45&&hon<0.65)return allCombos.filter(c=>comboKind(c[0],rankMap)[0]!=='穴型').slice(0,k);
  return allCombos.slice(0,k);
}
// 標準帯の穴型（購入しない・参考表示用）。確率上位を最大n点。
function triAnaRef(allCombos,rankMap,n){return allCombos.filter(c=>comboKind(c[0],rankMap)[0]==='穴型').slice(0,n||3);}
// 穴候補(API4番人気)をアタマに置いた3連単の参考（買わない穴目）。aArr=API per-mille配列。
// 穴候補→上位3艇(本命/2番手/3番手)の2-3着流し＝6点。
// backtest(25,017R): 的中3.9%・回収72%(穴帯79%/穴候補API≥12%で80%)・平均配当¥11,000。
// 軽視艇アタマで当たれば万舟級＝穴の本線。
function anaCandRef(aArr){
  const idx=[0,1,2,3,4,5].slice().sort((a,b)=>aArr[b]-aArr[a]);   // API降順index
  const c=idx[3]+1;                                              // 穴候補=4番人気
  const T=[idx[0]+1,idx[1]+1,idx[2]+1];                          // 相手=上位3艇(本命/2番手/3番手)
  const out=[];
  for(const a of T)for(const b of T)if(a!==b)out.push([c,a,b]);  // 穴候補アタマ 6点
  return out;
}
// 対抗1艇(betScore=展示反映後の2番手)をアタマに置いた3連単6点。相手=本命/3番手/4番手の2-3着流し。
// 穴帯の穴予想はこちらを採用（backtest 26,135R 標準+穴の帯別運用: 穴帯 対抗6点=回収88.2%/的中15.3%
//  ＞穴候補6点79.7%。標準帯は穴候補6点73.3%が上位）。※betScore は展示無ければ朝の学習モデル。
function taikouRef(r){
  const s=betScore(r);
  const o=[0,1,2,3,4,5].slice().sort((a,b)=>s[b]-s[a]);          // betScore降順index
  const head=o[1]+1;                                            // 対抗=2番手
  const T=[o[0]+1,o[2]+1,o[3]+1];                               // 相手=本命/3番手/4番手
  const out=[];
  for(const a of T)for(const b of T)if(a!==b)out.push([head,a,b]); // 対抗アタマ 6点
  return out;
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
  const s=r.b.map(x=>x[1]);                            // 学習モデル順位で本命/中位/穴を判定
  const win=finishOrder(r)[0];
  const rank=[0,1,2,3,4,5].sort((a,b)=>s[b]-s[a]).indexOf(win-1)+1;   // 1=本命
  if(rank===1)return {lab:'本命',cls:'honhit',rank};
  if(rank>=4)return {lab:'穴',cls:'anahit',rank};
  return {lab:'中位',cls:'midhit',rank};
}

// 会場コード→色（[文字色, 背景, 枠]）。リアル（時刻順）で会場を見分けるための固定色。
const VC=[['#7fb2ff','rgba(47,127,214,.16)','rgba(47,127,214,.45)'],
  ['#5dcaa5','rgba(29,158,117,.16)','rgba(29,158,117,.45)'],
  ['#e0c07a','rgba(224,169,59,.15)','rgba(224,169,59,.45)'],
  ['#aaa2f0','rgba(127,119,221,.2)','rgba(127,119,221,.5)'],
  ['#ed93b1','rgba(212,83,126,.16)','rgba(212,83,126,.45)'],
  ['#f0997b','rgba(216,90,48,.16)','rgba(216,90,48,.45)'],
  ['#97c459','rgba(99,153,34,.18)','rgba(99,153,34,.5)'],
  ['#5dc7e0','rgba(93,199,224,.15)','rgba(93,199,224,.45)'],
  ['#f09595','rgba(226,75,74,.15)','rgba(226,75,74,.45)'],
  ['#c7c3b6','rgba(180,178,169,.14)','rgba(180,178,169,.4)']];
function venueColor(c){return VC[(parseInt(c,10)||0)%VC.length];}
// 結果ありレースが「的中」か（2連複≤kEx か 3連単買い目に決着が入った）。一覧の的中表示と共通基準。
function isHit(r){
  if(!hasResult(r))return null;
  const s=betScore(r);const ord=finishOrder(r);   // 買い目＝展示反映後（展示無ければ朝の学習モデル）
  const hon=Math.max(...r.ab)/1000;const nEx=kEx(hon),nTri=kTri(hon);
  const ex=ord.slice(0,2),tri=ord.slice(0,3);
  const exHit=ex.length>=2&&plTopF(s,nEx).some(c=>eqPair(c[0],ex));
  const triHit=triOn(hon)&&tri.length>=3&&triBuyList(plTop(s,3,200),nTri,hon,laneRankMap(s)).some(c=>eqArr(c[0],tri));
  return exHit||triHit;
}
// 非完走（フライング等）艇＝着順なし。フライングは買い目が返還される＝賭け金は損失でなく戻る。
function flySet(r){const f={};r.b.forEach((b,i)=>{if(!b[2])f[i+1]=1;});return f;}
// 1日分の集計（的中率/投資/回収/回収率/F返還レース数）。買い目＝サイト本体と同一
// （betScore＝展示反映後・確率連動点数・各¥2,000配分・穴帯3連単は見送り）。
// 返還: 非完走艇を含む買い目はその賭け金を投資から除外（損失にしない）。
// 当日は update.json 反映後の D.races を使うので、なるべく最新状態を表す。
function daySummary(date){
  let nDone=0,nHit=0,inv=0,ret=0,nF=0;
  // 穴予想（帯別方式・参考シミュ・各¥100フル・F返還・3連単のみ）:
  //   標準帯(0.45-0.65)=穴候補6点(anaCandRef) / 波乱帯(<0.45)=対抗6点(taikouRef)。詳細画面と一致。
  let sN=0,sInv=0,sRet=0;   // 標準帯（穴候補6点）
  let hN=0,hInv=0,hRet=0;   // 波乱帯（対抗6点）
  D.races.forEach(r=>{
    if(r.d!==date||!hasResult(r))return;
    nDone++;
    const fly=flySet(r);if(Object.keys(fly).length)nF++;
    const s=betScore(r);const ord=finishOrder(r);
    const hon=Math.max(...r.ab)/1000;
    const actEx=ord.slice(0,2),actTri=ord.slice(0,3);
    let hit=false;
    const ex=plTopF(s,kEx(hon));const exYen=allocYen(meriW(ex.map(c=>c[1]),hon),2000);   // 2連複
    ex.forEach((c,i)=>{
      const kept=!c[0].some(w=>fly[w]);
      if(kept)inv+=exYen[i];                                   // 返還ぶんは投資から除外
      if(actEx.length>=2&&eqPair(c[0],actEx)){const pay=(r.po&&r.po[2]!=null)?Math.round(r.po[2]*exYen[i]/100):0;ret+=pay;hit=true;}
    });
    // 3連単: 実際に買うのは triOn(hon)=本命確率45%以上のみ（穴帯は見送り）。
    const doTri=triOn(hon);
    if(doTri){
      const tri=triBuyList(plTop(s,3,200),kTri(hon),hon,laneRankMap(s));
      const triYen=allocYen(meriW(tri.map(c=>c[1]),hon),2000);
      tri.forEach((c,i)=>{
        const kept=!c[0].some(w=>fly[w]);
        if(kept)inv+=triYen[i];
        if(actTri.length>=3&&eqArr(c[0],actTri)){
          const pay=r.po?Math.round(r.po[1]*triYen[i]/100):0;
          ret+=pay;hit=true;
        }
      });
    }
    // 穴予想（帯別方式・参考シミュ）＝標準帯は穴候補6点／波乱帯は対抗6点をアタマ3連単で
    // フル購入した場合の回収率。各¥100均等・F返還・3連単のみ。詳細画面の穴予想と同じ買い方。
    const tally=(combos,onInv,onRet)=>combos.forEach(c=>{
      const kept=!c.some(w=>fly[w]);
      if(kept)onInv(100);
      if(kept&&actTri.length>=3&&eqArr(c,actTri))onRet(r.po?r.po[1]:0);
    });
    if(hon>=0.45&&hon<0.65){sN++;tally(anaCandRef(r.ab),v=>sInv+=v,v=>sRet+=v);}
    else if(hon<0.45){hN++;tally(taikouRef(r),v=>hInv+=v,v=>hRet+=v);}
    if(hit)nHit++;
  });
  return {nDone,nHit,inv,ret,nF, sN,sInv,sRet, hN,hInv,hRet};
}
// 最上部サマリー: 当日/前日/前々日 の 的中率・投資・回収・回収率 を横並び（当日は最新反映）。
function summaryBar(){
  const cols=D.labels.map(l=>({lab:l[0],d:l[1],s:daySummary(l[1])}));
  if(!cols.some(c=>c.s.nDone))return '';     // まだ結果が1つも無ければ出さない
  const pct=(a,b)=>b?Math.round(a/b*100):0;
  const yen=v=>'¥'+Math.round(v).toLocaleString();
  const recCls=r=>r>=100?'rok':(r>0?'ramb':'rng');
  let h='<div class="sumbar"><div class="sumttl">的中率・回収率'
    +((IS_LIVE||IS_CLOUD)?'（当日は最新反映）':'')
    +'　買い目を各¥2,000配分・フライングは返還</div>';
  h+='<table class="sumt"><thead><tr><th></th>';
  cols.forEach(c=>h+='<th>'+c.lab+'<small>'+mmdd(c.d)+'</small></th>');
  h+='</tr></thead><tbody>';
  const cellY=(c,v)=>'<td>'+(c.s.nDone?yen(v):'–')+'</td>';
  h+='<tr><td class="rl">的中率</td>';
  cols.forEach(c=>h+='<td>'+(c.s.nDone?pct(c.s.nHit,c.s.nDone)+'%<small>'+c.s.nHit+'/'+c.s.nDone+'</small>':'–')+'</td>');
  h+='</tr><tr><td class="rl">投資</td>';
  cols.forEach(c=>h+=cellY(c,c.s.inv));
  h+='</tr><tr><td class="rl">回収</td>';
  cols.forEach(c=>h+=cellY(c,c.s.ret));
  h+='</tr><tr><td class="rl">回収率</td>';
  cols.forEach(c=>{const rr=pct(c.s.ret,c.s.inv);h+='<td>'+(c.s.nDone?'<b class="'+recCls(rr)+'">'+rr+'%</b>':'–')+'</td>';});
  // 穴目回収率（選択式: 合算/波乱帯/標準帯）。帯別方式＝標準帯 穴候補6点 / 波乱帯 対抗6点。
  const scopeOf=s=>anaScope==='haran'?{inv:s.hInv,ret:s.hRet,n:s.hN}
                  :anaScope==='std'?{inv:s.sInv,ret:s.sRet,n:s.sN}
                  :{inv:s.sInv+s.hInv,ret:s.sRet+s.hRet,n:s.sN+s.hN};
  const scLab={all:'合算',haran:'波乱帯',std:'標準帯'};
  h+='</tr><tr><td class="rl">穴目回収率<small>3単</small></td>';
  cols.forEach(c=>{const a=scopeOf(c.s);const rr=pct(a.ret,a.inv);h+='<td>'+(a.inv?'<b class="'+recCls(rr)+'">'+rr+'%</b><small>'+a.n+'R</small>':'–')+'</td>';});
  h+='</tr></tbody></table>';
  h+='<div class="anascope">穴予想の対象：'
    +['all','haran','std'].map(k=>'<button class="asb'+(anaScope===k?' on':'')+'" data-as="'+k+'">'+scLab[k]+'</button>').join('')+'</div>';
  const foot={
    all:'「穴目回収率」＝標準帯(本命45-65%)は穴候補6点／波乱帯(本命45%未満)は対抗6点をアタマに置いた3連単6点をフル購入した場合の<b>合算</b>回収率（参考シミュ・各¥100均等・F返還・3連単のみ・詳細画面の穴予想と同じ買い方）。',
    haran:'「穴目回収率／波乱帯」＝本命45%未満のレースで穴予想＝<b>対抗</b>（betScore2番手）をアタマに置いた3連単6点をフル購入した場合の回収率（参考シミュ・各¥100均等・F返還・3連単のみ）。',
    std:'「穴目回収率／標準帯」＝本命45-65%のレースで穴予想＝<b>穴候補</b>（API4番人気）をアタマに置いた3連単6点をフル購入した場合の回収率（参考シミュ・各¥100均等・F返還・3連単のみ）。'};
  h+='<div class="sumf">'+foot[anaScope]+'</div>';
  const fcols=cols.filter(c=>c.s.nF);
  if(fcols.length)h+='<div class="sumf">F返還：'+fcols.map(c=>c.lab+' '+c.s.nF+'R').join(' / ')
    +'（非完走艇を含む買い目は投資から除外）</div>';
  h+='</div>';
  return h;
}
// リアル＝全会場を締切時刻順に1列表示。現在時刻の直近レースをハイライトし自動スクロール。
function realList(rs){
  const tmv=s=>{if(!s)return 1e9;const p=s.split(':');return (+p[0])*60+(+p[1]);};
  const order=rs.map((r,i)=>i).filter(i=>lvlFilter==='all'||honAna(rs[i]).lvlcls===lvlFilter)
    .sort((a,b)=>tmv(rs[a].tm)-tmv(rs[b].tm));   // 帯フィルタ（鉄板/波乱）はリアルにも適用
  const isToday=selDate===D.base;
  const now=new Date();const nowMin=now.getHours()*60+now.getMinutes();
  const hhmm=String(now.getHours()).padStart(2,'0')+':'+String(now.getMinutes()).padStart(2,'0');
  let tgt=-1;
  if(isToday){for(const i of order){if(rs[i].tm&&tmv(rs[i].tm)>=nowMin){tgt=i;break;}}}
  let h='<div class="meta">締切時刻順・'+(lvlFilter==='all'?'全':'絞込 ')+order.length+'レース・タップで詳細'
    +(isToday&&tgt>=0?'・<span style="color:#e0a93b">現在 '+hhmm+'</span> の直近へ自動スクロール':'')+'</div>';
  let nowShown=false;
  for(const i of order){
    const r=rs[i];
    if(isToday&&tgt>=0&&!nowShown&&i===tgt){
      h+='<div class="nowline"><span class="l"></span><b>&#9660; 現在 '+hhmm+'</b><span class="l"></span></div>';
      nowShown=true;
    }
    const ps=r.b.map(x=>x[1]);let hm=0;for(let w=1;w<6;w++)if(ps[w]>ps[hm])hm=w;
    const ha=honAna(r);const done=hasResult(r);const vc=venueColor(r.c);
    const past=isToday&&r.tm&&tmv(r.tm)<nowMin;
    h+='<div class="row rrow'+(i===tgt?' nowrow':'')+(past?' past':'')+'" data-i="'+i+'"'+(i===tgt?' id="nowtarget"':'')+'>'
      +'<span class="rtm2">'+(r.tm||'--:--')+'</span>'
      +'<span class="vpill" style="color:'+vc[0]+';background:'+vc[1]+';border-color:'+vc[2]+'">'+r.v+'</span>'
      +'<span class="rno">'+r.no+'R</span>'+chip(hm+1)
      +'<span class="nm">'+r.b[hm][0]+'</span>'+(r.mz?'<span class="wn">&#9888;</span>':'');
    if(done){
      const hit=isHit(r);const shobu=r.ev!=null&&r.ev>=1.5;
      const lvlTag=ha.lvlcls!=='std'?'<span class="lvl '+ha.lvlcls+'">'+ha.lvl+'</span>':'';  // 鉄板/波乱含みは残す・標準は出さない
      h+='<span class="rrt">'+lvlTag
        +(shobu?'<span class="prize">&#127919;勝負</span>':'')
        +(hit?'<span class="ok">的中</span>':'<span class="ng">不的中</span>')+'</span>';
    }else{
      const shobu=r.ev!=null&&r.ev>=1.5;
      const to=tetsuOdds(r);   // 鉄板×実オッズ<2.0＝見送り推奨（オッズ未取得は非表示）
      h+='<span class="rrt"><span class="lvl '+ha.lvlcls+'">'+ha.lvl+'</span>'
        +(i===tgt?'<span class="soon">まもなく</span>':'')
        +(shobu?'<span class="prize">&#127919;勝負</span>':'')
        +(to&&to.skip?'<span class="skipb">&#9888;見送り</span>':'')
        +'<span class="hp">本命<b>'+Math.round(ha.honC*100)+'</b> 穴<b class="a">'+Math.round(ha.anaC*100)+'</b></span></span>';
    }
    h+='<span class="chev">&rsaquo;</span></div>';
  }
  if(!order.length)h+='<div class="meta" style="margin-top:14px">'+(lvlFilter==='tetsu'?'鉄板':'波乱')+'のレースはありません</div>';
  return h;
}
function listView(){
  const rs=dayRaces();
  const lab=D.labels.find(l=>l[1]===selDate);
  let h=summaryBar()+'<div class="dsel">';
  for(const l of D.labels)h+='<button class="dbtn'+(selDate===l[1]?' on':'')+'" data-d="'+l[1]+'">'+l[0]+'<small>'+mmdd(l[1])+'</small></button>';
  h+='</div>';
  h+='<div class="meta">直前情報なしモデル（朝の出走表のみ）・ '+rs.length+'レース ・ タップで詳細'
    +(hasResult(rs[0]||{b:[]})?' ・ 結果あり（的中=2連複/3連単の変動上位に決着 / 下段に3連単の決着と配当）':'')+'</div>';
  const venues=[];const seen={};for(const r of rs){if(!seen[r.c]){seen[r.c]=1;venues.push([r.c,r.v]);}}
  const isBase=selDate===D.base, anytm=rs.some(r=>r.tm);
  h+='<div class="vfilter"><button class="vbtn'+(cur==='ALL'?' on':'')+'" data-v="ALL">全場</button>';
  if(isBase&&anytm)h+='<button class="vbtn rt'+(cur==='REAL'?' on':'')+'" data-v="REAL"><span class="livedot"></span>リアル</button>';
  for(const a of venues)h+='<button class="vbtn'+(cur===a[0]?' on':'')+'" data-v="'+a[0]+'">'+a[1]+'</button>';
  h+='</div>';
  // 帯フィルタ（鉄板/波乱）: 荒れ度の帯で一覧を絞る。件数はその日の全場ベース。
  const nT=rs.filter(r=>honAna(r).lvlcls==='tetsu').length, nH=rs.filter(r=>honAna(r).lvlcls==='haran').length;
  h+='<div class="vfilter" style="margin-top:2px">'
    +'<button class="lvbtn'+(lvlFilter==='all'?' on':'')+'" data-lv="all">すべて</button>'
    +'<button class="lvbtn tetsu'+(lvlFilter==='tetsu'?' on':'')+'" data-lv="tetsu">鉄板 '+nT+'</button>'
    +'<button class="lvbtn haran'+(lvlFilter==='haran'?' on':'')+'" data-lv="haran">波乱 '+nH+'</button></div>';
  if(cur==='REAL')return h+realList(rs);
  const shownV={};let shown=0;   // 場見出し＝表示される最初のレースで出す（フィルタ対応）
  rs.forEach((r,gi)=>{
    if(cur!=='ALL'&&cur!==r.c)return;
    if(lvlFilter!=='all'&&honAna(r).lvlcls!==lvlFilter)return;
    shown++;
    if(!shownV[r.c]){shownV[r.c]=1;h+='<h3>'+r.v+'<span class="vmeta"><span class="grd'+(r.g&&r.g!=='一般'?' hi':'')+'">'+(r.g||'一般')+'</span>'+(r.day?'第'+r.day+'日':'')+'</span></h3>';}
    const ps=r.b.map(x=>x[1]);let hm=0;for(let w=1;w<6;w++)if(ps[w]>ps[hm])hm=w;   // 本命＝学習モデル1番手
    const ha=honAna(r);const done=hasResult(r);
    h+='<div class="row'+(done?' done':'')+'" data-i="'+gi+'"><span class="rno">'+r.no+'R</span>'
     +(r.tm?'<span class="rtm">'+r.tm+'</span>':'')+chip(hm+1)
     +'<span class="nm">'+r.b[hm][0]+'</span>'+(r.mz?'<span class="wn">&#9888;</span>':'');
    const chance = ha.hon<0.35 && ha.ana>=0.25;            // 本命弱い×穴厚い＝コメントチャンス
    const shobu = r.ev!=null && r.ev>=1.5;  // EV≥1.5で点灯（荒れ度の帯は問わず・オッズ取得後）
    const to = done?null:tetsuOdds(r);      // 鉄板×実オッズ<2.0＝見送り推奨（締切前のみ）
    h+='<span class="ha2"><span class="lvl '+ha.lvlcls+'">'+ha.lvl+'</span>'
        +(shobu?'<span class="prize">&#127919;勝負</span>':'')
        +(to&&to.skip?'<span class="skipb">&#9888;見送り</span>':'')
        +(chance?'<span class="chance">&#10024;チャンス</span>':'')
      +'<span class="hp">本命<b>'+Math.round(ha.honC*100)+'</b> 穴<b class="a">'+Math.round(ha.anaC*100)+'</b></span></span>';
    if(done){
      const s=betScore(r);const ord=finishOrder(r);      // 買い目＝展示反映後（展示無ければ朝の学習モデル）
      const hon=Math.max(...r.ab)/1000;const nEx=kEx(hon),nTri=kTri(hon);   // 点数＝API本命確率
      const ex=ord.slice(0,2);const tri=ord.slice(0,3);
      const exHit=ex.length>=2&&plTopF(s,nEx).some(c=>eqPair(c[0],ex));     // 2連複
      const triBuy=triBuyList(plTop(s,3,200),nTri,hon,laneRankMap(s)); // 標準帯は穴型を購入対象外
      const triHit=triOn(hon)&&tri.length>=3&&triBuy.some(c=>eqArr(c[0],tri)); // 穴帯は3連単を買わない
      const anaRef=!triOn(hon)?taikouRef(r):null;        // 穴帯は本線3連単を見送り→穴予想(対抗6点)の的中を表示
      const anaHit=!!(anaRef&&tri.length>=3&&anaRef.some(c=>eqArr(c,tri)));
      const hit=exHit||triHit;                           // 2連複/3連単の変動上位に決着が入れば的中（ヘッダは本線ベース）
      const pay=r.po?r.po[1]:null;const pay2=(r.po&&r.po.length>2)?r.po[2]:null;   // 2連複配当
      h+='<span class="res">'+(hit?'<span class="ok">的中</span>':'<span class="ng">不的中</span>')+'</span>'
        +'<span class="chev">&rsaquo;</span>';
      h+='<div class="resline"><span class="rll">2連複</span>';
      ex.forEach((w,i)=>{h+=(i?'<span class="arr">=</span>':'')+chip(w,'mc');});
      if(exHit)h+='<span class="ok" style="font-size:11px">的中</span>';
      h+='<span class="yen'+(exHit?' hit':'')+'">'+(pay2!=null?'¥'+pay2.toLocaleString():'配当 –')+'</span></div>';
      h+='<div class="resline"><span class="rll">3連単</span>';
      tri.forEach((w,i)=>{h+=(i?'<span class="arr">&rarr;</span>':'')+chip(w,'mc');});
      if(triHit)h+='<span class="ok" style="font-size:11px">的中</span>';
      if(anaRef)h+='<span style="font-size:11px;margin-left:4px">'+(anaHit?'<span class="ok" style="color:#c79bff">穴予想的中</span>':'<span style="color:#7e8796">穴予想=圏外（本線見送り）</span>')+'</span>';
      h+='<span class="yen'+((triHit||anaHit)?' hit':'')+'">'+(pay!=null?'¥'+pay.toLocaleString():'配当 –')+'</span></div>';
    }else{
      h+='<span class="chev">&rsaquo;</span>';
    }
    h+='</div>';
  });
  if(!shown)h+='<div class="meta" style="margin-top:14px">'+(lvlFilter==='tetsu'?'鉄板':'波乱')+'のレースはありません</div>';
  return h;
}

// 回収率バッジ（券種ヘッダー用）。rec=null→非表示, 0→灰, <100→琥珀, ≥100→緑。
function recBadge(rec){if(rec==null)return '';const c=rec>=100?'rok':(rec>0?'ramb':'rng');return '<span class="recpct '+c+'">回収'+rec+'%</span>';}
function detailView(r){
  const ps=r.b.map(x=>x[1]);const s=ps;const mx=Math.max(...ps);   // 本命/順位/1着確率＝朝の学習モデル（ヘッドライン）
  const _tp=tenjiPred(r);const sb=_tp?_tp.prob.slice():ps;const exOn=!!_tp;   // 買い目＝展示反映後（exOn=展示あり・backtest+0.9〜1.3pt）
  const honBand=Math.max(...r.ab)/1000;                            // 荒れ度/点数の基準＝API本命確率(backtestでAPI据置)
  const done=hasResult(r);const ord=done?finishOrder(r):null;
  const actEx=(done&&ord.length>=2)?ord.slice(0,2):null, actTri=(done&&ord.length>=3)?ord.slice(0,3):null;
  const payEx=(r.po&&r.po.length>2)?r.po[2]:null, payTri=r.po?r.po[1]:null;   // 実配当（2連複/3連単・100円あたり）。的中行に配当・回収率を表示。
  let hm=0;for(let w=1;w<6;w++)if(s[w]>s[hm])hm=w;
  const _nR=dayRaces().length;   // 前/次レース＝同じ日付内をインデックス移動（締切順）
  let h='<div class="dnav">'
    +'<button class="back">&lsaquo; 一覧へ戻る</button>'
    +'<div class="dnav-r">'
    +'<button class="pnav prev"'+(sel<=0?' disabled':'')+'>&lsaquo; 前のレース</button>'
    +'<button class="pnav next"'+(sel>=_nR-1?' disabled':'')+'>次のレース &rsaquo;</button>'
    +'</div></div>';
  h+='<div class="dh">'+r.v+' '+r.no+'R'+(r.tm?' <span class="dhtm">'+r.tm+' 締切</span>':'')+'</div>';
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
  h+='<div class="predhdr api">荒れ度の基準＝API簡易合成<span class="psub">勝率＋枠＋機力（鉄板/標準/穴・点数の判定に使用）</span></div>';
  h+='<div class="sec">本命確率 / 穴確率<span class="kbadge" title="過去の実測1着率にもとづく補正（isotonic較正）。素のAPI確率は高い帯で実力を1割ほど過小評価していたため、実測ベースに直して表示">実測補正</span></div>';
  h+='<div class="ha"><div class="hacell"><div class="hak">本命確率</div><div class="hav hon">'
    +Math.round(ha.honC*100)+'%</div><div class="hsub">'+chip(ha.hmLane,'mc')+' '+r.b[ha.hmLane-1][0]+'</div></div>'
    +'<div class="hacell"><div class="hak">穴確率（4-6番手）</div><div class="hav ana">'
    +Math.round(ha.anaC*100)+'%</div><div class="hsub"><span class="lvl '+ha.lvlcls+'">'+ha.lvl+'</span></div></div></div>';
  h+='<div style="font-size:11px;color:#7e8796;margin:2px 0 4px">※確率は過去の実測1着率で較正済み（検証: 素のAPI値は本命が強い帯で+11〜12pt過小評価）。鉄板/標準/穴の帯・点数は従来どおり内部値で判定（backtest済みルールを変えないため）。</div>';
  {const al=ha.anaLane, af=r.ft?r.ft[al-1]:null, afin=done?r.b[al-1][2]:null;
   h+='<div class="cause" style="border-left-color:#e0a93b;color:#e0c896"><span class="h" style="color:#b89a5a">穴候補</span>'
     +chip(al,'mc')+' '+r.b[al-1][0]+' … '
     +'モーター'+(af&&af[0]!=null?'レース内'+Math.round(af[0])+'位':'–')
     +' / 直近勝率'+(af&&af[1]!=null?Math.round(af[1]*100)+'%':'–')
     +' / ST'+(af&&af[2]!=null?'レース内'+Math.round(af[2])+'位':'–')
     +'（1着確率'+(calP(r.ab[al-1]/1000)*100).toFixed(1)+'%・実測補正）'
     +(done?' <b style="color:'+(afin===1?'#43c59e':'#9aa3b2')+'">→ 結果'+(afin===1?'1着！（穴的中）':(afin?afin+'着':'－'))+'</b>':'')
     +'<br><span style="font-size:11px;color:#9aa3b2">※モデルはモーター・直近を織り込み済み（検証で確率を超える穴シグナルは無し）。材料が揃う穴ほど展開次第で1着の目。本命が弱いレースほど荒れやすい（本命&lt;40%で穴率約20%／≥70%で約8%）。</span></div>';}
  // 対抗1艇（穴）＝本命を食う筆頭＋根拠タグ。betScore2番手（展示反映後）。
  {const tk=taikou(r);const tkfin=done?r.b[tk.lane-1][2]:null;
   h+='<div class="cause" style="border-left-color:#a56be0;color:#d9c8ef"><span class="h" style="color:#b98fe0">🐎 対抗1艇（穴）</span>'
     +chip(tk.lane,'mc')+' '+tk.name+' … <b>本命'+chip(tk.fav,'mc')+'を食う筆頭</b>'
     +'（1着確率目安'+(tk.prob*100).toFixed(1)+'%）'
     +(tk.tags.length
        ? '<div style="margin-top:5px">'+tk.tags.map(t=>'<span class="tkt" title="'+t[1]+'">'+t[0]+'</span>').join('')+'</div>'
        : '<div style="margin-top:5px;font-size:11px;color:#9aa3b2">目立った武器なし（地力で予想2番手）</div>')
     +(done?' <b style="color:'+(tkfin===1?'#43c59e':'#9aa3b2')+'">→ 結果'+(tkfin===1?'1着！（対抗的中）':(tkfin?tkfin+'着':'－'))+'</b>':'')
     +'<br><span style="font-size:11px;color:#9aa3b2">※対抗＝betScore（'+(tk.ex?'展示反映後':'朝の学習')+'）の2番手。検証で隣接・展示・捲り屋は p_win に織込済＝当てる力は本命確率どまり（本命が飛んだ時に44%が1着）。回収率は本命軸に劣る<b>夢枠</b>。チルトは当日データのみ・過去検証不可の参考。</span></div>';}
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
      const pfOf=c=>plOf([c[0],c[1]])+plOf([c[1],c[0]]);   // 2連複＝両順序の和
      const evrow=(lab,combo,po,probFn,sep)=>{if(!combo||po==null)return '';const need=1/probFn(combo),bai=po/100,ok=bai>=need;
        return lab+' '+combo.join(sep||'-')+'：配当<b>'+bai.toFixed(1)+'倍</b> / 必要'+need.toFixed(1)+'倍 → '
          +(ok?'<span class="ok">妙味◎(+EV)</span>':'<span style="color:#9aa3b2">届かず(-EV)</span>');};
      h+='<div class="cause" style="border-left-color:#5dc7e0;color:#cdd6e2"><span class="h" style="color:#7fb2ff">買えてた場合の妙味</span>'
        +evrow('2連複',actEx,(r.po.length>2?r.po[2]:null),pfOf,'=')+'<br>'+evrow('3連単',actTri,r.po[1],plOf)+'</div>';
    }
  }
  h+='<div class="sec">1着確率（AI予想・学習モデル）</div>';
  r.b.forEach((b,w)=>{const a=LC[w+1];const fin=b[2];const pm=ps[w];
    h+='<div class="boat">'+(done?'<span class="fin">'+(fin===1?'<b>1着</b>':(fin?fin+'着':'<span style="color:#6b7280">－</span>'))+'</span>':'')
     +chip(w+1)+'<span class="bn">'+b[0]+'</span>'
     +(r.rk&&r.rk[w]?'<span class="rk" title="公式級別">'+(r.rk[w][0]||'–')+'</span>'
        +(r.rk[w][1]?'<span class="airk ai'+r.rk[w][1]+'" title="AI 3着以内ランク（この枠で3着以内に来る可能性：枠別3連対率×級別×直近）">'+r.rk[w][1]+'</span>':''):'')
     +'<div class="barw"><div class="bar" style="width:'+Math.max(pm/mx*100,2)+'%;background:'+a[0]+'"></div></div>'
     +'<span class="bp">'+(pm/10).toFixed(1)+'%</span></div>';});
  h+='<div style="font-size:11px;color:#7e8796;margin:2px 0 0">'
    +'<span class="rk">A1</span> 公式級別　｜　'
    +'<span class="airk aiS">S</span><span class="airk aiA">A</span><span class="airk aiB">B</span>'
    +'<span class="airk aiC">C</span><span class="airk aiD">D</span> '
    +'AI 3着以内ランク＝この枠で3着以内に来る可能性（枠別3連対率×級別×直近フォーム）の5段階評価</div>';
  // 相手（非本命5艇）の差＝2・3着の絞りやすさ。勝負どころ＝本命が強い×相手に差。
  const rd=rivalDiff(r);
  h+='<div class="rdbox'+(rd.prize?' prizebox':'')+'">'
    +'<span class="h">相手の差</span><span class="lvl '+rd.cls+'">'+rd.lvl+'</span> '
    +(rd.prize
       ? '&#127919; <b>勝負どころ</b>：本命が強く、相手（2・3着）にも差がある。検証では同じ本命確率でも2連複/3連単の的中が一段高い帯（P(2着&#124;1着)が+7〜18pt）。点数を絞って2連複/3連単に厚く張る価値。'
       : rd.lvl==='大'
         ? '相手（2・3着）は絞りやすいが、本命確率が低め。1着が読みづらいので頭は広めに。'
         : '相手が横一線で2・3着を絞りにくい。本命が強くても2連複/3連単は伸びにくい帯＝点数を欲張らない。')
    +'<br><span style="font-size:11px;color:#7e8796">※「相手の差」＝非本命5艇の1着確率のばらつき（発走前にモデルから分かる量）。1着の精度は本命確率が決め、相手の差はもっぱら2・3着を絞れるかに効く（検証 OOS 7,862R）。</span></div>';
  // 鉄板×実オッズの見送り判定（締切前・オッズ取得済のみ表示）。
  // backtest(2026-06・鉄板81R・最終オッズ): 1点目2連単<2.0見送り＝回収88.8→98.9%(+10.1pt)、
  // <3.0まで見送ると110.2%だが対象半減(n=33)のため2〜3倍帯は「妙味薄」の目安表示に留める。
  if(!done){
    const to=tetsuOdds(r);
    if(to){
      h+='<div class="rdbox"'+(to.skip?' style="border-color:#5a2a2a"':'')+'>'
        +'<span class="h">実オッズ判定</span>本命サイド2連単 '+to.c[0]+'-'+to.c[1]+' の実オッズ <b>'+to.odds.toFixed(1)+'倍</b>（市場の堅さの物差し・購入は2連複） → '
        +(to.skip
          ?'<b style="color:#f09595">&#9888; 見送り推奨</b>：本命サイドが売れすぎ（2倍未満）＝当たっても戻りが薄い帯。検証（6月・鉄板81R）ではこの帯を見送るだけで回収88.8→98.9%(+10.1pt)・的中率も向上。'
          :to.odds<3.0
            ?'<span style="color:#e0a93b">△ 妙味薄</span>：2〜3倍帯。参考検証では3倍未満まで見送ると回収110.2%だが対象が半減（n=33）のため目安扱い。'
            :'<b style="color:#43c59e">&#10003; オッズ妙味あり</b>：3倍以上。鉄板の回収を支えるのはこの帯。')
        +'<br><span style="font-size:11px;color:#7e8796">※ 公式サイトの2連単オッズを直接取得（締切40分前から約3分毎に自動更新）。オッズは締切まで動くので最終判断は直前に。</span></div>';
    }
  }
  // オッズ一覧ボタン（別ページ odds.html）: 2連複15点/2連単30点/3連単120点＋AI買い目ハイライト。
  // bf=本線2連複・b3=本線3連単・ba=穴予想（穴帯=対抗6点/標準帯=穴候補6点）をURLで渡す。/api/odds はクラウドのみ。
  if(IS_CLOUD){
    const _bf=plTopF(sb,kEx(honBand)).map(c=>c[0].join('-')).join(',');
    const _b3=triOn(honBand)?triBuyList(plTop(sb,3,200),kTri(honBand),honBand,laneRankMap(sb)).map(c=>c[0].join('-')).join(','):'';
    const _ba=(honBand<0.45?taikouRef(r):(honBand<0.65?anaCandRef(r.ab):[])).map(c=>c.join('-')).join(',');
    h+='<div style="margin:10px 0 2px"><a href="odds.html?id='+r.id+'&v='+encodeURIComponent(r.v)+'&no='+r.no+(r.tm?'&tm='+encodeURIComponent(r.tm):'')+(_bf?'&bf='+_bf:'')+(_b3?'&b3='+_b3:'')+(_ba?'&ba='+_ba:'')+'" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border:1px solid #2a3852;border-radius:8px;background:#141a26;color:#8fa8d0;font-size:13px;font-weight:600;text-decoration:none">&#128202; オッズ一覧<span style="font-size:11px;color:#7e8796;font-weight:400">（別ページ・全買い目の実オッズ）</span></a></div>';
  }
  // 2連複 上位（予想確率で点数変動・上限3）。2026-07-16 上限5→3へ縮小。
  const honD=honBand;const nEx=kEx(honD),nTri=kTri(honD);   // 点数＝API本命確率（荒れ度据置）
  const exTag=exOn?'<span class="kbadge" style="color:#43c59e;background:#10231d;border-color:#2f6f57">展示反映</span>':'';
  const ex=plTopF(sb,nEx);
  const exYen=allocYen(meriW(ex.map(c=>c[1]),honD),2000);
  let exHit=actEx?ex.some(c=>eqPair(c[0],actEx)):false;
  const exHitI=actEx?ex.findIndex(c=>eqPair(c[0],actEx)):-1;
  const exRec=(actEx&&payEx!=null)?(exHitI>=0?Math.round(payEx*exYen[exHitI]/2000):0):null;  // 回収率＝払戻÷¥2,000
  h+='<div class="sec">2連複 上位'+nEx+'<span class="kbadge">確率連動</span>'+exTag+'<span class="kbadge bud">計¥2,000</span>'+(actEx?(exHit?'<span class="tag h">的中</span>':'<span class="tag m">圏外</span>'):'')+recBadge(exRec)+'</div>';
  ex.forEach((c,i)=>{const hit=actEx&&eqPair(c[0],actEx);
    h+='<div class="crow'+(hit?' hit':'')+'" data-combo="'+c[0].join('-')+'" data-p="'+c[1]+'"><span class="rk">'+(i+1)+'</span>'+chip(c[0][0],'mc')+'<span class="arr">=</span>'+chip(c[0][1],'mc')
     +(hit?'<span class="ok" style="font-size:11px;margin-left:4px">的中</span>'+(payEx!=null?'<span class="hitpay">配当¥'+payEx.toLocaleString()+'</span>':''):'')
     +'<span class="stake">¥'+exYen[i].toLocaleString()+'</span>'
     +'<span class="cp">'+(c[1]*100).toFixed(1)+'%<span class="odds">必要'+(1/c[1]).toFixed(1)+'倍</span></span><span class="ev"></span></div>';});
  if(actEx&&!exHit){h+='<div class="crow"><span class="rk">実</span>'+chip(Math.min(actEx[0],actEx[1]),'mc')+'<span class="arr">=</span>'+chip(Math.max(actEx[0],actEx[1]),'mc')+'<span class="cp ng">実際の結果</span></div>';}
  // 3連単 上位（予想確率で点数変動・上限20）。標準帯は本命型/標準型/穴型を明示し穴型を必ず1点含める。
  // 穴帯(本命<0.45)は3連単を組まない（回収72.9%＝資金を溶かす主犯。停止で全体+0.8pt・賭け金▲16%）。
  const rankMap=laneRankMap(sb);const stdBand=honD>=0.45&&honD<0.65;
  if(!triOn(honD)){
    // 穴帯（波乱）＝本線の3連単は見送り（回収72.9%＝資金を溶かす主犯）。3連単スロットは
    // 「穴予想＝対抗6点（帯別方式A）」を合体出力。穴帯は対抗アタマ6点が最良（backtest 88.2%）。
    const tk=taikou(r);const taiRef=taikouRef(r);
    const taiHit=actTri?taiRef.some(c=>eqArr(c,actTri)):false;
    h+='<div class="sec">3連単 <span class="kbadge bud" style="color:#a56be0;background:#1c1526;border-color:#5a3f8f">本線=見送り</span><span class="kbadge" style="color:#c79bff">穴予想=対抗'+taiRef.length+'点</span>'+(actTri?(taiHit?'<span class="tag h">的中</span>':'<span class="tag m">圏外</span>'):'')+'</div>';
    h+='<div style="font-size:11px;color:#7e8796;margin:2px 0 0">※穴帯（本命確率・実測補正'+Math.round(calP(honD)*100)+'%）は本線の3連単を買いません（回収72.9%＝資金を溶かす主犯・backtest 27,660R）。<b>本線は2連複のみ勝負。</b>穴予想は帯別方式（穴帯＝<b style="color:#b98fe0">対抗'+chip(tk.lane,'mc')+'号</b>をアタマに本命/3番/4番へ流す6点）。穴帯ではこれが最良（backtest 回収88.2%・的中15.3%＞穴候補79.7%）。<b>本線¥2,000とは別枠の穴狙い（各¥100・計¥600目安）。</b>'+(actTri?(taiHit?'<b style="color:#43c59e"> →来た</b>':''):'')+'</div>';
    taiRef.forEach(c=>{const hit=actTri&&eqArr(c,actTri);
      h+='<div class="crow'+(hit?' hit':'')+'" style="opacity:.82"><span class="rk" style="font-size:9px;color:#b98fe0">対抗</span>'+chip(c[0],'mc')+'<span class="arr">&rarr;</span>'+chip(c[1],'mc')+'<span class="arr">&rarr;</span>'+chip(c[2],'mc')
       +'<span class="ctag tg-ana" style="background:#2a1c3a;color:#c79bff">穴予想</span>'
       +'<span class="stake">¥100</span>'
       +(hit?'<span class="ok" style="font-size:11px;margin-left:4px">来た</span>':'')
       +'</div>';});
    if(actTri&&!taiHit){h+='<div class="crow"><span class="rk">実</span>'+chip(actTri[0],'mc')+'<span class="arr">&rarr;</span>'+chip(actTri[1],'mc')+'<span class="arr">&rarr;</span>'+chip(actTri[2],'mc')+'<span class="cp ng">実際の結果（本線見送り）</span></div>';}
  }else{
  const triAll=plTop(sb,3,200);
  const tri=triBuyList(triAll,nTri,honD,rankMap);
  const triYen=allocYen(meriW(tri.map(c=>c[1]),honD),2000);
  let triHit=actTri?tri.some(c=>eqArr(c[0],actTri)):false;
  const triHitI=actTri?tri.findIndex(c=>eqArr(c[0],actTri)):-1;
  const triRec=(actTri&&payTri!=null)?(triHitI>=0?Math.round(payTri*triYen[triHitI]/2000):0):null;  // 回収率＝払戻÷¥2,000
  h+='<div class="sec">3連単 上位'+tri.length+'<span class="kbadge">確率連動</span>'+exTag+'<span class="kbadge bud">計¥2,000</span>'+(actTri?(triHit?'<span class="tag h">的中</span>':'<span class="tag m">圏外</span>'):'')+recBadge(triRec)+'</div>';
  tri.forEach((c,i)=>{const hit=actTri&&eqArr(c[0],actTri);const tk=(stdBand&&comboKind(c[0],rankMap)[0]==='本命型')?comboKind(c[0],rankMap):null;
    h+='<div class="crow'+(hit?' hit':'')+'" data-combo="'+c[0].join('-')+'" data-p="'+c[1]+'"><span class="rk">'+(i+1)+'</span>'+chip(c[0][0],'mc')+'<span class="arr">&rarr;</span>'+chip(c[0][1],'mc')+'<span class="arr">&rarr;</span>'+chip(c[0][2],'mc')
     +(tk?'<span class="ctag '+tk[1]+'">'+tk[0]+'</span>':'')
     +(hit?'<span class="ok" style="font-size:11px;margin-left:4px">的中</span>'+(payTri!=null?'<span class="hitpay">配当¥'+payTri.toLocaleString()+'</span>':''):'')
     +'<span class="stake">¥'+triYen[i].toLocaleString()+'</span>'
     +'<span class="cp">'+(c[1]*100).toFixed(1)+'%<span class="odds">必要'+(1/c[1]).toFixed(1)+'倍</span></span><span class="ev"></span></div>';});
  if(actTri&&!triHit){h+='<div class="crow"><span class="rk">実</span>'+chip(actTri[0],'mc')+'<span class="arr">&rarr;</span>'+chip(actTri[1],'mc')+'<span class="arr">&rarr;</span>'+chip(actTri[2],'mc')+'<span class="cp ng">実際の結果</span></div>';}
  }
  // 穴予想（帯別方式A・標準帯）＝穴候補アタマ6点。標準帯はこれが最良（backtest 73.3%）。
  //   本線¥2,000（2連単/3連単）とは別枠の穴狙い（各¥100・計¥600目安）。
  if(stdBand){
    const candRef=anaCandRef(r.ab);
    const candHit=actTri?candRef.some(c=>eqArr(c,actTri)):false;
    h+='<div style="font-size:11px;color:#7e8796;margin:8px 0 1px"><b style="color:#e0a93b">穴予想（別枠の穴狙い）</b>：穴候補<b style="color:#e0a93b">'+ha.anaLane+'号</b>をアタマに上位3艇へ流す6点（標準帯はこれが最良・backtest 回収73.3%）。軽視艇アタマで当たれば万舟級。<b>本線¥2,000とは別枠（各¥100・計¥600目安）。</b>'+(actTri?(candHit?'<b style="color:#43c59e"> →来た</b>':''):'')+'</div>';
    candRef.forEach(c=>{const hit=actTri&&eqArr(c,actTri);
      h+='<div class="crow'+(hit?' hit':'')+'" style="opacity:.82"><span class="rk" style="font-size:9px;color:#b89a5a">穴</span>'+chip(c[0],'mc')+'<span class="arr">&rarr;</span>'+chip(c[1],'mc')+'<span class="arr">&rarr;</span>'+chip(c[2],'mc')
       +'<span class="ctag tg-ana">穴予想</span>'
       +'<span class="stake">¥100</span>'
       +(hit?'<span class="ok" style="font-size:11px;margin-left:4px">来た</span>':'')
       +'</div>';});
  }
  // 実オッズ取得ボタンは一旦非表示（必要オッズ表示のみ残す）
  h+='<div class="cause" style="border-left-color:#e0a93b;color:#e0c896;margin-top:10px"><span class="h" style="color:#b89a5a">期待値の見方</span>'
    +'<span class="odds" style="display:inline;color:#e0a93b">必要◯倍</span>＝この買い目が期待値プラスになる最低オッズ（＝1÷確率）。'
    +'発走前の実オッズがこれを超えていれば長期的に有利な買い目（必要倍＝AI予想確率の逆数）。</div>';
  h+='<div class="legend">※ 予想＝AI学習モデル（本命・1着確率・買い目）。割合・荒れ度・点数の基準＝API簡易合成。確率は朝の出走表のみから算出（展示・オッズ不使用）。本命=1着確率最大の枠。前日・前々日は結果と的中可否を表示。'
    +'買目点数は予想確率に連動（堅い→少点／荒れ→多点、2連複≦5・3連単≦20）＝本命確率（実測補正）'+Math.round(calP(honD)*100)+'%で2連複'+nEx+'点'+(triOn(honD)?'/3連単'+nTri+'点':'（穴帯につき3連単は見送り）')+'。'
    +'<b>金額は'+(triOn(honD)?'2連複・3連単それぞれ':'2連複に')+'¥2,000を配分（¥100単位・各点最低¥100）。全帯<b style="color:#e0a93b">爆発重視</b>＝配当の大きい薄い目に振り切って配分（薄目重み∝1÷確率・EVフラット）。合成回収はほぼ不変だが5倍超の爆発頻度と一撃の最大配当が大きく増える（的中率は不変・backtest 26,870R）。</b>'
    +'2連複は2026-07-07に2連単から切替（同じ点数で回収+3.7pt・的中52→68%・backtest 26,843R）。'
    +(triOn(honD)?'':'<b>穴帯は3連単を買いません（回収72.9%→停止で全体+0.8pt／賭け金減・backtest検証）。</b>')
    +(stdBand?'標準帯の3連単は穴型（5-6番手絡み）を購入対象から外します。':'')
    +(honD<0.65?'<b>穴予想（帯別方式）</b>＝'+(stdBand?'標準帯は穴候補（API4番人気）アタマ6点':'穴帯は対抗（予想2番手）アタマ6点')+'を'+(triOn(honD)?'別途':'3連単スロットに合体して')+'表示。本線¥2,000とは別枠の穴狙い（各¥100・計¥600目安）。標準+穴の帯別運用でbacktest 回収75.7%（標準=候補73.3%／穴帯=対抗88.2%）。':'')
    +'実オッズはモデル外なので各自で確認し「必要◯倍」と比較してください。</div>';
  return h;
}

function nav(){
  return '<h1>競艇当日予想</h1><div class="tabs">'
    +'<button class="tb'+(tab==='pred'?' on':'')+'" data-t="pred">予想</button>'
    +'<button class="tb'+(tab==='stats'?' on':'')+'" data-t="stats">場別成績</button>'
    +((IS_LIVE||IS_CLOUD)?'<button class="upbtn"'+(upBusy?' disabled':'')+' data-act="update">更新</button>':'')+'</div>'
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
  return '<div class="sortbar">並び替え:'+mk('e','2連複')+mk('t','3連単')+'</div>';
}
// 仮想100万円チャレンジ 残高推移スパークライン（開始点を含む）。
function gameChart(G){
  const pts=[{bal:G.start}].concat(G.rows.map(r=>({bal:r.bal})));
  if(pts.length<2)return '';
  const W=340,H=76,pad=6;
  const bals=pts.map(p=>p.bal);
  const mn=Math.min(G.start,...bals), mx=Math.max(G.start,...bals), rng=(mx-mn)||1;
  const x=i=>pad+i*(W-2*pad)/(pts.length-1);
  const y=v=>pad+(H-2*pad)*(1-(v-mn)/rng);
  let dp=''; pts.forEach((p,i)=>{dp+=(i?'L':'M')+x(i).toFixed(1)+' '+y(p.bal).toFixed(1)+' ';});
  const last=pts[pts.length-1].bal, up=last>=G.start, col=up?'#43c59e':'#e06b6b';
  const y0=y(G.start).toFixed(1);
  let s='<svg viewBox="0 0 '+W+' '+H+'" width="100%" style="max-width:360px;display:block;margin:8px 0">';
  s+='<line x1="0" y1="'+y0+'" x2="'+W+'" y2="'+y0+'" stroke="#4a5470" stroke-dasharray="3 3" stroke-width="1"/>';
  s+='<path d="'+dp+'" fill="none" stroke="'+col+'" stroke-width="2" stroke-linejoin="round"/>';
  s+='<circle cx="'+x(pts.length-1).toFixed(1)+'" cy="'+y(last).toFixed(1)+'" r="3" fill="'+col+'"/>';
  s+='</svg>';
  return s;
}
// 仮想100万円チャレンジのパネル（場別成績の先頭に表示）。
function gameView(){
  const G=D.game; if(!G)return '';
  const yen=v=>'¥'+Math.round(v).toLocaleString('en-US');
  const start=G.start, bal=G.bal, pl=bal-start, up=pl>=0, col=up?'#43c59e':'#e06b6b';
  let h='<div style="margin-top:8px;border:1px solid #2a3550;border-radius:12px;padding:14px;background:linear-gradient(180deg,#141c2e,#0f1522)">';
  h+='<div style="font-size:16px;font-weight:700;color:#ffd66b">💰 仮想100万円チャレンジ <span style="font-size:12px;color:#8b96a8;font-weight:500">（7/1〜7/31・AI運用）</span></div>';
  if(G.busted)h+='<div style="margin:10px 0;padding:10px;border-radius:8px;background:#3a1420;color:#ff8a8a;text-align:center;font-weight:700;font-size:15px">💀 GAME OVER ― 残高が尽きました</div>';
  h+='<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:8px 0 2px">'
    +'<span style="font-size:30px;font-weight:800;color:'+col+'">'+yen(bal)+'</span>'
    +'<span style="font-size:14px;font-weight:700;color:'+col+'">'+(up?'+':'')+yen(pl)+'（'+(up?'+':'')+Math.round(pl/start*100)+'%）</span></div>';
  h+='<div style="font-size:11px;color:#8b96a8">スタート '+yen(start)+' ／ 最高 '+yen(G.peak)+'</div>';
  if(G.pending&&G.pending.n)
    h+='<div style="font-size:12px;color:#7fb2ff;margin-top:6px">▶ 本日 '+G.pending.d.slice(5)+' 運用中：鉄板 '+G.pending.n+'レースに約 '+yen(G.pending.stake)+' を投票予定（結果は翌朝反映）</div>';
  h+=gameChart(G);
  if(G.rows.length){
    h+='<div class="swrap"><table class="st"><thead><tr>'
      +'<th class="k">日付</th><th>鉄板R</th><th>賭け金</th><th>払戻</th><th>損益</th><th>残高</th></tr></thead><tbody>';
    for(const r of G.rows){const rp=r.pl>=0;
      h+='<tr><td class="k">'+r.d.slice(5)+'</td><td class="num scol">'+r.n+'</td>'
        +'<td class="num">'+yen(r.stake)+'</td><td class="num">'+yen(r.ret)+'</td>'
        +'<td class="num" style="color:'+(rp?'#43c59e':'#e06b6b')+'">'+(rp?'+':'')+yen(r.pl)+'</td>'
        +'<td class="num" style="font-weight:700">'+yen(r.bal)+'</td></tr>';}
    h+='</tbody></table></div>';
  }else{
    h+='<div class="meta">まだ精算済みの日がありません（初日の結果は翌朝に反映されます）。</div>';
  }
  h+='<div class="legend">AI運用ルール（強気設定）：<b>鉄板レース（内部判定・本命確率65％以上）だけ</b>を厳選し、1レースあたり残高の約12％を、そのレースの買い目（2連複＋3連単・確率比例配分）に投票。'
    +'1日の投票上限は残高（最大100万円）。実際の配当で精算し、フライングは返還。<b>残高が¥100を切ったら終了。</b>'
    +'※控除率25％の壁があり増え続ける保証はありません＝AIの実力を可視化する実験です。</div>';
  h+='</div>';
  return h;
}
// 仮想100万円チャレンジ【穴帯バージョン】パネル。本家(鉄板)の下に対比表示。
function gameViewAna(){
  const G=D.game_ana; if(!G)return '';
  const yen=v=>'¥'+Math.round(v).toLocaleString('en-US');
  const start=G.start, bal=G.bal, pl=bal-start, up=pl>=0, col=up?'#43c59e':'#e06b6b';
  let h='<div style="margin-top:12px;border:1px solid #3a2a50;border-radius:12px;padding:14px;background:linear-gradient(180deg,#1c1430,#140f22)">';
  h+='<div style="font-size:16px;font-weight:700;color:#c79bff">🕳️ 仮想100万円チャレンジ【穴帯】 <span style="font-size:12px;color:#9a8bb8;font-weight:500">（7/1〜7/31・穴だけ追う実験）</span></div>';
  if(G.busted)h+='<div style="margin:10px 0;padding:10px;border-radius:8px;background:#3a1420;color:#ff8a8a;text-align:center;font-weight:700;font-size:15px">💀 GAME OVER ― 残高が尽きました</div>';
  h+='<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:8px 0 2px">'
    +'<span style="font-size:30px;font-weight:800;color:'+col+'">'+yen(bal)+'</span>'
    +'<span style="font-size:14px;font-weight:700;color:'+col+'">'+(up?'+':'')+yen(pl)+'（'+(up?'+':'')+Math.round(pl/start*100)+'%）</span></div>';
  h+='<div style="font-size:11px;color:#9a8bb8">スタート '+yen(start)+' ／ 最高 '+yen(G.peak)+'</div>';
  if(G.pending&&G.pending.n)
    h+='<div style="font-size:12px;color:#c79bff;margin-top:6px">▶ 本日 '+G.pending.d.slice(5)+' 運用中：穴帯 '+G.pending.n+'レースに約 '+yen(G.pending.stake)+' を投票予定（結果は翌朝反映）</div>';
  h+=gameChart(G);
  if(G.rows.length){
    h+='<div class="swrap"><table class="st"><thead><tr>'
      +'<th class="k">日付</th><th>穴帯R</th><th>賭け金</th><th>払戻</th><th>損益</th><th>残高</th></tr></thead><tbody>';
    for(const r of G.rows){const rp=r.pl>=0;
      h+='<tr><td class="k">'+r.d.slice(5)+'</td><td class="num scol">'+r.n+'</td>'
        +'<td class="num">'+yen(r.stake)+'</td><td class="num">'+yen(r.ret)+'</td>'
        +'<td class="num" style="color:'+(rp?'#43c59e':'#e06b6b')+'">'+(rp?'+':'')+yen(r.pl)+'</td>'
        +'<td class="num" style="font-weight:700">'+yen(r.bal)+'</td></tr>';}
    h+='</tbody></table></div>';
  }else{
    h+='<div class="meta">まだ精算済みの日がありません（初日の結果は翌朝に反映されます）。</div>';
  }
  h+='<div class="legend">穴狙いルール（実験）：<b>穴帯レース（本命確率45％未満）だけ</b>を対象に、1レースあたり残高の約1％を<b>穴予想＝対抗（予想2番手）アタマ6点</b>（3連単・帯別方式）に均等投票。'
    +'1日の投票上限は残高。実際の配当で精算し、フライングは返還。<b>残高が¥100を切ったら終了。</b>'
    +'※波乱帯の穴予想は対抗6点でbacktest回収88.2％（穴候補6点79.7％より上位）だが高分散＝<b>「穴を追い続けるとどうなるか」を可視化する実験</b>。本家（鉄板）と見比べてください。</div>';
  h+='</div>';
  return h;
}
// 直近30日の日別回収率 折れ線（2連単／3連単／穴目 の3系統）。
function recoveryChart(days,series){
  if(!days||!days.length)return '';
  const W=340,padL=34,padR=10,padT=14,padB=26,H=padT+150+padB;
  const rec=(o)=>o&&o.inv>0?o.ret/o.inv*100:null;    // 回収率（投資0は点なし）
  let mx=100;
  days.forEach(d=>series.forEach(s=>{const v=rec(d[s.k]);if(v!=null&&v>mx)mx=v;}));
  mx=Math.min(mx,400);                                // 上限クリップ（読みやすさ）
  const step=mx>300?100:mx>150?50:mx>100?40:20;
  const M=Math.ceil(mx/step)*step;
  const n=days.length;
  const x=i=>padL+(n<=1?(W-padL-padR)/2:i*(W-padL-padR)/(n-1));
  const y=v=>padT+(H-padT-padB)*(1-Math.min(v,M)/M);
  let s='<svg viewBox="0 0 '+W+' '+H+'" width="100%" style="max-width:520px;display:block;margin:6px auto">';
  for(let g=0;g<=M;g+=step){const yy=y(g).toFixed(1);
    s+='<line x1="'+padL+'" y1="'+yy+'" x2="'+(W-padR)+'" y2="'+yy+'" stroke="#232c42" stroke-width="1"/>';
    s+='<text x="'+(padL-4)+'" y="'+(+yy+3)+'" fill="#6b7488" font-size="9" text-anchor="end">'+g+'</text>';}
  const y100=y(100).toFixed(1);                        // 100%損益分岐
  s+='<line x1="'+padL+'" y1="'+y100+'" x2="'+(W-padR)+'" y2="'+y100+'" stroke="#d9745c" stroke-dasharray="4 3" stroke-width="1"/>';
  series.forEach(se=>{
    let dp='',started=false;const pts=[];
    days.forEach((d,i)=>{const v=rec(d[se.k]);
      if(v==null){started=false;return;}
      dp+=(started?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)+' ';started=true;pts.push([i,v]);});
    s+='<path d="'+dp+'" fill="none" stroke="'+se.col+'" stroke-width="1.8" stroke-linejoin="round"/>';
    pts.forEach(p=>{s+='<circle cx="'+x(p[0]).toFixed(1)+'" cy="'+y(p[1]).toFixed(1)+'" r="1.7" fill="'+se.col+'"/>';});
  });
  // X軸ラベル（最初/中間/最後）
  const ticks=n<=1?[0]:[0,Math.floor((n-1)/2),n-1];
  ticks.forEach(i=>{const mm=days[i].d.slice(5).replace('-','/');
    s+='<text x="'+x(i).toFixed(1)+'" y="'+(H-8)+'" fill="#6b7488" font-size="9" text-anchor="middle">'+mm+'</text>';});
  s+='</svg>';
  return s;
}
// 【直近回収率結果】＝直近30日の日別回収率グラフ＋券種別（2連単/3連単/穴目）の累計成績。
function recentRecoveryView(){
  const R=D.daily_rec; if(!R||!R.days||!R.days.length)return '';
  // 穴目の対象帯（波乱/標準/合算）＝global anaScope を共用（上部サマリーと連動）。
  //   波乱帯(<0.45)=対抗6点(ana_h) / 標準帯(0.45-0.65)=穴候補6点(ana_s) / 合算=両者を合算。
  const scLab={all:'合算',haran:'波乱帯',std:'標準帯'};
  const merge=(a,b)=>({n:a.n+b.n,h:a.h+b.h,inv:a.inv+b.inv,ret:a.ret+b.ret});
  const anaPick=o=>anaScope==='haran'?o.ana_h:anaScope==='std'?o.ana_s:merge(o.ana_h,o.ana_s);
  const days=R.days.map(d=>Object.assign({},d,{ana:anaPick(d)}));   // 穴目=選択帯で合成
  const tot=Object.assign({},R.tot,{ana:anaPick(R.tot)});
  const series=[{k:'ex',col:'#43c59e',lab:'2連複'},{k:'tri',col:'#e0a93b',lab:'3連単'},{k:'ana',col:'#c79bff',lab:'穴目（'+scLab[anaScope]+'）'}];
  const pct=(a,b)=>b?Math.round(a/b*100):0;
  const recCls=r=>r>=100?'rok':(r>0?'ramb':'rng');
  const yfmt=v=>'¥'+Math.round(v).toLocaleString();
  let h='<div class="sec" style="margin-top:22px;color:#cdd6e2;font-size:15px;font-weight:700">📈 直近回収率結果 <span style="font-size:11px;color:#8b96a8;font-weight:500">（'+R.from.slice(5)+'〜'+R.to.slice(5)+'・直近'+R.days.length+'日）</span></div>';
  h+='<div class="meta">買い目・金額はサイト本体と同一（各券種¥2,000を全帯爆発重視で配分＝薄い高配当目に振り切り・穴帯は3連単を買わない・フライングは返還）。'
    +'折れ線＝日別の回収率（％）。<span style="color:#d9745c">赤破線＝100％（損益分岐）</span>。</div>';
  // 穴目の対象帯セレクタ（グラフ・券種別テーブルの穴目行に連動）
  h+='<div class="anascope" style="justify-content:center;margin:6px 0 2px">穴目の対象：'
    +['all','haran','std'].map(k=>'<button class="asb'+(anaScope===k?' on':'')+'" data-as="'+k+'">'+scLab[k]+'</button>').join('')+'</div>';
  // 凡例
  h+='<div class="meta" style="text-align:center">'
    +series.map(s=>'<span style="color:'+s.col+'">■</span> '+s.lab).join('　')+'</div>';
  h+=recoveryChart(days,series);
  // ── 当日・前日・前々日（券種別 的中率／投資／回収／回収率）──────────────
  // 直近3日を新しい順に 当日／前日／前々日 として券種別に並べる。
  const cols=days.slice(-3).reverse();
  const labs=['当日','前日','前々日'];
  function tbl3(k,lab,isAna){
    let t='<div class="swrap"><table class="st"><thead><tr><th class="k">'
      +'<span style="color:'+series.find(s=>s.k===k).col+'">■</span> '+lab+'</th>';
    cols.forEach((c,i)=>{t+='<th>'+labs[i]+'<br><small>'+c.d.slice(5)+'</small></th>';});
    t+='</tr></thead><tbody>';
    t+='<tr><td class="k">的中率</td>'+cols.map(c=>{const o=c[k];
      return '<td class="num">'+(o.n?pct(o.h,o.n)+'%':'–')+'<small>'+o.h+'/'+o.n+'</small></td>';}).join('')+'</tr>';
    t+='<tr><td class="k">投資</td>'+cols.map(c=>'<td class="num">'+yfmt(c[k].inv)+'</td>').join('')+'</tr>';
    t+='<tr><td class="k">回収</td>'+cols.map(c=>'<td class="num">'+yfmt(c[k].ret)+'</td>').join('')+'</tr>';
    t+='<tr><td class="k">回収率</td>'+cols.map(c=>{const o=c[k];const rr=pct(o.ret,o.inv);
      return '<td class="num"><b class="'+recCls(rr)+'">'+(o.inv?rr+'%':'–')+'</b></td>';}).join('')+'</tr>';
    if(isAna)  // 穴目は買わないので「的中していれば穴予想的中」を明示
      t+='<tr><td class="k">穴目判定</td>'+cols.map(c=>{const o=c[k];
        return '<td class="num">'+(o.n===0?'<small style="color:#7e8796">対象なし</small>'
          :o.h>0?'<b style="color:#c79bff">🎯的中'+o.h+'R</b>'
          :'<small style="color:#7e8796">不的中</small>')+'</td>';}).join('')+'</tr>';
    t+='</tbody></table></div>';
    return t;
  }
  if(cols.length){
    h+='<div class="meta" style="margin-top:12px;color:#cdd6e2;font-weight:600">当日・前日・前々日（券種別）</div>';
    h+=series.map(s=>tbl3(s.k,s.lab,s.k==='ana')).join('');
  }
  // 券種別 累計（的中率／投資／回収／回収率）
  h+='<div class="swrap"><table class="st"><thead><tr>'
    +'<th class="k">券種</th><th>的中率</th><th>投資</th><th>回収</th><th>回収率</th></tr></thead><tbody>';
  series.forEach(s=>{const t=tot[s.k];const rr=pct(t.ret,t.inv);
    h+='<tr><td class="k"><span style="color:'+s.col+'">■</span> '+s.lab
      +(s.k==='ana'?'<small style="color:#9a8bb8"> 買わない参考</small>':'')+'</td>'
      +'<td class="num">'+pct(t.h,t.n)+'%<small>'+t.h+'/'+t.n+'</small></td>'
      +'<td class="num">'+yfmt(t.inv)+'</td>'
      +'<td class="num">'+yfmt(t.ret)+'</td>'
      +'<td class="num"><b class="'+recCls(rr)+'">'+rr+'%</b></td></tr>';});
  h+='</tbody></table></div>';
  const anaDesc={
    all:'標準帯（本命45-65％）は穴候補（API4番人気）アタマ6点／波乱帯（本命45％未満）は対抗（予想2番手）アタマ6点',
    haran:'波乱帯（本命確率45％未満）で穴予想＝対抗（予想2番手）アタマ6点',
    std:'標準帯（本命45-65％）で穴予想＝穴候補（API4番人気）アタマ6点'};
  h+='<div class="legend"><b>2連複・3連単</b>＝実際にサイトが買う買い目の回収率（購入対象）。'
    +'<b style="color:#c79bff">穴目（'+scLab[anaScope]+'）</b>＝'+anaDesc[anaScope]+'を3連単で買った場合の<b>参考シミュレーション（実際は購入しない）</b>（帯別方式）。'
    +'的中していれば「穴予想的中」＝万舟級の的中。帯別方式のbacktest回収＝標準帯73.3％／波乱帯88.2％（合算75.7％）だが高分散で日別は大きく振れます。'
    +'※日別のため高配当1本で大きく振れます。控除率約25％の壁で、いずれの券種も長期回収率は100％未満が基本です。</div>';
  return h;
}
function statsView(){
  // 場別成績は Python 側で全期間（2026年〜）集計済み（学習モデル＝主系統で順位付け）。
  const V=D.vstats_api;
  const RC=D.recent_api;
  const RG=D.regime_api;
  let h=nav();
  h+=gameView();
  h+=gameViewAna();
  h+=recentRecoveryView();
  h+='<div class="meta" style="margin-top:6px">'
    +'<b style="color:#7fb2ff">AI予想（学習モデル）</b>の成績。'
    +'<br><span style="font-size:11px;color:#7e8796">※荒れ度（鉄板/標準/穴）・買目点数はAPI本命確率で判定（順位付けは学習モデル）。</span></div>';
  if(!V||!V.n){return h+'<div class="meta">結果データがまだありません。</div>';}
  const cell=(v,extra)=>'<td class="num'+(extra?' '+extra:'')+'">'+v+'%</td>';
  const row=(a,all)=>'<tr'+(all?' class="all"':'')+'><td class="k">'+a[0]+'</td>'
    +'<td class="num scol">'+a[1]+'</td><td class="num">'+a[2]+'%</td>'
    +cell(a[3],all?'':'g2')+cell(a[5],all?'':'g2')+cell(a[6],all?'':'g2')
    +cell(a[7],all?'':'g3')+cell(a[9],all?'':'g3')+cell(a[10],all?'':'g3')+'</tr>';
  h+='<div class="meta">対象 '+V.from+'〜'+V.to+'（'+V.n+'レース・収集データ全体）・ '
    +'数字=予想上位K通り以内に決着が入った割合。「変動」=予想確率連動の点数（堅い→少点／荒れ→多点）。'
    +'並び替えは「変動」的中率（2連複≤3／3連単≤20）基準。</div>';
  h+=sortbar('v',vsort);
  const vrows=vsort.c?sortRows(V.rows,vsort.c==='e'?5:9,vsort.d):V.rows;
  h+='<div class="swrap"><table class="st"><thead><tr>'
    +'<th class="k">会場</th><th>R数</th><th>本命<br>1着</th>'
    +'<th class="g2">2連複<br>本命</th><th class="g2">変動<br>≤3</th><th class="g2">変動<br>回収</th>'
    +'<th class="g3">3連単<br>本命</th><th class="g3">変動<br>≤20</th><th class="g3">変動<br>回収</th></tr></thead><tbody>';
  for(const a of vrows)h+=row(a,false);
  h+=row(V.all,true);
  h+='</tbody></table></div>';
  h+='<div class="legend">※ 収集データ全体（'+V.from+'〜'+V.to+'）の結果から集計。本命=1着確率最大の枠。'
    +'「変動」=予想確率に応じた点数（2連複≤3/3連単≤20）以内に実際の決着が含まれた割合（その点数を買えば当たる割合）。'
    +'<b>「変動回収」=その変動点数を各100円で実際に買った場合の回収率（Σ配当÷賭け金）。100%超で利益。</b>'
    +'※ 4月までは学習期間を含むため的中率・回収率はやや高めに出る（5月以降が純粋な検証）。'
    +'※ 全期間でも回収率は100%未満が基本（控除率約25%の壁）。</div>';
  // 前日・前々日の場別 的中率＋回収率（系統は上のトグルに連動）
  if(RC&&RC.rows&&RC.rows.length){
    const rrow=(a,all)=>'<tr'+(all?' class="all"':'')+'><td class="k">'+a[0]+'</td>'
      +'<td class="num scol">'+a[1]+'</td>'
      +'<td class="num g2">'+a[2]+'%</td><td class="num g2">'+a[3]+'%</td>'
      +'<td class="num g3">'+a[4]+'%</td><td class="num g3">'+a[5]+'%</td></tr>';
    h+='<div class="sec" style="margin-top:22px;color:#cdd6e2;font-size:14px">前日・前々日の的中率・回収率（'+RC.from.slice(5)+'〜'+RC.to.slice(5)+'）</div>';
    h+='<div class="meta">実践的中＝サイトの買い目を実際に買った場合の的中率（2連複≤3/3連単≤20点）。'
      +'<b>買い目・金額はサイト本体と同一</b>＝各券種¥2,000を全帯爆発重視で配分（薄い高配当目に振り切り・EVフラット）・穴帯(本命&lt;45%)は3連単を買わない・標準帯は穴型を除外・フライングは返還。'
      +'回収率＝Σ(配当×賭け金/100)÷Σ賭け金。100%超で利益。並び替えは回収率基準。</div>';
    h+=sortbar('r',rsort);
    const rrows=rsort.c?sortRows(RC.rows,rsort.c==='e'?3:5,rsort.d):RC.rows;
    h+='<div class="swrap"><table class="st"><thead><tr>'
      +'<th class="k">会場</th><th>R数</th>'
      +'<th class="g2">2連複<br>実践的中</th><th class="g2">回収率</th>'
      +'<th class="g3">3連単<br>実践的中</th><th class="g3">回収率</th></tr></thead><tbody>';
    for(const a of rrows)h+=rrow(a,false);
    h+=rrow(RC.all,true);
    h+='</tbody></table></div>';
    h+='<div class="legend">'
      +(RC.nf?'<b style="color:#e0a93b">フライング返還を反映</b>：非完走艇（F等）を含む買い目はその賭け金を投資から除外して回収率を算出（直近2日でF '+RC.nf+'レース）。':'')
      +'※ 直近2日のみ＝サンプル小。回収率は高配当1本で大きく振れる（特に3連単）。'
      +'確率帯別バックテスト(2026全体)ではどの帯も回収率100%未満（控除率約25%の壁）＝確率だけで機械的に買うと負ける。'
      +'各レース詳細の「買えてた場合の妙味」で、実配当が必要オッズを超えたか（＝買えてたら+EVか）を確認できる。</div>';
  }
  // 鉄板・標準・穴 と予想した場合の 的中率／回収率（系統は上のトグルに連動）
  if(RG&&RG.length&&RG.some(r=>r.n)){
    h+='<div class="sec" style="margin-top:22px;color:#cdd6e2;font-size:14px">鉄板・標準・穴 別の的中率と回収率（2026年〜）</div>';
    h+='<div class="meta">予想の荒れ度で3分類：<b>鉄板</b>＝本命確率≥65％ ／ <b>標準</b>＝45–65％ ／ <b>穴</b>＝本命確率&lt;45％（波乱含み）。'
      +'<b>荒れ度・点数はAPI本命確率で判定</b>。'
      +'買い目＝確率連動の変動点数（2連複≤3／3連単≤20点・各100円）。</div>';
    h+='<div class="meta" style="text-align:center"><span style="color:#5b9bd5">■</span> 本命1着　'
      +'<span style="color:#43c59e">■</span> 2連複（≤3点）　'
      +'<span style="color:#e0a93b">■</span> 3連単（≤20点）</div>';
    h+='<div style="text-align:center"><div style="font-size:13px;color:#cdd6e2;margin:8px 0 2px;font-weight:600">① 的中率（％）</div>'
      +grpBars(RG,[{k:'win',col:'#5b9bd5'},{k:'h2',col:'#43c59e'},{k:'h3',col:'#e0a93b'}])+'</div>';
    h+='<div style="text-align:center"><div style="font-size:13px;color:#cdd6e2;margin:14px 0 2px;font-weight:600">② 回収率（％・<span style="color:#d9745c">赤破線=100％損益分岐</span>）</div>'
      +grpBars(RG,[{k:'r2',col:'#43c59e'},{k:'r3',col:'#e0a93b'}],{ref:100})+'</div>';
    h+='<div class="legend"><b style="color:#5b9bd5">本命1着</b>＝本命（モデル1番手）が1着に来た割合（着順1つだけ）。鉄板ほど高い。'
      +'2連複（1・2着の組＝順不同）・3連単（1-2-3着）は組合せまで当てるぶん下がる。'
      +'回収率は払戻÷賭け金で、本命1着は単勝オッズが無いため対象外＝2連複・3連単のみ。'
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
  // ③展示妙味フラグ（参考のみ）: 展示タイム最速が朝AIの人気薄(4番手以下)なら、
  // 市場が過小評価する“人気薄の激走”を展示が先読みしている可能性。精査(before_value_deepdive):
  // 直前情報8日188Rで 本命×この艇のワイド 回収率≈100%(荒れ気味139%)・的中32%。ただしCIは100跨ぎ=未確定。
  if(best>=0){
    const psM=r.b.map(x=>x[1]||0);
    const mrank={};psM.map((p,i)=>[p,i]).sort((a,b)=>b[0]-a[0]).forEach((x,k)=>{mrank[x[1]]=k+1;});
    let favM=0;for(let i=1;i<6;i++)if(psM[i]>psM[favM])favM=i;
    if(mrank[best]>=4&&best!==favM){
      h+='<div style="margin:8px 0;padding:9px 12px;border:1px solid #3a5a44;border-radius:8px;background:#16241b;color:#b6e4c4;font-size:13px">'
        +'🔎 <b>展示妙味（参考）</b>：'+chip(best+1,'mc')+' が展示タイム最速だが朝AI評価は'+mrank[best]+'番手。'
        +'市場が見落としがちな“人気薄の激走”を展示が示唆。参考買い目＝<b>'+chip(favM+1,'mc')+'×'+chip(best+1,'mc')+' のワイド</b>。'
        +'<div style="margin-top:4px;color:#7fae8c;font-size:11px">※直前情報8日・188Rの小標本での傾向（ワイド回収率≈100%／荒れ気味のレースで高め・的中約32%）。統計的に未確定のため<b>確度は低く、少額の参考</b>に留めてください。</div></div>';
    }
  }
  // 展示後の予想（展示タイム/STを朝予想に軽くブレンド）
  const tp=tenjiPred(r);
  if(tp){
    const ps=r.b.map(x=>x[1]); let mh=0; for(let i=1;i<6;i++)if(ps[i]>ps[mh])mh=i;  // 朝の本命index
    const changed=tp.top!==mh;
    h+='<div class="tjp"><div class="tjhon">展示後の本命 '+chip(tp.top+1,'mc')+' '+(r.b[tp.top]?r.b[tp.top][0]:'')
      +' <span style="color:#43c59e">'+Math.round(tp.prob[tp.top]*100)+'%</span>'
      +(changed?' <span class="tchg">▲ 朝の本命は'+chip(mh+1,'mc')+'</span>':' <span class="tsame">朝と同じ</span>')+'</div>';
    h+='<div class="tjrk">展示後の順位　'+tp.order.map(i=>chip(i+1,'mc')).join(' ')+'</div></div>';
  }
  h+='<div class="legend">※ 展示＝発走直前の情報（自動取得：締切40分前から公式サイトを直接・約3分毎に反映。それ以外はOpenAPI直前フィード）。'
    +'<b style="color:#43c59e">緑</b>＝展示タイム最速、<b style="color:#e0a93b">橙</b>＝展示でイン(1c)進入。'
    +'「展示後の本命」＝朝のAI予想に展示タイム・展示STを軽く加味した補正（過去検証で本命的中+0.5〜1.4pt）。'
    +'<b style="color:#43c59e">展示があるレースは買い目もこの展示反映後で組みます</b>（backtestで総回収+0.9〜1.3pt）。ヘッドラインの本命/順位/1着確率と荒れ度・点数は朝予想のまま。</div>';
  return h;
}

// 「更新」ボタン：当日(base)の展示＋結果を serve_odds.py 経由で取得し D.races へ反映。
// 結果が出たレースは着順/決まり手/配当を入れて前日同様の結果表示に切替わる。
// 取得結果 o={races,fetched_at} を当日(base)レースへ反映。戻り値=反映件数。
function applyUpd(o){
  const R=o.races||{}; let nres=0,nex=0,nev=0;
  D.races.forEach(r=>{
    if(r.d!==D.base)return;                      // 反映は当日のみ
    const rec=R[r.id]; if(!rec)return;
    if(rec.ex){r.ex=rec.ex; nex++;}
    if(rec.ev!=null){r.ev=rec.ev; nev++;}        // 鉄板×EV≥1.5 で 🎯勝負 点灯
    if(rec.result){
      const rs=rec.result, fin=rs.fin||[];
      for(let w=1;w<=6;w++)r.b[w-1][2]=fin[w-1];   // 着順→前日同様の結果表示
      if(rs.km)r.km=rs.km;
      if(rs.po2!=null||rs.po3!=null||rs.po2f!=null)r.po=[rs.po2,rs.po3,rs.po2f!=null?rs.po2f:null];
      nres++;
    }
  });
  return {nres,nex,nev};
}
// 更新データの取得元（速度A）: まず /api/update（KV配信＝リビルド不要・即時。404なら未設定）、
// 次に静的 ./update.json（クラウド静的配信）、最後に更新サーバ /update（ローカル serve_odds.py / LAN）。
function upJSON(){
  const hd=D.base.replace(/-/g,'');
  return fetch('/api/update',{cache:'no-store'}).then(r=>{if(r.ok)return r.json();throw 0;})
    .catch(()=>fetch('update.json',{cache:'no-store'}).then(r=>{if(r.ok)return r.json();throw 0;}))
    .catch(()=>fetch('update?date='+hd+'&odds=1').then(r=>{if(!r.ok)throw 0;return r.json();}));
}
function fetchUpd(){ return upJSON(); }

// ── 案A: boatraceopenapi の previews フィード（展示/直前情報）をクライアント直読み ──
// GitHub Actions枠を消費しない広域ソース。ただし実測の更新は1日6〜8回（2〜4時間間隔・
// README公称は約30分）＋CDN max-age=600 なので発走前に間に合わないことが多い。
// 締切が近いレースは /api/tenji（公式直取り・下記）が優先して補う。
// 提供項目＝展示タイム/展示ST/チルト/進入コース/天候。結果・EVは含まないので update.json 側で補う。
// CORS: Access-Control-Allow-Origin:* で許可済み。オフライン/失敗時は静かに無視（従来動作）。
const PREVIEWS_URL='https://boatraceopenapi.github.io/previews/v2/today.json';
const WX_NUM=[null,'晴','曇り','雨','雪','風','霧'];   // race_weather_number 1..6
function pad2(n){return (n<10?'0':'')+n;}
// previews の1レース → r.ex（{time,st,tilt,course,parts,weather}）。展示未計測(タイム全0/欠)なら null。
function pvToEx(pv){
  const bo=pv.boats||{};
  const time=[null,null,null,null,null,null], st=time.slice(), tilt=time.slice(), course=time.slice();
  let any=false;
  for(let w=1;w<=6;w++){
    const b=bo[w]; if(!b)continue;
    const t=b.racer_exhibition_time;
    if(t!=null&&t>0){time[w-1]=t; any=true;}                    // 0=未計測 は null 扱い
    if(b.racer_start_timing!=null)st[w-1]=b.racer_start_timing; // 負=F（既存 tenjiPred が F 扱い）
    if(b.racer_tilt_adjustment!=null)tilt[w-1]=b.racer_tilt_adjustment;
    if(b.racer_course_number!=null)course[w-1]=b.racer_course_number;
  }
  if(!any)return null;                                          // 展示タイムが1つも無い＝展示前
  const weather={tenki:WX_NUM[pv.race_weather_number]||null,
                 winddir:pv.race_wind_direction_number!=null?pv.race_wind_direction_number:null,
                 wind:pv.race_wind!=null?pv.race_wind:null,
                 wave:pv.race_wave!=null?pv.race_wave:null,
                 temp:pv.race_temperature!=null?pv.race_temperature:null};
  return {time:time,st:st,tilt:tilt,course:course,parts:[null,null,null,null,null,null],weather:weather};
}
function fetchPreviews(){
  return fetch(PREVIEWS_URL,{cache:'no-store'}).then(r=>{if(!r.ok)throw 0;return r.json();});
}
// previews を当日(base)レースへ反映。展示があるレースの r.ex を previews の新鮮版で上書き（previews優先）。
// 結果・EV には一切触れない。戻り値＝反映した展示レース数。
function applyPreviews(pj){
  const arr=(pj&&pj.previews)||[]; if(!arr.length)return 0;
  const byId={};
  arr.forEach(pv=>{byId[pad2(pv.race_stadium_number)+String(pv.race_date).replace(/-/g,'')+pad2(pv.race_number)]=pv;});
  let n=0;
  D.races.forEach(r=>{
    if(r.d!==D.base)return;                                     // 反映は当日のみ
    if(r.exSrc==='official')return;                             // 公式直取り済み＝より新鮮なので previews で戻さない
    const pv=byId[r.id]; if(!pv)return;
    const ex=pvToEx(pv); if(!ex)return;                         // 展示前は上書きしない
    r.ex=ex; n++;
  });
  return n;
}
// 手動「更新」時に previews を即取得反映（結果/EVは updateAll 本体が別途取得）。
function refreshPreviews(){
  return fetchPreviews().then(pj=>{const n=applyPreviews(pj); if(n)render(); return n;}).catch(()=>0);
}

// ── 公式サイト直取り（/api/tenji・Cloudflare Pages Function）──
// previews フィードは実測2〜4時間おきで発走前に間に合わないため、締切が近いレースだけ
// 公式 beforeinfo を直接取得する（公表後≤3分で反映）。サーバ側90秒キャッシュ・
// 対象は同時0〜4レース程度なので公式サイトへの負荷は人が見るのと同程度。
const tenjiLast={};                                   // race_id → 最終取得エポックms（2分スロットル）
function raceMin(r){                                  // 締切までの分（r.tm='HH:MM'）。不明は null
  if(!r.tm)return null; const m=String(r.tm).match(/^(\d{1,2}):(\d{2})/); if(!m)return null;
  const now=new Date();
  return (new Date(now.getFullYear(),now.getMonth(),now.getDate(),+m[1],+m[2])-now)/60000;
}
function hasResult(r){return r.b.some(b=>b&&b[2]===1);}
function tenjiFetch(r){
  const now=Date.now();
  if(tenjiLast[r.id]&&now-tenjiLast[r.id]<120000)return Promise.resolve(false);
  tenjiLast[r.id]=now;
  const q='jcd='+r.id.slice(0,2)+'&rno='+(+r.id.slice(10,12))+'&hd='+r.id.slice(2,10);
  return fetch('/api/tenji?'+q,{cache:'no-store'}).then(x=>{if(!x.ok)throw 0;return x.json();})
    .then(o=>{ if(!o||!o.ex)return false; r.ex=o.ex; r.exSrc='official'; return true; })
    .catch(()=>false);                                // 失敗は previews/update.json にフォールバック
}
// 締切40分前〜締切後10分・結果未確定の当日レース（＋詳細表示中のレース）を直取り。
function tenjiSweep(){
  if(!IS_CLOUD)return Promise.resolve(false);         // /api/tenji はクラウドのみ
  const targets=[];
  D.races.forEach(r=>{
    if(r.d!==D.base||hasResult(r))return;
    const mn=raceMin(r); if(mn==null)return;
    if(mn<=40&&mn>=-10)targets.push(r);
  });
  if(sel!=null){const r=dayRaces()[sel];
    if(r&&r.d===D.base&&!hasResult(r)&&targets.indexOf(r)<0)targets.push(r);}
  if(!targets.length)return Promise.resolve(false);
  return Promise.all(targets.map(tenjiFetch)).then(a=>a.some(x=>x));
}

// ── 結果の公式直取り（/api/result・Cloudflare Pages Function）──
// results フィード(boatraceopenapi)も実測2〜4時間おきで確定直後に間に合わないため、
// 締切を過ぎた未確定レースだけ公式 raceresult を直接取得（確定後≤数分で反映）。
// applyUpd と同じく着順/決まり手/配当を D.races へ入れる。サーバ側120秒キャッシュ。
const resLast={};                                     // race_id → 最終取得エポックms（2分スロットル）
function resultFetch(r){
  const now=Date.now();
  if(resLast[r.id]&&now-resLast[r.id]<120000)return Promise.resolve(false);
  resLast[r.id]=now;
  const q='jcd='+r.id.slice(0,2)+'&rno='+(+r.id.slice(10,12))+'&hd='+r.id.slice(2,10);
  return fetch('/api/result?'+q,{cache:'no-store'}).then(x=>{if(!x.ok)throw 0;return x.json();})
    .then(o=>{
      const rs=o&&o.result; if(!rs||!rs.fin)return false;
      const fin=rs.fin; for(let w=1;w<=6;w++)r.b[w-1][2]=fin[w-1];   // 着順→結果表示に切替
      if(rs.km)r.km=rs.km;
      if(rs.po2!=null||rs.po3!=null||rs.po2f!=null)r.po=[rs.po2,rs.po3,rs.po2f!=null?rs.po2f:null];
      r.resSrc='official'; return true;
    })
    .catch(()=>false);                                // 失敗は update.json にフォールバック
}
// 締切後2分〜締切後90分・未確定の当日レース（＋詳細表示中）を直取り。確定したら以後スキップ。
function resultSweep(){
  if(!IS_CLOUD)return Promise.resolve(false);         // /api/result はクラウドのみ
  const targets=[];
  D.races.forEach(r=>{
    if(r.d!==D.base||hasResult(r))return;
    const mn=raceMin(r); if(mn==null)return;
    if(mn<=-2&&mn>=-90)targets.push(r);               // 締切2分後〜90分後（大幅遅延も拾う）
  });
  if(sel!=null){const r=dayRaces()[sel]; const mn=r?raceMin(r):null;
    if(r&&r.d===D.base&&!hasResult(r)&&mn!=null&&mn<=-2&&targets.indexOf(r)<0)targets.push(r);}
  if(!targets.length)return Promise.resolve(false);
  return Promise.all(targets.map(resultFetch)).then(a=>a.some(x=>x));
}

// ── 鉄板レースの実オッズ直取り（/api/odds・Cloudflare Pages Function）──
// backtest(2026-06・最終オッズ19日分・鉄板81R): 1点目2連単の実オッズ<2.0のレースを
// 見送ると回収88.8→98.9%(+10.1pt)・的中率も+4pt。超低オッズ帯は当たっても戻りが薄い。
// 対象は鉄板(API本命確率≥0.65)のみ・締切40分前〜締切・2分スロットル・サーバ側90秒キャッシュ
// なので公式サイトへの負荷は人が見るのと同程度。取得失敗・発売前(オッズ0)は表示なし。
const oddsLast={};                                    // race_id → 最終取得エポックms
function isTetsu(r){return Math.max(...r.ab)/1000>=0.65;}
function top2Combo(r){const t=plTop(betScore(r),2,1);return t.length?t[0][0]:null;}
function oddsFetch(r){
  const now=Date.now();
  if(oddsLast[r.id]&&now-oddsLast[r.id]<120000)return Promise.resolve(false);
  oddsLast[r.id]=now;
  const q='jcd='+r.id.slice(0,2)+'&rno='+(+r.id.slice(10,12))+'&hd='+r.id.slice(2,10);
  return fetch('/api/odds?'+q,{cache:'no-store'}).then(x=>{if(!x.ok)throw 0;return x.json();})
    .then(o=>{ if(!o||!o.o2)return false; r.o2=o.o2; return true; })
    .catch(()=>false);
}
// 締切40分前〜締切・結果未確定・鉄板の当日レース（＋詳細表示中の鉄板）のオッズを直取り。
function oddsSweep(){
  if(!IS_CLOUD)return Promise.resolve(false);         // /api/odds はクラウドのみ
  const targets=[];
  D.races.forEach(r=>{
    if(r.d!==D.base||hasResult(r)||!isTetsu(r))return;
    const mn=raceMin(r); if(mn==null)return;
    if(mn<=40&&mn>=0)targets.push(r);
  });
  if(sel!=null){const r=dayRaces()[sel];
    if(r&&r.d===D.base&&!hasResult(r)&&isTetsu(r)&&targets.indexOf(r)<0)targets.push(r);}
  if(!targets.length)return Promise.resolve(false);
  return Promise.all(targets.map(oddsFetch)).then(a=>a.some(x=>x));
}
// 鉄板の見送り判定。1点目2連単(買い目筆頭)の実オッズ<2.0→見送り推奨。
// オッズ未取得・発売前(0)・非鉄板は null＝何も表示しない（縮退安全）。
function tetsuOdds(r){
  if(!r.o2||!isTetsu(r))return null;
  const c=top2Combo(r); if(!c)return null;
  const o=r.o2[c.join('-')];
  if(o==null||o<=0)return null;                       // 0＝発売前・欠番＝判定不能
  return {odds:o,skip:o<2.0,c:c};
}
// 当日・締切前の鉄板オッズ署名。判定表示(0.1倍単位)が変わった時だけ再描画するため。
function oddsSig(){
  let s='';
  D.races.forEach(r=>{const t=(r.d===D.base&&!hasResult(r)&&r.o2)?tetsuOdds(r):null;
    if(t)s+=r.id+':'+t.odds.toFixed(1)+';';});
  return s;
}

// 当日レースの展示タイム/展示STの署名。値が変わった時だけ再描画するため（無変化のちらつき防止）。
function exSig(){
  let s='';
  D.races.forEach(r=>{ if(r.d===D.base&&r.ex&&r.ex.time)s+=r.id+':'+r.ex.time.join(',')+'/'+(r.ex.st||[]).join(',')+';'; });
  return s;
}
// スクロール位置と表示画面を保ったまま再描画（自動更新用）。
function rerenderKeepScroll(){
  const y=window.scrollY||document.documentElement.scrollTop||0;
  render();
  try{window.scrollTo(0,y);}catch(e){}
}
// 自動更新: previews / update.json / 公式直取り(/api/tenji) を静かに再取得。
// 展示や結果に変化があった時だけ、スクロール位置・表示中の画面を保ったまま再描画する。
// GitHub Actions 枠は消費しない。
function autoRefresh(){
  if(upBusy)return;                       // 手動更新中は触らない
  const before=exSig(), beforeO=oddsSig();
  Promise.allSettled([upJSON(),fetchPreviews()]).then(res=>{
    const u=res[0], p=res[1]; let changed=false;
    if(u.status==='fulfilled'&&u.value&&u.value.fetched_at&&u.value.fetched_at!==upFetched){
      applyUpd(u.value); upFetched=u.value.fetched_at; changed=true;   // 結果/EVが更新された
    }
    if(p.status==='fulfilled'&&p.value){ applyPreviews(p.value); }     // 展示は previews で上書き
    return Promise.all([tenjiSweep(),resultSweep(),oddsSweep()]).then(sw=>{  // 締切前=展示・オッズ／締切後=結果を公式直取り
      if(exSig()!==before)changed=true;                                // 展示が変わった
      if(sw[1])changed=true;                                           // 結果が確定した
      if(oddsSig()!==beforeO)changed=true;                             // 鉄板オッズ判定が変わった
      if(changed)rerenderKeepScroll();
    });
  }).catch(()=>{});
}

// クラウド: update.json を周期取得し、fetched_at が prev から変われば反映。tries回まで20秒間隔。
function pollUpd(prev,tries){
  if(tries<=0){upBusy=false;upErr=true;upMsg='反映待ちがタイムアウトしました。数分後にページを再読み込みしてください。';render();return;}
  upJSON().then(o=>{
    if(o.fetched_at&&o.fetched_at!==prev){
      const n=applyUpd(o); upFetched=o.fetched_at; upBusy=false; upErr=false;
      upMsg='更新 '+o.fetched_at+' ／ 結果 '+n.nres+'・展示 '+n.nex+(n.nev?'・EV '+n.nev:'')+'レースを反映';
      render();
    }else{ setTimeout(()=>pollUpd(prev,tries-1),20000); }
  }).catch(()=>setTimeout(()=>pollUpd(prev,tries-1),20000));
}
// クラウド「更新」: /api/refresh が Actions(boatrace-update) を起動→update.json生成→ポーリング反映。
function requestRefresh(){
  if(upBusy)return;
  upBusy=true; upErr=false;
  upMsg='収集をリクエスト中…（GitHubで取得→反映まで数分かかります）';
  selDate=D.base; tab='pred'; sel=null; render();
  fetch('/api/refresh',{method:'POST',cache:'no-store'})
    .then(r=>r.json().catch(()=>({status:r.ok?'queued':'error'})))
    .then(o=>{
      if(o.status==='already_running'){ upMsg='すでに収集中です。反映までお待ちください…'; }
      else if(o.status==='queued'){ upMsg='収集を開始しました。反映まで数分お待ちください…'; }
      else { upBusy=false; upErr=true; upMsg='起動に失敗しました（'+(o.message||o.status||'error')+'）。'; render(); return; }
      render();
      pollUpd(upFetched,30);   // 20秒×30=最大10分
    })
    .catch(()=>{ upBusy=false; upErr=true; upMsg='起動に失敗しました（通信エラー）。'; render(); });
}
// 「更新」ボタン：当日(base)の展示＋結果＋EVを取得して D.races へ反映。
function updateAll(){
  if(upBusy)return;
  refreshPreviews();                       // 案A: 展示は最新フィードを即反映（結果/EVは以下で取得）
  if(IS_CLOUD){requestRefresh();return;}   // クラウド=Actions起動→ポーリング反映
  // ファイル直開き(file://)では取得不可。更新サーバ版ページへ移動する
  // （サーバ未起動なら接続不可＝ランチャー/常駐サーバで起動が必要）。
  if(location.protocol==='file:'){location.href='http://localhost:8787/today.html#update';return;}
  upBusy=true; upErr=false;
  upMsg='取得中… 当日の展示・結果を収集しています（開催場数により数十秒かかることがあります）';
  selDate=D.base; tab='pred'; sel=null; render();
  fetchUpd().then(o=>{
    const n=applyUpd(o);
    upBusy=false; upErr=false;
    upMsg='更新 '+(o.fetched_at||'')+' ／ 結果 '+n.nres+'レース・展示 '+n.nex+'レース'+(n.nev?'・EV '+n.nev+'レース':'')+'を反映';
    render();
  }).catch(()=>{
    upBusy=false; upErr=true;
    upMsg='取得失敗：発走前は展示・結果がまだ無いことがあります。ローカルでは py -3.13 serve_odds.py 経由で開いてください。';
    render();
  });
}

function render(){
  if(tab==='stats'){
    root.innerHTML=statsView();
    document.querySelectorAll('.tb[data-t]').forEach(b=>b.onclick=()=>{tab=b.dataset.t;sel=null;render();});
    document.querySelectorAll('.upbtn').forEach(b=>b.onclick=updateAll);
    document.querySelectorAll('.asb').forEach(b=>b.onclick=()=>{anaScope=b.dataset.as;render();});   // 穴目の対象帯（回収率グラフ・券種別）
    document.querySelectorAll('.sortb').forEach(b=>b.onclick=()=>{
      const s=b.dataset.st==='v'?vsort:rsort, c=b.dataset.col;
      if(s.c===c){s.d=-s.d;}else{s.c=c;s.d=-1;}   // 同じ列=方向反転／別列=降順から
      render();
    });
    return;
  }
  const rby=backY; backY=null;   // 「一覧へ戻る」で保存した位置。通常描画では常に先頭へ
  root.innerHTML = sel===null ? nav()+listView() : detailView(dayRaces()[sel]);
  if(rby!=null){try{window.scrollTo(0,rby);}catch(e){}}else{window.scrollTo(0,0);}
  if(sel===null){
    document.querySelectorAll('.tb[data-t]').forEach(b=>b.onclick=()=>{tab=b.dataset.t;sel=null;render();});
    document.querySelectorAll('.upbtn').forEach(b=>b.onclick=updateAll);
    document.querySelectorAll('.dbtn').forEach(b=>b.onclick=()=>{selDate=b.dataset.d;cur='ALL';render();});
    document.querySelectorAll('.vbtn').forEach(b=>b.onclick=()=>{cur=b.dataset.v;render();});
    document.querySelectorAll('.lvbtn').forEach(b=>b.onclick=()=>{lvlFilter=b.dataset.lv;render();});   // 帯フィルタ（鉄板/波乱）
    document.querySelectorAll('.asb').forEach(b=>b.onclick=()=>{backY=window.scrollY||document.documentElement.scrollTop||0;anaScope=b.dataset.as;render();});   // 対象帯の切替は現在位置を保つ（REALの現在レースへ飛ばさない）
    document.querySelectorAll('.row').forEach(rw=>rw.onclick=()=>{listY=window.scrollY||document.documentElement.scrollTop||0;sel=+rw.dataset.i;render();});
    if(rby==null&&cur==='REAL'){const t=document.getElementById('nowtarget');if(t)t.scrollIntoView({block:'center'});}
  }else{
    document.querySelector('.back').onclick=()=>{sel=null;backY=listY;render();};
    const _pv=document.querySelector('.pnav.prev'); if(_pv&&!_pv.disabled)_pv.onclick=()=>{sel--;render();};
    const _nx=document.querySelector('.pnav.next'); if(_nx&&!_nx.disabled)_nx.onclick=()=>{sel++;render();};
    // 詳細を開いたら、そのレースを公式から即取得（締切後は結果・締切前は展示。2分スロットル）。
    const dr=dayRaces()[sel];
    if(IS_CLOUD&&dr&&dr.d===D.base&&!hasResult(dr)){
      const mn=raceMin(dr), fn=(mn!=null&&mn<=-2)?resultFetch:tenjiFetch;
      fn(dr).then(ch=>{ if(ch&&sel!=null&&dayRaces()[sel]===dr)rerenderKeepScroll(); });
      // 鉄板は実オッズも即取得（見送り判定）。締切後は不要。
      if(isTetsu(dr)&&!(mn!=null&&mn<=-2))
        oddsFetch(dr).then(ch=>{ if(ch&&sel!=null&&dayRaces()[sel]===dr)rerenderKeepScroll(); });
    }
  }
}
// file:// で直接開かれ、かつ更新サーバが起動中ならサーバ版へ自動で切替える
// （更新ボタンは serve_odds.py 経由でないと動かないため）。サーバ停止中は
// no-cors fetch が失敗→何もしない＝そのままオフライン閲覧できる。
if(location.protocol==='file:'){
  fetch('http://localhost:8787/today.html',{mode:'no-cors',cache:'no-store'})
    .then(()=>{location.replace('http://localhost:8787/today.html');})
    .catch(()=>{});
}else{
  // クラウド/ローカルサーバ配信では、ページ表示時に更新データを自動反映（ボタン押下不要）。
  //  ・update.json(/api/update) ＝ 結果・EV・展示（自前30分バッチ）
  //  ・previews フィード（案A） ＝ 展示のみだが鮮度が高い。展示は previews を優先して上書き。
  // どちらも無ければ静かに無視（従来どおり手動更新で取得）。
  Promise.allSettled([upJSON(),fetchPreviews()]).then(res=>{
    const u=res[0], p=res[1]; let n={nres:0,nex:0,nev:0}, npv=0, fa='';
    if(u.status==='fulfilled'&&u.value){ n=applyUpd(u.value); fa=u.value.fetched_at||''; upFetched=fa; }
    if(p.status==='fulfilled'&&p.value){ npv=applyPreviews(p.value); }   // 展示は previews で上書き（新鮮）
    const nexFinal=D.races.filter(r=>r.d===D.base&&r.ex).length;
    upMsg='自動更新 '+(fa||'')+' ／ 結果 '+n.nres+'・展示 '+nexFinal+(n.nev?'・EV '+n.nev:'')+' レース反映'
      +(npv?' ・展示は最新フィード('+npv+'R)':'');
    render();
    Promise.all([tenjiSweep(),resultSweep(),oddsSweep()]).then(sw=>{ if(sw[0]||sw[1]||sw[2])rerenderKeepScroll(); });   // 締切前=展示・オッズ／締切後=結果を公式直取りで即最新化
  }).catch(()=>{});
  // 開いたまま展示公表を待てるよう、3分ごとに自動再取得（previews＋update.json＋公式直取り）。
  // 変化があった時だけ再描画するので、表示を邪魔しない。タブ非表示中はスキップ。
  setInterval(()=>{ if(!document.hidden)autoRefresh(); }, 180000);
  // スマホでタブ/アプリに戻った瞬間も即時再取得（3分間隔を待たない）。
  document.addEventListener('visibilitychange',()=>{ if(!document.hidden)autoRefresh(); });
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
