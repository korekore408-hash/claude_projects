# -*- coding: utf-8 -*-
"""相手(非本命5艇)の p_win ばらつき が 2連単/3連単 の的中にどれだけ効くかを再現検証。

問い: 「本命(モデル1番手)以外に差がない場合 vs 差がある場合で、
        2連単/3連単の予想(本命)の当たり方はどれだけ違うか」

方法:
  - レースごとに 非本命5艇の p_win の母集団標準偏差 = rival_spread を計算。
  - 生比較は交絡する(平坦なレースほど本命が強い)ので、本命確率(top1 p_win)帯で層別し、
    各帯の中で rival_spread の中央値で「平坦 / 差あり」に二分して比較する。
  - 指標: 2連単本命的中(実1-2着==モデル1-2番手) / 3連単本命的中(実1-2-3着==モデル1-2-3番手)
          / P(2着|1着) / P(3着|2着)。

入力: predict_win.csv (race_id,枠番,登番,p_win,strength,finish_rank), cp932。
既定は honest OOS = 学習に使っていない期間のみ(--oos-from で指定, 既定 20260501)。
出力: 標準出力(実行は PYTHONIOENCODING=utf-8 推奨)。
"""
import csv
import argparse
import statistics
from collections import defaultdict

ENC = "cp932"


def fnum(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def load_races(path, oos_from):
    """rid -> [(lane, p_win, finish_rank)]。oos_from 以降の日付のみ。"""
    races = defaultdict(list)
    with open(path, encoding=ENC, newline="") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if not row:
                continue
            rid = row[0]
            if oos_from and rid[2:10] < oos_from:
                continue
            races[rid].append((row[1], fnum(row[3]), fnum(row[5])))
    return races


def race_metrics(boats):
    """1レース分 → 指標 dict。完走1-2-3着が揃わない/勝者なしは None。"""
    if len(boats) < 6:
        return None
    order = sorted(boats, key=lambda b: -(b[1] if b[1] is not None else -1))
    m1, m2, m3 = order[0][0], order[1][0], order[2][0]          # モデル1/2/3番手の枠
    hon = order[0][1] or 0.0
    rivals = [b[1] or 0.0 for b in order[1:]]                   # 非本命5艇の p_win
    spread = statistics.pstdev(rivals)
    a = {b[2]: b[0] for b in boats if b[2] in (1, 2, 3)}        # 着順 -> 枠
    if 1 not in a or 2 not in a or 3 not in a:
        return None
    a1, a2, a3 = a[1], a[2], a[3]
    return {
        "hon": hon, "spread": spread,
        "ex_hit": int(a1 == m1 and a2 == m2),                  # 2連単 本命的中
        "tri_hit": int(a1 == m1 and a2 == m2 and a3 == m3),    # 3連単 本命的中
        "win1": int(a1 == m1),                                 # 本命が1着
        "p2_given1": int(a1 == m1 and a2 == m2),               # 1着的中かつ2着も的中
        "p3_given2": int(a1 == m1 and a2 == m2 and a3 == m3),  # 1-2着的中かつ3着も
        "d12_den": int(a1 == m1),                              # P(2|1)の分母
        "d23_den": int(a1 == m1 and a2 == m2),                 # P(3|2)の分母
    }


HON_BANDS = [(0.0, 0.40, "<.40"), (0.40, 0.50, ".40-.50"),
             (0.50, 0.65, ".50-.65"), (0.65, 0.80, ".65-.80"),
             (0.80, 1.01, ">=.80")]


def band_of(hon):
    for lo, hi, name in HON_BANDS:
        if lo <= hon < hi:
            return name
    return ">=.80"


def agg(rows):
    n = len(rows)
    if n == 0:
        return None
    d12 = sum(r["d12_den"] for r in rows)
    d23 = sum(r["d23_den"] for r in rows)
    return {
        "n": n,
        "hon": sum(r["hon"] for r in rows) / n,
        "spread": sum(r["spread"] for r in rows) / n,
        "ex": sum(r["ex_hit"] for r in rows) / n,
        "tri": sum(r["tri_hit"] for r in rows) / n,
        "win1": sum(r["win1"] for r in rows) / n,
        "p2g1": (sum(r["p2_given1"] for r in rows) / d12) if d12 else 0.0,
        "p3g2": (sum(r["p3_given2"] for r in rows) / d23) if d23 else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="predict_win.csv")
    ap.add_argument("--oos-from", default="20260501",
                    help="この日付(YYYYMMDD)以降のみ。全期間は 0 を指定。")
    args = ap.parse_args()
    oos = "" if args.oos_from in ("0", "") else args.oos_from

    races = load_races(args.csv, oos)
    rows = [m for b in races.values() if (m := race_metrics(b))]
    rows.sort(key=lambda r: r["hon"])

    span = "全期間" if not oos else f"honest OOS(>= {oos})"
    print("=" * 70)
    print(f"相手(非本命5艇)の差 × 2連単/3連単  ［{span}］  対象 {len(rows)} レース")
    sp_all = sorted(r["spread"] for r in rows)
    t1, t2 = sp_all[len(sp_all)//3], sp_all[2*len(sp_all)//3]
    md = sp_all[len(sp_all)//2]
    print(f"rival_spread 分布: 中央値={md:.4f}  3分位しきい(小|中|大境界)={t1:.4f} / {t2:.4f}")
    print()

    # 全体(交絡あり・参考): rival_spread 中央値で二分
    print("[参考] 全体を rival_spread 中央値で二分 (交絡=平坦ほど本命が強い)")
    flat = [r for r in rows if r["spread"] <= md]
    diff = [r for r in rows if r["spread"] > md]
    _print_pair(agg(flat), agg(diff))
    print()

    # 本命確率帯で層別 → 各帯内の中央値で平坦/差ありに二分
    print("[本命確率帯で層別]  各帯内の rival_spread 中央値で 平坦/差あり に二分")
    print("  帯        群     n    本命  spread  2連単  3連単  P(2|1)  P(3|2)")
    for lo, hi, name in HON_BANDS:
        band = [r for r in rows if lo <= r["hon"] < hi]
        if len(band) < 40:
            continue
        sp = sorted(r["spread"] for r in band)
        bmd = sp[len(sp)//2]
        fl = agg([r for r in band if r["spread"] <= bmd])
        df = agg([r for r in band if r["spread"] > bmd])
        for tag, a in (("平坦", fl), ("差あり", df)):
            if a:
                print(f"  {name:8s} {tag}  {a['n']:5d}  {a['hon']:.3f}  {a['spread']:.4f}  "
                      f"{a['ex']:.3f}  {a['tri']:.3f}  {a['p2g1']:.3f}   {a['p3g2']:.3f}")
        if fl and df:
            print(f"  {'':8s} 差→{'':2s}  {'':5s}  {'':5s}  {'':6s}  "
                  f"{df['ex']-fl['ex']:+.3f}  {df['tri']-fl['tri']:+.3f}  "
                  f"{df['p2g1']-fl['p2g1']:+.3f}   {df['p3g2']-fl['p3g2']:+.3f}")
    print()
    print("読み方: 同じ本命確率帯(=本命の強さを固定)でも、相手に差があるレース(差あり)の方が")
    print("        2連単/3連単とも的中が高い。効くのは主に P(2|1)・P(3|2)=相手(2-3着)を絞れるか。")


def _print_pair(flat, diff):
    print("  群     n     本命  spread  2連単  3連単  P(2|1)  P(3|2)")
    for tag, a in (("平坦", flat), ("差あり", diff)):
        print(f"  {tag}  {a['n']:5d}  {a['hon']:.3f}  {a['spread']:.4f}  "
              f"{a['ex']:.3f}  {a['tri']:.3f}  {a['p2g1']:.3f}   {a['p3g2']:.3f}")


if __name__ == "__main__":
    main()
