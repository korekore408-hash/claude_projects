# -*- coding: utf-8 -*-
"""
2連複を「買い目点数 N」ごとに検証する。

各レースで学習モデル p_win（train≤260430固定＝評価期間はhonest OOS）の
PL確率で 2連複15点を並べ、上位 N 点を各¥100で買う（フラット）。
非完走(F等)艇を含む買い目は返還＝投資から除外（アプリと同方針）。

N=1..15 について:
  - 的中率（買った組に実際の2連複が含まれたレースの割合）
  - 回収率（Σ払戻 / Σ投資）
  - 100%超えの日数（その日の Σ払戻/Σ投資 > 100% の日）
を出す。買い目を増やすと的中率は上がるが回収率(期待値)は動くのか？を見る。
"""
from collections import defaultdict
import build_today as B
import analyze_ana_taikou_roi as A


def main():
    print("データ読込中…")
    model = A.load_predict()                       # {rid:{w:p_win}}
    kd = A.load_all_ktxt()                          # 着順・status
    dates = sorted({f"{rid[2:6]}-{rid[6:8]}-{rid[8:10]}" for rid in kd})
    payout = B.load_payouts(dates)                 # {rid:(po2t,po3t,po2f)}

    # レース → (sv, actual_pair, fly, po2f)
    races = []
    for rid, rc in kd.items():
        mp = model.get(rid)
        if not mp or len(mp) != 6:
            continue
        if rid not in payout:
            continue
        po2f = payout[rid][2]
        if not po2f:                               # 2連複配当が無い日はスキップ
            continue
        sv = [mp[w] for w in range(1, 7)]
        fins = rc["fin"]
        order = sorted([w for w in range(1, 7) if fins.get(w)], key=lambda w: fins[w])
        if len(order) < 2 or fins[order[0]] != 1:
            continue
        act = tuple(sorted(order[:2]))
        fly = {w for w in range(1, 7) if rc["status"].get(w) != "finish"}
        races.append((rid, sv, act, fly, po2f))

    print(f"対象: {len(races):,}レース（2連複配当あり）\n")

    print(f"{'点数':>4}{'投資レース':>10}{'的中率':>9}{'回収率':>9}{'100%超えの日':>14}{'黒字日率':>9}")
    print("-" * 56)
    for N in range(1, 16):
        stake = ret = 0
        bought_races = 0        # 実際に賭けが1点でも成立したレース
        hit_races = 0
        day = defaultdict(lambda: [0, 0])   # date -> [stake, ret]
        for rid, sv, act, fly, po2f in races:
            buy = B._pf_topk(sv, N)
            s = r = 0
            hit = False
            for c in buy:
                if any(w in fly for w in c):      # 非完走含む＝返還（賭けない）
                    continue
                s += 100
                if c == act:
                    r += po2f
                    hit = True
            if s == 0:
                continue
            bought_races += 1
            stake += s
            ret += r
            if hit:
                hit_races += 1
            d = rid[2:10]
            day[d][0] += s
            day[d][1] += r
        roi = ret / stake * 100 if stake else 0
        hitrate = hit_races / bought_races * 100 if bought_races else 0
        ndays = len(day)
        over = sum(1 for st, rt in day.values() if st and rt > st)
        winday = sum(1 for st, rt in day.values() if st and rt >= st)
        print(f"{N:>4}{bought_races:>10,}{hitrate:>8.1f}%{roi:>8.1f}%"
              f"{over:>7}/{ndays}日{winday/ndays*100:>7.0f}%")
    print("\n※各点フラット¥100・非完走含む買い目は返還。的中率は買った組に実際の2連複が入った割合。")
    print("  買い目を増やすと的中率は上がるが、回収率(期待値)は控除率ぶんの負けに収束する。")


if __name__ == "__main__":
    main()
