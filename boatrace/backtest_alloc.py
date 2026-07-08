# -*- coding: utf-8 -*-
"""買い目内の「金額配分」にメリハリを付けた場合の検証。

現行は予算¥2,000を確率比例(allocYen＝堅い目ほど厚い)で配る。これを
weight ∝ p^γ に一般化し、γを下げるほど「妙味＝配当の大きい薄い目」へ資金を
寄せる。予算とk点数は現行のまま、配分だけを変える(=総投資額は不変)。

  γ= 1.0  現行(確率比例・本命厚い)
  γ= 0.5  やや均し
  γ= 0.0  均等
  γ=-0.5  薄目寄せ(妙味重視)
  γ=-1.0  EVフラット(1/p・当たれば当該点はほぼ同額回収=最も荒い)

出力は帯別・全体で「回収率／的中率／総投資に対する分布」:
  max=単レース最大回収, ≥2.5x/≥5x=爆発回数(投資2000に対し),
  top5%=総回収のうち上位5レースが占める割合(集中度・荒さの指標)。

券種: 2連複(本番の本線・全帯 上位k_ex) と 3連単(triOn帯・triBuyList)。

  py -3 backtest_alloc.py
  py -3 backtest_alloc.py --since 2026-05-01     # OOS
"""
import argparse

import build_today as B
import backtest as BT
import backtest_fukushiki as F

GAMMAS = [1.0, 0.5, 0.0, -0.5, -1.0]
BANDS = [("鉄板", 0.65, 9.9), ("標準", 0.45, 0.65), ("穴 ", -1, 0.45)]
BUDGET = 2000


def alloc_pow(probs, gamma):
    """weight ∝ p^γ を¥100単位に丸めて配分(各点最低¥100・合計=BUDGET)。
    γ=1 は現行 _alloc_yen と同一。"""
    w = [max(p, 1e-12) ** gamma for p in probs]
    return B._alloc_yen(w, BUDGET)      # _alloc_yen は渡した重みに比例配分するだけ


def _combo_kind_ana(combo, rank):
    return max(rank[w] for w in combo) >= 5


def collect(races, hon_api, payout, system):
    """各レースの 2連複/3連単 の (買い目prob配列, 的中index, 配当) を帯付きで。"""
    rows = []
    for rid, rc in races.items():
        if len(rc["fin"]) != 6:
            continue
        s = [rc[system].get(w) for w in range(1, 7)]
        if any(x is None for x in s):
            continue
        hon = hon_api.get(rid)
        if hon is None:
            continue
        po = payout.get(rid)               # (3連複, 2連複, 3連複組, 2連複組)
        fins = [rc["fin"][w] for w in range(1, 7)]
        order = sorted([w for w in range(1, 7)
                        if fins[w - 1] is not None and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < 3 or fins[order[0] - 1] != 1:
            continue
        rank = BT.lane_ranks(s)
        # --- 2連複 ---
        kx = B.k_ex(hon)
        pf = B._pf_topk(s, kx)                              # [(a,b)...]
        pf_prob = [B._pf_prob(s, p) for p in pf]
        act2 = tuple(sorted(order[:2]))
        i2 = next((i for i, p in enumerate(pf) if tuple(sorted(p)) == act2), None)
        p2f = po[1] if po and po[1] else None
        # --- 3連単 ---
        tri = None
        if hon >= 0.45:                                    # triOn
            allc = BT.pl_order(s, 3, set())
            kt = B.k_tri(hon)
            if 0.45 <= hon < 0.65:
                allc = [c for c in allc if not _combo_kind_ana(c[0], rank)]
            buy = allc[:kt]
            tri_prob = [p for _, p in buy]
            act3 = tuple(order[:3])
            i3 = next((i for i, (c, _) in enumerate(buy) if c == act3), None)
            # 3連単配当は build_today.load_payouts 側。ここでは別ロードするため後段で結合
            tri = {"prob": tri_prob, "hit": i3, "act3": act3}
        rows.append({"d": rc["d"], "hon": hon, "rid": rid,
                     "pf_prob": pf_prob, "i2": i2, "p2f": p2f, "tri": tri})
    return rows


def attach_tri_payout(rows, payout3):
    for r in rows:
        if r["tri"] is not None:
            r["tri"]["pay"] = payout3.get(r["rid"])


def simulate(rows, gamma, which, band=None):
    """which='2f' or '3t'。回収率・的中率・分布指標を返す。"""
    stake_tot = ret_tot = hit = n = 0
    rets = []                                   # 各レースの回収額(投資は常にBUDGET)
    for r in rows:
        if band and not (band[1] <= r["hon"] < band[2]):
            continue
        if which == "2f":
            probs, hi, pay = r["pf_prob"], r["i2"], r["p2f"]
        else:
            t = r["tri"]
            if t is None:
                continue
            probs, hi, pay = t["prob"], t["hit"], t.get("pay")
        if not probs:
            continue
        n += 1
        stake_tot += BUDGET
        yen = alloc_pow(probs, gamma)
        got = 0
        if hi is not None and hi < len(yen) and pay:
            got = pay * yen[hi] / 100
            hit += 1
        ret_tot += got
        rets.append(got)
    if not n:
        return None
    rets.sort(reverse=True)
    big25 = sum(1 for x in rets if x >= BUDGET * 2.5)
    big5 = sum(1 for x in rets if x >= BUDGET * 5)
    top5 = sum(rets[:5]) / ret_tot * 100 if ret_tot else 0
    return {"rec": ret_tot / stake_tot * 100, "hit": hit / n * 100, "n": n,
            "max": rets[0] if rets else 0, "big25": big25, "big5": big5,
            "top5": top5}


def show(rows, which, title):
    print(f"\n{'='*74}\n{title}\n{'='*74}")
    hdr = "  γ     回収%  的中%   最大回収  ≥2.5x  ≥5x   top5集中%"
    for band in [None] + BANDS:
        blab = "全体" if band is None else band[0]
        # n を先頭γで取得
        first = simulate(rows, GAMMAS[0], which, band)
        if first is None or first["n"] == 0:
            continue
        print(f"\n[{blab}] n={first['n']}")
        print(hdr)
        for g in GAMMAS:
            m = simulate(rows, g, which, band)
            if m is None:
                continue
            tag = " ←現行" if g == 1.0 else (" EVフラット" if g == -1.0 else "")
            print(f"  {g:+.1f}  {m['rec']:6.1f} {m['hit']:5.1f}   "
                  f"{m['max']:8.0f}  {m['big25']:4d}  {m['big5']:3d}   "
                  f"{m['top5']:5.1f}{tag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    args = ap.parse_args()

    print(f"データ読込 since={args.since} …")
    races, hon_api, _, payout3 = BT.load_races(
        "features_race_relative.csv", "predict_win.csv",
        "features_player_history.csv", args.since)
    keep = sorted({rc["d"] for rc in races.values()})
    payout_f = F.load_payouts_fuku(keep)
    # payout3: build_today.load_payouts -> race_id -> (2連単, 3連単, 2連複)
    p3 = {rid: v[1] for rid, v in payout3.items()}

    for system in ("model", "api"):
        rows = collect(races, hon_api, payout_f, system)
        attach_tri_payout(rows, p3)
        sname = "学習モデル(本番)" if system == "model" else "API合成"
        show(rows, "2f", f"順位系統={sname} ── 2連複(本線・上位k_ex)")
        show(rows, "3t", f"順位系統={sname} ── 3連単(triOn帯・triBuyList)")

    print("\n※投資は毎レース¥2,000固定・配分だけ変更。max/≥2.5x/≥5x は単レース回収額。")
    print("※top5集中%=総回収のうち上位5レースの占有率(高いほど荒く爆発依存)。F返還未考慮。")


if __name__ == "__main__":
    main()
