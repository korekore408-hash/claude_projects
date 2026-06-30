# -*- coding: utf-8 -*-
"""ロマン砲：回収率を無視して『好奇心・一発期待』で出目を選ぶ（本番非搭載）。

固めの穴（確率の薄い所を点で取る）ではなく、
『軽視されているが“一発の武器”を持つ艇』を理由付きで拾い、
当たれば万舟になる夢の出目を1〜2点で組む。

武器シグナル（API予想で3番手以下＝軽視が条件）:
  🔧 モーターがレース内1位 / 🔧2位
  ⚡ 展示前ST評価がレース内1位
  🅰 A1級なのに外枠（不利枠の格上＝化けたら一撃）
  🎯 コース勝率が高い当地巧者
  🚤 5-6コースから武器持ち（アウト一閃）
  🌱 若手の上がり調子
本命が弱い(API<45%)レースは『1号艇が飛ぶ匂い』としてロマン度を底上げ。

出力:
  A) 直近日のロマン出目サンプル（理由つき・実結果と配当）
  B) ロマン砲の“夢”統計（的中率・当たった時の平均/最大配当・万舟捕獲数）
"""
import argparse
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
        rc = races.setdefault(rid, {"d": r["日付"], "v": r["会場"], "no": r.get("レース"),
                                    "fin": {}, "api": {}, "nm": {}, "mr": {}, "sr": {},
                                    "cl": {}, "wn": {}, "lw": {}, "age": {}})
        rc["fin"][w] = fin
        rc["api"][w] = api_map.get((rid, w))
        rc["nm"][w] = r.get("選手名", "")
        rc["mr"][w] = B.to_float(r.get("motor_rank_in_race"))
        rc["sr"][w] = B.to_float(r.get("st_rank_in_race"))
        rc["cl"][w] = B.to_float(r.get("class_ord"))
        rc["wn"][w] = B.to_float(r.get("win_rate_national"))
        rc["lw"][w] = B.to_float(h.get("lane_win_rate"))
        rc["age"][w] = B.to_float(r.get("age"))
    payout = B.load_payouts(sorted({r["日付"] for r in rel if r["日付"] >= since}))
    return races, payout


def weapons(rc, w, hon):
    """艇wの武器タグlistとロマン度scoreを返す。"""
    tags, sc = [], 0
    mr, sr, cl = rc["mr"].get(w), rc["sr"].get(w), rc["cl"].get(w)
    lw, age = rc["lw"].get(w), rc["age"].get(w)
    if mr == 1:
        tags.append("🔧機力レース1位"); sc += 3
    elif mr == 2:
        tags.append("🔧機力レース2位"); sc += 1
    if sr == 1:
        tags.append("⚡ST評価レース1位"); sc += 2
    elif sr == 2:
        sc += 1
    if cl == 4 and w >= 4:
        tags.append("🅰A1級が"+str(w)+"コース"); sc += 2
    if lw is not None and lw >= 0.40:
        tags.append(f"🎯コース勝率{int(lw*100)}%"); sc += 2
    if w >= 5 and (mr in (1, 2) or sr in (1, 2)):
        tags.append("🚤アウトから武器持ち"); sc += 1
    if age is not None and age <= 25 and (rc["wn"].get(w) or 0) >= 5.5:
        tags.append("🌱若手上がり調子"); sc += 1
    if hon < 0.45:
        sc += 1   # 本命が弱い＝荒れる匂いでロマン度底上げ
    return tags, sc


def iter_races(races):
    for rid, rc in races.items():
        if len(rc["fin"]) != 6:
            continue
        s = [rc["api"].get(w) for w in range(1, 7)]
        if any(x is None for x in s):
            continue
        fins = [rc["fin"][w] for w in range(1, 7)]
        if any(f is None for f in fins):
            continue
        order = sorted([w for w in range(1, 7) if fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < 3 or fins[order[0] - 1] != 1:
            continue
        ranks = sorted(range(1, 7), key=lambda w: s[w - 1], reverse=True)
        yield rid, rc, s, order, ranks


def pick_roman(rc, s, ranks):
    """ロマン艇と夢出目を選ぶ。軽視(API3番手以下)で武器最大の艇をアタマに。
    返り値 (roman_w, tags, combos[(t1,t2,t3)], partners)。該当なしはNone。"""
    fav = ranks[0]
    hon = s[fav - 1]
    cand = []
    for w in ranks[2:]:          # API3番手以下＝軽視
        tags, sc = weapons(rc, w, hon)
        if tags:
            cand.append((sc, -s[w - 1], w, tags))   # スコア高→より軽視(低api)を優先
    if not cand:
        return None
    cand.sort(reverse=True)
    _, _, roman, tags = cand[0]
    # 相方=本命 + 残りで武器/実力上位1艇。夢の本線はロマン→本命→相方。
    rest = [w for w in ranks if w != roman]
    partner2 = fav
    others = [w for w in rest if w != fav]
    # 残りの中で武器スコアが高い1艇（無ければAPI上位）
    others.sort(key=lambda w: (weapons(rc, w, hon)[1], s[w - 1]), reverse=True)
    partner3 = others[0] if others else rest[0]
    combos = [(roman, partner2, partner3), (roman, partner3, partner2)]
    return roman, tags, combos, (partner2, partner3)


def section_sample(races, payout, day, limit):
    print(f"\n========== A) {day} のロマン出目サンプル（理由つき）==========")
    rows = [(rid, rc, s, order, ranks) for rid, rc, s, order, ranks in iter_races(races)
            if rc["d"] == day]
    rows.sort(key=lambda x: (x[1]["v"], str(x[1]["no"])))
    shown = 0
    for rid, rc, s, order, ranks in rows:
        pk = pick_roman(rc, s, ranks)
        if not pk:
            continue
        roman, tags, combos, (p2, p3) = pk
        nm = rc["nm"].get(roman, "")
        po3 = payout.get(rid, (0, 0))[1]
        hit = tuple(order[:3]) in set(combos)
        res = (f"★的中 ¥{po3:,}" if hit else f"×（実際は{'-'.join(map(str,order[:3]))}）")
        mark = "🎯💥" if (hit and po3 >= 10000) else ("🎯" if hit else "  ")
        print(f"\n{mark} {rc['v']} {rc['no']}R  夢の本線: {roman}-{p2}-{p3}"
              f"（裏 {roman}-{p3}-{p2}）2点")
        print(f"   アタマ={roman}号 {nm}（API{int(s[roman-1]*100)}%＝{ranks.index(roman)+1}番人気の軽視艇）")
        print(f"   理由: {' / '.join(tags)}")
        print(f"   相方: {p2}号(本命) ・ {p3}号  →  結果 {res}")
        shown += 1
        if shown >= limit:
            break
    if shown == 0:
        print("（この日は該当レースなし）")


def section_stats(races, payout):
    print("\n========== B) ロマン砲の“夢”統計（全期間・2点固定）==========")
    n = nhit = 0
    pays = []
    man = 0       # 万舟(≥1万)捕獲
    big = []      # 高配当の実例
    for rid, rc, s, order, ranks in iter_races(races):
        pk = pick_roman(rc, s, ranks)
        if not pk:
            continue
        roman, tags, combos, _ = pk
        n += 1
        if tuple(order[:3]) in set(combos):
            nhit += 1
            po = payout.get(rid, (0, 0))[1]
            pays.append(po)
            if po >= 10000:
                man += 1
                big.append((po, rc["d"], rc["v"], rc["no"], roman))
    hit = nhit / n * 100 if n else 0
    avg = sum(pays) / len(pays) if pays else 0
    mx = max(pays) if pays else 0
    print(f"\n対象レース {n}  / 的中 {nhit}（{hit:.1f}%）")
    print(f"当たった時の平均配当 ¥{avg:,.0f} ／ 最高 ¥{mx:,}")
    print(f"万舟(≥¥10,000)を当てた回数 {man}（的中の{man/nhit*100 if nhit else 0:.0f}%）")
    # 2点=¥200/レースの“遊び”として：参考の通算
    inv = n * 200
    ret = sum(pays)
    print(f"\n参考（あくまで遊び）：1レース2点¥200で全{n}レース買った場合 "
          f"投資¥{inv:,}→払戻¥{ret:,}（回収{ret/inv*100 if inv else 0:.0f}%）")
    big.sort(reverse=True)
    print("\n── 夢が当たった高配当TOP10 ──")
    for po, d, v, no, rm in big[:10]:
        print(f"  ¥{po:>7,}  {d} {v}{no}R  アタマ{rm}号")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--hist", default="features_player_history.csv")
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--day", default="2026-06-27")
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args()

    print(f"読込 since={args.since} …")
    races, payout = load_all(args.rel, args.pred, args.hist, args.since)
    section_sample(races, payout, args.day, args.limit)
    section_stats(races, payout)
    print("\n※回収率は目的ではない。『理由のある軽視艇アタマ＝当たれば万舟』の夢出目。")


if __name__ == "__main__":
    main()
