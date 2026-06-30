# -*- coding: utf-8 -*-
"""穴狙いの探索バックテスト（本番非搭載・検討用）。

ユーザー要望:
  - 1着が1番人気(=APIスコア最大)以外になりやすいレースを、
    モーター/選手評価から「混戦」として広く検出し、
  - 穴の出目を広く買って、日によって回収率100%を狙える買い方を探す。

実オッズではなく K-file の実配当(2連単/3連単)で回収率を評価する。
混戦フィルタ × 買い方(箱)を総当たりで比較し、全体回収率と
「日別回収率の分布(100%超の日の割合・中央値・最大)」を出す。
"""
import argparse
import itertools
from collections import defaultdict

import build_today as B


def load_all(rel_path, pred_path, hist_path, since):
    rel = B.load(rel_path)
    pred = {(r["race_id"], r["枠番"]): r for r in B.load(pred_path)}
    hist = {(r["race_id"], r["枠番"]): r for r in B.load(hist_path)}
    api_map = B.build_api_scores(rel)

    races = {}
    for r in rel:
        if r["日付"] < since:
            continue
        rid = r["race_id"]
        try:
            w = int(r["枠番"])
        except (ValueError, KeyError):
            continue
        pr = pred.get((rid, r["枠番"]), {})
        h = hist.get((rid, r["枠番"]), {})
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            fin = None
        rc = races.setdefault(rid, {"c": r["場コード"], "v": r["会場"], "d": r["日付"],
                                    "fin": {}, "api": {}, "model": {},
                                    "mrank": {}, "strank": {}, "cl": {},
                                    "mrate": {}, "wrank": {}, "lw": {}})
        rc["fin"][w] = fin
        rc["api"][w] = api_map.get((rid, w))
        rc["model"][w] = B.to_float(pr.get("p_win"))
        rc["mrank"][w] = B.to_float(r.get("motor_rank_in_race"))
        rc["strank"][w] = B.to_float(r.get("st_rank_in_race"))
        rc["cl"][w] = B.to_float(r.get("class_ord"))
        rc["mrate"][w] = B.to_float(r.get("motor_top2_rate"))
        rc["wrank"][w] = B.to_float(r.get("winrate_rank_in_race"))
        rc["lw"][w] = B.to_float(h.get("lane_win_rate"))
    payout = B.load_payouts(sorted({r["日付"] for r in rel if r["日付"] >= since}))
    return races, payout


# ───────── 混戦フィルタ群: race rc -> bool（買う対象レースか）─────────
def _api_vec(rc):
    s = [rc["api"].get(w) for w in range(1, 7)]
    return s if all(x is not None for x in s) else None


def f_all(rc, s, hon, favw):
    return True


def f_hon45(rc, s, hon, favw):
    return hon < 0.45


def f_hon40(rc, s, hon, favw):
    return hon < 0.40


def f_hon35(rc, s, hon, favw):
    return hon < 0.35


def f_fav_weak_motor(rc, s, hon, favw):
    """1番人気のモーターが格別でない(レース内2位以下)＝混戦の芽。"""
    mr = rc["mrank"].get(favw)
    return hon < 0.45 and (mr is None or mr >= 2)


def f_strong_rival(rc, s, hon, favw):
    """1番人気が弱め(hon<0.45)で、対抗にモーター1位 or A1級が居る。"""
    if hon >= 0.45:
        return False
    for w in range(1, 7):
        if w == favw:
            continue
        if rc["mrank"].get(w) == 1 or rc["cl"].get(w) == 4:
            return True
    return False


def f_spread(rc, s, hon, favw):
    """有力艇が多い: APIで勝率12%以上の枠が3つ以上＝割れている。"""
    return hon < 0.45 and sum(1 for x in s if x >= 0.12) >= 3


FILTERS = {
    "all": f_all, "hon<45": f_hon45, "hon<40": f_hon40, "hon<35": f_hon35,
    "fav弱motor": f_fav_weak_motor, "対抗強": f_strong_rival, "割れ": f_spread,
}


# ───────── 買い方(箱)群: (rc,s,hon,favw)-> list of combos(枠tuple) ─────────
def _topk_api(s, k):
    return [i + 1 for i in sorted(range(6), key=lambda i: s[i], reverse=True)[:k]]


def _blend_rank(rc):
    """モーター/選手評価のブレンドで枠を並べる(混戦時の1着候補)。
    api確率 + 機力(motorランク逆) + ST(逆) + 級別 を合成。1=最有力。"""
    sc = {}
    for w in range(1, 7):
        a = (rc["api"].get(w) or 0)
        mr = rc["mrank"].get(w) or 6
        sr = rc["strank"].get(w) or 6
        cl = rc["cl"].get(w) or 1
        sc[w] = a + 0.04 * (6 - mr) + 0.03 * (6 - sr) + 0.02 * (cl - 1)
    return sorted(range(1, 7), key=lambda w: sc[w], reverse=True)


def b_box2_3(rc, s, hon, favw):
    L = _topk_api(s, 3)
    return list(itertools.permutations(L, 2))


def b_box2_4(rc, s, hon, favw):
    L = _topk_api(s, 4)
    return list(itertools.permutations(L, 2))


def b_box3_4(rc, s, hon, favw):
    L = _topk_api(s, 4)
    return list(itertools.permutations(L, 3))


def b_box3_5(rc, s, hon, favw):
    L = _topk_api(s, 5)
    return list(itertools.permutations(L, 3))


def b_favflop3(rc, s, hon, favw):
    """1番人気アタマ無し3連単: 1着=対抗(top5の非本命)、2-3着=top5自由。
    本命が飛んだ時の大穴を取りに行く。"""
    L = _topk_api(s, 5)
    heads = [w for w in L if w != favw]
    out = []
    for h in heads:
        for a, b in itertools.permutations([w for w in L if w != h], 2):
            out.append((h, a, b))
    return out


def b_blendbox3_4(rc, s, hon, favw):
    """モーター/選手ブレンド上位4の3連単ボックス。"""
    L = _blend_rank(rc)[:4]
    return list(itertools.permutations(L, 3))


def b_blendflop3(rc, s, hon, favw):
    """ブレンド上位5、ただし1番人気(api最大)はアタマから外す。"""
    L = _blend_rank(rc)[:5]
    heads = [w for w in L if w != favw]
    out = []
    for h in heads:
        for a, b in itertools.permutations([w for w in L if w != h], 2):
            out.append((h, a, b))
    return out


BUILDERS = {
    "2連箱3": (b_box2_3, 2), "2連箱4": (b_box2_4, 2),
    "3連箱4": (b_box3_4, 3), "3連箱5": (b_box3_5, 3),
    "本命飛3連": (b_favflop3, 3), "ﾌﾞﾚﾝﾄﾞ箱4": (b_blendbox3_4, 3),
    "ﾌﾞﾚﾝﾄﾞ飛3連": (b_blendflop3, 3),
}


def run(races, payout, filt, builder, kind, since):
    """全体集計 + 日別回収率。kind=2/3(2連単/3連単)。"""
    inv = 0
    ret = 0
    nbet = 0
    nhit = 0
    day_inv = defaultdict(int)
    day_ret = defaultdict(int)
    for rid, rc in races.items():
        if len(rc["fin"]) != 6:
            continue
        s = _api_vec(rc)
        if s is None:
            continue
        fins = [rc["fin"][w] for w in range(1, 7)]
        if any(f is None for f in fins):
            continue
        order = sorted([w for w in range(1, 7) if fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < kind or fins[order[0] - 1] != 1:
            continue
        hon = max(s)
        favw = max(range(1, 7), key=lambda w: s[w - 1])
        if not filt(rc, s, hon, favw):
            continue
        combos = builder(rc, s, hon, favw)
        combos = list(dict.fromkeys(combos))   # 重複排除
        if not combos:
            continue
        cost = len(combos) * 100
        po = payout.get(rid, (0, 0))
        actual = tuple(order[:kind])
        win = po[kind - 2] if actual in set(combos) else 0
        inv += cost
        ret += win
        nbet += 1
        nhit += 1 if win else 0
        day_inv[rc["d"]] += cost
        day_ret[rc["d"]] += win

    roi = ret / inv * 100 if inv else 0
    hitr = nhit / nbet * 100 if nbet else 0
    # 日別回収率分布
    days = sorted(day_inv)
    day_rois = [day_ret[d] / day_inv[d] * 100 for d in days if day_inv[d]]
    n100 = sum(1 for x in day_rois if x >= 100)
    day_rois_sorted = sorted(day_rois)
    med = day_rois_sorted[len(day_rois_sorted) // 2] if day_rois_sorted else 0
    mx = max(day_rois) if day_rois else 0
    avg_pts = (inv / nbet / 100) if nbet else 0
    return {
        "n": nbet, "hit": hitr, "roi": roi, "inv": inv, "ret": ret,
        "pts": avg_pts, "nday": len(day_rois), "n100": n100,
        "p100": (n100 / len(day_rois) * 100 if day_rois else 0),
        "med": med, "max": mx,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--hist", default="features_player_history.csv")
    ap.add_argument("--since", default="2026-01-01")
    args = ap.parse_args()

    print(f"読込 since={args.since} …")
    races, payout = load_all(args.rel, args.pred, args.hist, args.since)
    print(f"対象レース {len(races)} / 配当データ {len(payout)}\n")

    print(" フィルタ      買い方        R数  平均点 的中%  全体回収%   日数 100%超日% 日中央% 日最大%")
    print(" " + "-" * 92)
    for fname, filt in FILTERS.items():
        for bname, (builder, kind) in BUILDERS.items():
            r = run(races, payout, filt, builder, kind, args.since)
            if r["n"] == 0:
                continue
            print(f" {fname:<10s} {bname:<10s} {r['n']:6d} {r['pts']:5.0f} "
                  f"{r['hit']:5.1f} {r['roi']:7.1f}%  {r['nday']:5d} "
                  f"{r['p100']:6.1f}% {r['med']:6.1f}% {r['max']:7.0f}%")
        print()
    print("※全体回収%>100で長期プラス。控除率約25%＝無選別の理論線≒75%。")
    print("  『100%超日%』＝その日トータルで100%を超えた日の割合（高分散なら跳ねる日は出る）。")


if __name__ == "__main__":
    main()
