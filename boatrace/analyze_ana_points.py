# -*- coding: utf-8 -*-
"""
穴予想（波乱帯）の「点数を固定6点にすべきか／自由度を持たせるべきか」を検証する。

現状の穴予想（build_today._taikou_ref）＝
  対抗(モデル2番手)アタマ固定 × {本命(1番手), 3番手, 4番手} を2・3着に流す = 3P2 = 6点固定。

本スクリプトは train<=260430 固定モデルの honest OOS 予測(predict_win.csv)を使い、
波乱帯レース（モデル本命確率 top1 p_win < 0.45）だけを対象に、対抗アタマ流しの
3連単を「何点買うか」を変えたときの回収率を実配当(K-file)で比較する。

比較する点数構成（すべて対抗=モデル2番手をアタマに固定）:
  A) 現状の固定フォーメーション: 相手プール={本命,3番手,4番手} の6点
  B) 対抗アタマ 全20点(相手=残り5艇の2着×3着)を PL確率で並べ、上位k点（k=2,4,6,8,12,20）
  C) 本命確率(荒れの深さ)で点数を可変にするルール案 いくつか

さらに、対抗アタマ流しを PL確率順に並べた「1点目,2点目,…」の“点別”限界回収率を出し、
鉄板3連単で見つかった「4点目の崖」に相当する構造が穴でもあるかを見る（＝どこまで買う価値があるか）。

回収率 = Σ払戻(的中時のみ・100円あたり配当) / (点数 × 100 × レース数) × 100
"""
import csv
import glob
import itertools
import re
from collections import defaultdict

from features_player_history import VENUE_CODE

ENC = "cp932"
BAND_MAX = 0.45          # 波乱帯の上限（モデル top1 p_win）


# ───────── PL(Plackett-Luce)確率 ─────────
def pl_prob(s, combo):
    p, rem = 1.0, sum(s)
    for w in combo:
        p *= s[w - 1] / rem
        rem -= s[w - 1]
    return p


# ───────── データ読み込み ─────────
def load_predict(path="predict_win.csv"):
    """race_id -> {枠(1-6): (p_win, finish_rank)} 。6艇そろい・全p_win・着順ありのみ。"""
    races = defaultdict(dict)
    with open(path, encoding=ENC, newline="") as f:
        r = csv.reader(f)
        h = next(r)
        i_rid, i_lane, i_p, i_fin = 0, 1, h.index("p_win"), h.index("finish_rank")
        for row in r:
            if not row:
                continue
            try:
                lane = int(row[i_lane]); p = float(row[i_p])
            except ValueError:
                continue
            try:
                fin = int(row[i_fin])
            except (ValueError, IndexError):
                fin = None
            races[row[i_rid]][lane] = (p, fin)
    return races


def load_tri_payouts():
    """race_id -> 3連単配当(int, 100円あたり)。data/k*.csv(cp932) から。"""
    payout = {}
    for kp in glob.glob("data/k*.csv"):
        with open(kp, encoding=ENC) as f:
            for r in csv.DictReader(f):
                code = VENUE_CODE.get(r["会場"], "00")
                y, mo, dd = r["日付"].split("/")
                rid = f"{code}{int(y):04d}{int(mo):02d}{int(dd):02d}{int(r['レース']):02d}"
                if rid in payout:
                    continue
                try:
                    payout[rid] = int(r["3連単_配当"])
                except (ValueError, KeyError):
                    pass
    return payout


# ───────── 対抗アタマ流しの候補点 ─────────
def taikou_candidates_ranked(s):
    """対抗(モデル2番手)アタマの3連単全20点を PL確率降順で返す。要素=(枠tuple, pl_prob)。"""
    order = sorted(range(1, 7), key=lambda w: s[w - 1], reverse=True)
    head = order[1]                       # 対抗＝2番手
    others = [w for w in order if w != head]
    combos = [(head, a, b) for a, b in itertools.permutations(others, 2)]
    combos.sort(key=lambda c: pl_prob(s, list(c)), reverse=True)
    return combos, head, order


def fixed_formation(order, head):
    """現状の固定6点＝対抗アタマ×{本命,3番手,4番手}の2-3着流し。"""
    T = [order[0], order[2], order[3]]    # 本命,3番手,4番手
    return [(head, a, b) for a in T for b in T if a != b]


# ───────── メイン ─────────
def main():
    races = load_predict()
    payout = load_tri_payouts()

    # 波乱帯レースを抽出
    band = []   # (rid, s[6], actual_trifecta or None, hon)
    for rid, boats in races.items():
        if len(boats) != 6:
            continue
        s = [boats.get(w, (0.0, None))[0] for w in range(1, 7)]
        if any(v <= 0 for v in s):
            continue
        hon = max(s)
        if hon >= BAND_MAX:
            continue
        fins = {w: boats[w][1] for w in range(1, 7)}
        order_fin = sorted([w for w in range(1, 7) if fins[w] is not None and fins[w] >= 1],
                           key=lambda w: fins[w])
        if len(order_fin) < 3 or fins[order_fin[0]] != 1:
            actual = None            # 集計対象外（着順欠損/失格でtrifecta不成立）
        else:
            actual = (order_fin[0], order_fin[1], order_fin[2])
        if rid not in payout:
            continue
        band.append((rid, s, actual, hon))

    n_all = len(band)
    n_valid = sum(1 for _, _, a, _ in band if a is not None)
    print(f"波乱帯レース(モデル本命確率<{BAND_MAX}): {n_all}  （trifecta成立 {n_valid}）")
    print(f"平均本命確率: {sum(h for *_ , h in band)/n_all:.3f}")
    print()

    # ═══ 1) 点数構成ごとの回収率（全波乱帯・一律ルール） ═══
    def roi_for_topk(k):
        stake = hit = ret = 0
        for rid, s, actual, hon in band:
            combos, head, order = taikou_candidates_ranked(s)
            buy = combos[:k]
            stake += k * 100
            if actual is not None and tuple(actual) in buy:
                hit += 1
                ret += payout[rid]
        return stake, hit, ret

    def roi_for_fixed():
        stake = hit = ret = 0
        for rid, s, actual, hon in band:
            _, head, order = taikou_candidates_ranked(s)
            buy = fixed_formation(order, head)
            stake += len(buy) * 100
            if actual is not None and tuple(actual) in buy:
                hit += 1
                ret += payout[rid]
        return stake, hit, ret, len(buy)

    print("【A/B】対抗アタマ流し：買う点数を変えたときの回収率（全波乱帯 一律）")
    print(f"  {'構成':<22}{'点/R':>5}{'的中率':>8}{'投資':>13}{'回収':>13}{'回収率':>8}")
    st, hi, rt, kfix = roi_for_fixed()
    print(f"  {'現状=固定フォメ(本/3/4)':<20}{kfix:>5}{hi/n_all*100:>7.1f}%"
          f"{st:>13,}{rt:>13,}{rt/st*100:>7.1f}%")
    for k in (2, 4, 6, 8, 12, 20):
        st, hi, rt = roi_for_topk(k)
        tag = "PL上位%d点" % k
        print(f"  {tag:<20}{k:>5}{hi/n_all*100:>7.1f}%{st:>13,}{rt:>13,}{rt/st*100:>7.1f}%")
    print()

    # ═══ 2) 点別 限界回収率（対抗アタマをPL確率順に1点ずつ） ═══
    print("【点別】対抗アタマ流しを PL確率順に並べた r 点目だけの回収率")
    print("  （その1点を全波乱帯で買った場合の回収率＝そこまで買う価値があるかの限界値）")
    per = defaultdict(lambda: [0, 0, 0])   # rank -> [n, hit, ret]
    for rid, s, actual, hon in band:
        combos, head, order = taikou_candidates_ranked(s)
        for r, c in enumerate(combos, 1):
            per[r][0] += 1
            if actual is not None and tuple(actual) == c:
                per[r][1] += 1
                per[r][2] += payout[rid]
    print(f"  {'r点目':>5}{'的中数':>7}{'回収率(その1点)':>16}  {'累積回収率(1..r点)':>18}")
    cum_st = cum_rt = 0
    for r in range(1, 21):
        n, h, rt = per[r]
        cum_st += n * 100; cum_rt += rt
        print(f"  {r:>5}{h:>7}{rt/(n*100)*100:>14.1f}%   {cum_rt/cum_st*100:>16.1f}%")
    print()

    # ═══ 3) 本命確率サブ帯 × 点数 の回収率（可変化の余地） ═══
    print("【C】本命確率サブ帯 × 点数：どの深さで何点が良いか（回収率%）")
    subbands = [(0.40, 0.45), (0.35, 0.40), (0.30, 0.35), (0.25, 0.30), (0.0, 0.25)]
    ks = (2, 4, 6, 8, 12, 20)
    header = "  " + f"{'本命帯':<12}{'R数':>6}" + "".join(f"{'k='+str(k):>8}" for k in ks)
    print(header)
    # 各サブ帯・各kの回収率
    for lo, hi in subbands:
        sub = [b for b in band if lo <= b[3] < hi]
        if not sub:
            continue
        row = f"  {f'{lo:.2f}-{hi:.2f}':<12}{len(sub):>6}"
        best_k = None; best_roi = -1
        for k in ks:
            st = hit = rt = 0
            for rid, s, actual, hon in sub:
                combos, head, order = taikou_candidates_ranked(s)
                buy = combos[:k]
                st += k * 100
                if actual is not None and tuple(actual) in buy:
                    rt += payout[rid]
            roi = rt / st * 100
            if roi > best_roi:
                best_roi, best_k = roi, k
            row += f"{roi:>8.1f}"
        print(row + f"   最良 k={best_k}")
    print()

    # ═══ 4) 固定6点 vs 可変ルール案の総合比較 ═══
    print("【総合】固定6点 vs いくつかの可変ルール（全波乱帯）")

    def eval_rule(fn, label):
        st = hit = rt = 0
        for rid, s, actual, hon in band:
            combos, head, order = taikou_candidates_ranked(s)
            k = fn(hon)
            buy = combos[:k]
            st += k * 100
            if actual is not None and tuple(actual) in buy:
                hit += 1; rt += payout[rid]
        print(f"  {label:<34}投資{st:>12,} 回収{rt:>12,} 回収率{rt/st*100:>6.1f}%  的中{hit/n_all*100:>5.1f}%")

    # 固定6（PL上位6でなく現状フォメ）
    st, hi, rt, kfix = roi_for_fixed()
    print(f"  {'現状=固定フォメ 6点':<34}投資{st:>12,} 回収{rt:>12,} 回収率{rt/st*100:>6.1f}%  的中{hi/n_all*100:>5.1f}%")
    eval_rule(lambda h: 6, "PL上位6点固定")
    eval_rule(lambda h: 4 if h >= 0.40 else 6 if h >= 0.30 else 8, "可変案1: 深いほど広げる(4/6/8)")
    eval_rule(lambda h: 2 if h >= 0.40 else 4 if h >= 0.30 else 6, "可変案2: 浅は絞る(2/4/6)")
    eval_rule(lambda h: 4, "PL上位4点固定")
    eval_rule(lambda h: 2, "PL上位2点固定")
    print()

    # ═══ 5) ブートストラップ信頼区間（レース単位リサンプル） ═══
    # 各レースの (stake, return) をルールごとに前計算し、レース単位で復元抽出して回収率の分布を作る。
    import random
    random.seed(42)

    def race_vectors(rule_k):
        """rule_k(hon)->k。各レースの (stake, return) を返す。fixed=Noneで現状フォメ。"""
        v = []
        for rid, s, actual, hon in band:
            combos, head, order = taikou_candidates_ranked(s)
            if rule_k is None:
                buy = fixed_formation(order, head)
            else:
                buy = combos[:rule_k(hon)]
            st = len(buy) * 100
            rt = payout[rid] if (actual is not None and tuple(actual) in buy) else 0
            v.append((st, rt))
        return v

    rules = {
        "現状=固定フォメ6点": None,
        "PL上位2点固定":     (lambda h: 2),
        "PL上位6点固定":     (lambda h: 6),
        "可変(浅8/深2)":     (lambda h: 8 if h >= 0.40 else 2),
    }
    print("【CI】回収率の95%ブートストラップ信頼区間（レース単位2000回リサンプル）")
    B = 2000
    n = n_all
    for label, rk in rules.items():
        vec = race_vectors(rk)
        point = sum(r for _, r in vec) / sum(s for s, _ in vec) * 100
        rois = []
        for _ in range(B):
            ss = rr = 0
            for _ in range(n):
                s_, r_ = vec[random.randrange(n)]
                ss += s_; rr += r_
            rois.append(rr / ss * 100)
        rois.sort()
        lo, hiq = rois[int(0.025 * B)], rois[int(0.975 * B)]
        print(f"  {label:<18} 回収率 {point:5.1f}%  95%CI [{lo:4.1f}, {hiq:4.1f}]")
    print()

    # ═══ 6) 時系列ホールドアウト（前半で規則決定→後半で評価＝正直な検証） ═══
    # 過剰適合を避けるため、サブ帯別の最良kを「前半レース」だけで決め、
    # その規則を「後半レース」で固定フォメ6点と比べる。
    band_sorted = sorted(band, key=lambda b: b[0][2:10])   # 日付(rid[2:10])昇順
    cut = len(band_sorted) // 2
    train, test = band_sorted[:cut], band_sorted[cut:]
    d_tr = (train[0][0][2:10], train[-1][0][2:10])
    d_te = (test[0][0][2:10], test[-1][0][2:10])
    print("【OOS】時系列ホールドアウト検証（多重比較の過剰適合チェック）")
    print(f"  学習(規則決定): {d_tr[0]}–{d_tr[1]}  {len(train)}R   /   "
          f"検証(評価): {d_te[0]}–{d_te[1]}  {len(test)}R")

    subbands = [(0.40, 0.45), (0.35, 0.40), (0.30, 0.35), (0.25, 0.30), (0.0, 0.25)]
    ks = (2, 4, 6, 8, 12, 20)

    def best_k_map(data):
        m = {}
        for lo, hi in subbands:
            sub = [b for b in data if lo <= b[3] < hi]
            if not sub:
                m[(lo, hi)] = 6; continue
            best_k, best_roi = 6, -1
            for k in ks:
                st = rt = 0
                for rid, s, actual, hon in sub:
                    combos, head, order = taikou_candidates_ranked(s)
                    buy = combos[:k]
                    st += k * 100
                    if actual is not None and tuple(actual) in buy:
                        rt += payout[rid]
                roi = rt / st * 100 if st else 0
                if roi > best_roi:
                    best_roi, best_k = roi, k
            m[(lo, hi)] = best_k
        return m

    km = best_k_map(train)
    print("  前半で選んだサブ帯別最良k: " +
          "  ".join(f"{lo:.2f}-{hi:.2f}:{km[(lo,hi)]}" for lo, hi in subbands))

    def eval_on(data, kfn):
        st = rt = hit = 0
        for rid, s, actual, hon in data:
            combos, head, order = taikou_candidates_ranked(s)
            buy = kfn(s, order, head, hon, combos)
            st += len(buy) * 100
            if actual is not None and tuple(actual) in buy:
                hit += 1; rt += payout[rid]
        return st, rt, hit, len(data)

    def kfn_variable(s, order, head, hon, combos):
        for lo, hi in subbands:
            if lo <= hon < hi:
                return combos[:km[(lo, hi)]]
        return combos[:6]

    def kfn_fixed(s, order, head, hon, combos):
        return fixed_formation(order, head)

    for label, fn in (("後半：前半学習の可変ルール", kfn_variable),
                      ("後半：現状の固定フォメ6点", kfn_fixed)):
        st, rt, hit, nd = eval_on(test, fn)
        print(f"  {label:<26} 投資{st:>10,} 回収{rt:>10,} 回収率{rt/st*100:>6.1f}%  的中{hit/nd*100:>5.1f}%")


if __name__ == "__main__":
    main()
