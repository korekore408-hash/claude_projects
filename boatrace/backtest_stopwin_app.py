# -*- coding: utf-8 -*-
"""
「アプリの予想どおり買って、その日の累積回収率>100%でやめる」利確ルールの検証。

買い目＝サイト本体と同一ポリシー（build_today.recent_stats と一致）:
  - 2連複: 全帯・確率上位 k_ex(hon)・¥2,000を確率比例配分(_alloc_yen∘_meri_w)
  - 3連単: 本命確率 hon≥0.45 のみ・上位 k_tri(hon)（標準帯0.45-0.65は穴型除外）・¥2,000配分
  - hon＝API簡易合成の本命確率 / 順位付けは学習モデル p_win
  - 非完走(F等)艇を含む買い目は返還＝投資から除外
時系列＝B-fileの締切時刻順（全会場横断）。
比較: (A)全レース買い vs (B)累積回収率>100%で当日打ち切り。
"""
from collections import defaultdict
import build_today as B
import analyze_ana_taikou_roi as A
from backtest_stopwin import load_deadlines

def app_bet(sv, api, order, fly, po):
    """1レースのアプリ買い目 →(stake, ret)。po=(po2,po3,pof)。"""
    hon = max(api)
    kx, kt = B.k_ex(hon), B.k_tri(hon)
    stake = ret = 0
    # 2連複（全帯）
    if len(order) >= 2:
        buy2 = B._pf_topk(sv, kx)
        if buy2:
            yen2 = B._alloc_yen(B._meri_w([B._pf_prob(sv, c) for c in buy2], hon))
            act2 = tuple(sorted(order[:2]))
            for c, y in zip(buy2, yen2):
                if not any(w in fly for w in c): stake += y
                if c == act2: ret += round(po[2] * y / 100)
    # 3連単（hon≥0.45）
    if len(order) >= 3 and hon >= 0.45:
        buy3 = B._tri_buy_list(B._pl_topk(sv, 3, 200), kt, hon, B._lane_rank_map(sv))
        if buy3:
            yen3 = B._alloc_yen(B._meri_w([B._pl_prob(sv, c) for c in buy3], hon))
            act3 = tuple(order[:3])
            for c, y in zip(buy3, yen3):
                if not any(w in fly for w in c): stake += y
                if c == act3: ret += round(po[1] * y / 100)
    return stake, ret

def main():
    print("データ読込中…")
    model = A.load_predict()                       # {rid:{w:p_win}}
    rel = B.load("features_race_relative.csv")
    api_map = B.build_api_scores(rel)              # {(rid,w):p}
    kd = A.load_all_ktxt()                          # 着順・status
    dates = sorted({f"{rid[2:6]}-{rid[6:8]}-{rid[8:10]}" for rid in kd})
    payout = B.load_payouts(dates)                 # {rid:(po2,po3,pof)}
    dl = load_deadlines()
    print(f"model {len(model)} / api {len(api_map)//6} / k {len(kd)} / payout {len(payout)} / 締切 {len(dl)}")

    # レース→(stake,ret)
    bet = {}
    for rid, rc in kd.items():
        mp = model.get(rid)
        if not mp or len(mp) != 6: continue
        api = [api_map.get((rid, w)) for w in range(1, 7)]
        if any(a is None for a in api): continue
        if rid not in payout: continue
        sv = [mp[w] for w in range(1, 7)]
        fins = rc["fin"]
        order = sorted([w for w in range(1, 7) if fins.get(w)], key=lambda w: fins[w])
        if not order or fins[order[0]] != 1: continue
        fly = {w for w in range(1, 7) if rc["status"].get(w) != "finish"}
        s, r = app_bet(sv, api, order, fly, payout[rid])
        if s > 0:
            bet[rid] = (s, r)

    # 日別・締切順にシミュレート
    byday = defaultdict(list)
    for rid, (s, r) in bet.items():
        t = dl.get(rid)
        if t is not None:
            byday[rid[2:10]].append((t, rid))

    full_s = full_r = sw_s = sw_r = 0
    days = days_stopped = 0
    win_full = win_sw = 0
    for d, lst in byday.items():
        lst.sort(); days += 1
        fs = fr = 0
        for _, rid in lst:
            s, r = bet[rid]; fs += s; fr += r
        full_s += fs; full_r += fr
        if fr >= fs: win_full += 1
        cs = cr = 0; stopped = False
        for _, rid in lst:
            s, r = bet[rid]; cs += s; cr += r
            if cr > cs: stopped = True; break
        sw_s += cs; sw_r += cr
        if stopped: days_stopped += 1
        if cr >= cs: win_sw += 1

    print(f"\n対象: {days}日 / {len(bet):,}レース（アプリ買い目・締切順）\n")
    print("                    全レース買い        利確版(>100%で打切)")
    print(f"  回収率            {full_r/full_s*100:>10.1f}%        {sw_r/sw_s*100:>10.1f}%")
    print(f"  投資総額          ¥{full_s:>12,}        ¥{sw_s:>12,}")
    print(f"  回収総額          ¥{full_r:>12,}        ¥{sw_r:>12,}")
    print(f"  収支              ¥{full_r-full_s:>12,}        ¥{sw_r-sw_s:>12,}")
    print(f"  黒字で終わる日     {win_full:>3}/{days}日           {win_sw:>3}/{days}日")
    print(f"  （利確で終えた日：{days_stopped}/{days}日）")
    print("\n※アプリ予想でも同じ：利確ルールは回収率(期待値)を上げず、賭け総額を減らすだけ。")

if __name__ == "__main__":
    main()
