# -*- coding: utf-8 -*-
"""
(1) アプリ買い目を「2連複だけ」にしたら利確ルールはどうなるか。
(2) 段々と購入金額を増やす（累進投資）と回収率は良くなるか。

買い目＝サイト本体と同一ポリシーの 2連複のみ（全帯・上位 k_ex(hon)・¥2,000を確率比例配分・F返還）。
順位付け＝学習モデル p_win、hon＝API本命確率。時系列＝締切時刻順（全会場横断）。

累進の定義（その日、締切順に k レース目の賭け金倍率 m_k。累積回収率>100%で打切）:
  flat      : m_k = 1（一定）
  linear    : m_k = k（1,2,3,… 倍）
  martingale: m_k = 1.6^(k-1)（負け続く＝まだ緑でない限り増やす）
基準額(¥2,000/レース)に倍率を掛ける。払戻は賭け金に比例するので ret も同倍率。
"""
from collections import defaultdict
import build_today as B
import analyze_ana_taikou_roi as A
from backtest_stopwin import load_deadlines

def nirenpuku_bet(sv, api, order, fly, po):
    """2連複のみの(stake, ret)。¥2,000確率比例配分・F返還。"""
    hon = max(api)
    kx = B.k_ex(hon)
    stake = ret = 0
    if len(order) >= 2:
        buy2 = B._pf_topk(sv, kx)
        if buy2:
            yen2 = B._alloc_yen(B._meri_w([B._pf_prob(sv, c) for c in buy2], hon))
            act2 = tuple(sorted(order[:2]))
            for c, y in zip(buy2, yen2):
                if not any(w in fly for w in c): stake += y
                if c == act2: ret += round(po[2] * y / 100)
    return stake, ret

def build_bets():
    model = A.load_predict()
    rel = B.load("features_race_relative.csv")
    api_map = B.build_api_scores(rel)
    kd = A.load_all_ktxt()
    dates = sorted({f"{rid[2:6]}-{rid[6:8]}-{rid[8:10]}" for rid in kd})
    payout = B.load_payouts(dates)
    dl = load_deadlines()
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
        s, r = nirenpuku_bet(sv, api, order, fly, payout[rid])
        if s > 0: bet[rid] = (s, r)
    byday = defaultdict(list)
    for rid in bet:
        t = dl.get(rid)
        if t is not None: byday[rid[2:10]].append((t, rid))
    for d in byday: byday[d].sort()
    return bet, byday

def sim(bet, byday, mult, stopwin=True):
    """mult(k)= k番目(0始まり)のレースの賭け金倍率。stopwin=累積>100%で打切。"""
    tot_s = tot_r = 0; days = 0; win = 0; worst = 0; stopped = 0
    for d, lst in byday.items():
        days += 1; cs = cr = 0; did_stop = False
        for k, (_, rid) in enumerate(lst):
            s0, r0 = bet[rid]; m = mult(k)
            cs += s0 * m; cr += r0 * m
            if stopwin and cr > cs:
                did_stop = True; break
        tot_s += cs; tot_r += cr
        if cr >= cs: win += 1
        if did_stop: stopped += 1
        worst = min(worst, cr - cs)
    return {"roi": tot_r/tot_s*100 if tot_s else 0, "stake": tot_s, "ret": tot_r,
            "days": days, "win": win, "worst": worst, "stopped": stopped}

def main():
    print("データ読込中…")
    bet, byday = build_bets()
    nR = sum(len(v) for v in byday.values())
    print(f"対象: {len(byday)}日 / {nR:,}レース（アプリ2連複のみ）\n")

    # (1) 2連複だけ：全買い vs 利確
    full = sim(bet, byday, lambda k: 1, stopwin=False)
    sw   = sim(bet, byday, lambda k: 1, stopwin=True)
    print("【(1) 2連複だけ】")
    print(f"  全買い : 回収率{full['roi']:.1f}%  投資¥{full['stake']:,}  収支¥{full['ret']-full['stake']:,}  黒字日{full['win']}/{full['days']}")
    print(f"  利確版 : 回収率{sw['roi']:.1f}%  投資¥{sw['stake']:,}  収支¥{sw['ret']-sw['stake']:,}  黒字日{sw['win']}/{sw['days']}（利確{sw['stopped']}日）")
    print()

    # (2) 累進投資（すべて利確つき）
    import math
    schemes = [
        ("flat（一定）",        lambda k: 1),
        ("linear（1,2,3…倍）",  lambda k: k + 1),
        ("martingale（1.6^k）", lambda k: 1.6 ** k),
    ]
    print("【(2) 段々と購入金額を増やす（累進・利確つき）】")
    print(f"  {'方式':<20}{'回収率':>8}{'投資総額':>16}{'収支':>16}{'黒字で終わる日':>14}{'最悪の1日':>16}")
    for name, mult in schemes:
        r = sim(bet, byday, mult, stopwin=True)
        print(f"  {name:<20}{r['roi']:>7.1f}%¥{r['stake']:>14,.0f}¥{r['ret']-r['stake']:>14,.0f}"
              f"{r['win']:>10}/{r['days']}¥{r['worst']:>14,.0f}")
    print("\n※flat/linear は回収率ほぼ同じ＝賭け金の増減(サイジング)では期待値は動かない。")

    # martingale が「155%・全勝」に見えるカラクリを暴く
    print("\n【martingale の正体】")
    # 必要な賭け金の最大値
    max_race = max_day = 0
    for d, lst in byday.items():
        cs = cr = 0; day_peak = 0
        for k, (_, rid) in enumerate(lst):
            s0, r0 = bet[rid]; m = 1.6 ** k
            rs = s0 * m; day_peak += rs; cs += rs; cr += r0 * m
            max_race = max(max_race, rs)
            if cr > cs: break
        max_day = max(max_day, day_peak)
    print(f"  必要な最大賭け金：1レース¥{max_race:,.0f} ／ 1日¥{max_day:,.0f}")
    print(f"  → 155%は『負けた日ほど賭け金を天文学的に増やし、最後に当たった日だけを数える』")
    print(f"    生存バイアス。無限の資金と青天井のベットが前提で、現実には不可能。")

    # 現実的な資金 ¥1,000,000 でマーチンゲールを回すと破産するか
    print("\n【現実：資金¥1,000,000 でマーチンゲール利確を回す】")
    START = 1_000_000
    bal = START; ruin_day = None; completed = 0; base_unit = 2000
    for i, (d, lst) in enumerate(sorted(byday.items()), 1):
        cs = cr = 0
        for k, (_, rid) in enumerate(lst):
            s0, r0 = bet[rid]
            scale = (1.6 ** k) * (base_unit / 2000)  # ¥2000基準の倍率
            want = s0 * scale
            if cs + want > bal:            # 残高を超える賭けはできない＝破産（緑にできず終了）
                ruin_day = i; break
            cs += want; cr += r0 * scale
            if cr > cs: break
        if ruin_day:
            bal -= cs  # その日の損失を反映（当たり無しで打切）
            break
        bal += (cr - cs); completed += 1
    if ruin_day:
        print(f"  {ruin_day}日目で資金が尽きて破産（残高¥{max(bal,0):,.0f}）。それまで{completed}日は黒字を積んでいた。")
    else:
        print(f"  220日完走・最終残高¥{bal:,.0f}（この標本ではたまたま破産日が来なかっただけ）。")
    print("  ＝マーチンゲールは『破産するまで勝ち続け、1度の連敗で全部失う』。期待値は83%のまま。")

if __name__ == "__main__":
    main()
