# -*- coding: utf-8 -*-
"""当日EVピック v2 — 較正済み確率 × 最新スナップショットオッズ。

v1 との違い:
  - 確率は較正曲線（calibration.json）適用後の値
  - 各買い目に「オッズの鮮度（取得からの経過分）」を表示。古いオッズのEVを
    信用しないための情報（T2 の運用面）。--max-age で古いものを除外可能
  - 予測には predict_win.csv（v1 daily が当日行だけ最新モデルに差し替えたもの）を使う。
    ※ 意思決定への利用は当日行のみなので T1（評価のin-sample混入）には当たらない

使い方:
  python ev_picks.py                        # 今日・EV>=1.5・鉄板(較正後hon>=0.60)のみ
  python ev_picks.py --ev-min 1.2 --hon-min 0 --max-age 15
"""
import argparse
import csv
import datetime
from collections import defaultdict

try:
    from . import config, odds, calibration, pl
except ImportError:
    import config
    import odds
    import calibration
    import pl


def load_today_pred(hd, path=None):
    """predict_win.csv から当日レースの {rid: [枠順 p_win×6]}。"""
    path = path or config.PRED_TODAY
    races = defaultdict(dict)
    with open(path, encoding="cp932") as f:
        for r in csv.DictReader(f):
            rid = r["race_id"]
            if rid[2:10] != hd:
                continue
            try:
                races[rid][int(r["枠番"])] = float(r["p_win"])
            except (ValueError, KeyError):
                continue
    return {rid: [b[w] for w in range(1, 7)]
            for rid, b in races.items() if len(b) == 6}


def age_min(fetched_at, now):
    try:
        t = datetime.datetime.strptime(fetched_at, "%Y-%m-%d %H:%M:%S")
        return (now - t).total_seconds() / 60
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description="当日のEVピック（較正確率×実オッズ）")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--ev-min", type=float, default=1.5)
    ap.add_argument("--hon-min", type=float, default=0.60,
                    help="較正後の本命確率下限（0で無効）")
    ap.add_argument("--max-age", type=float, default=None,
                    help="オッズ取得からの経過分の上限（超過は除外）")
    args = ap.parse_args()
    hd = args.date.replace("-", "")
    now = datetime.datetime.now()

    preds = load_today_pred(hd)
    snaps, legacy = odds.load_snapshots(hd)
    curve = calibration.load_curve()
    if not preds:
        print("当日の予測がありません（v1 daily.py を先に実行）")
        return
    if not snaps:
        print("当日のオッズスナップショットがありません（scheduler.py / odds.py で取得）")
        return
    if not curve:
        print("⚠ 較正曲線なし（calibration.py 未実行）→ 生の p_win を使用")
    if legacy:
        print("⚠ v1形式オッズ（取得履歴なし）を使用")

    n_pick = 0
    for rid in sorted(preds):
        strengths = calibration.calibrate_race(preds[rid], curve)
        hon = max(strengths)
        if hon < args.hon_min:
            continue
        cands = [("2t", c, p) for c, p in pl.pl_top(strengths, 2, config.TOP_2T)]
        cands += [("3t", c, p) for c, p in pl.pl_top(strengths, 3, config.TOP_3T)]
        rows = []
        for bt, combo, p in cands:
            cs = "-".join(map(str, combo))
            hist = snaps.get((rid, bt, cs))
            if not hist:
                continue
            fa, o = hist[-1]
            ev = p * o
            if ev < args.ev_min:
                continue
            age = age_min(fa, now)
            if args.max_age is not None and age is not None and age > args.max_age:
                continue
            rows.append((ev, bt, cs, p, o, age))
        if rows:
            rows.sort(reverse=True)
            print(f"\n{rid[:2]}場 {int(rid[10:]):2d}R（較正後本命 {hon*100:.0f}%）")
            for ev, bt, cs, p, o, age in rows:
                a = f"{age:.0f}分前" if age is not None else "取得時刻不明"
                stale = " ⚠古い" if (age or 0) > 15 else ""
                print(f"  {'2連単' if bt=='2t' else '3連単'} {cs:7s} "
                      f"p={p:.3f} × {o:5.1f}倍 = EV {ev:.2f}（オッズ{a}{stale}）")
            n_pick += len(rows)
    if n_pick == 0:
        print(f"\nEV>={args.ev_min}・本命>={args.hon_min} に合致する買い目なし"
              f"（無理に張らないのが正解）")
    else:
        print(f"\n計 {n_pick} 点。※検証では優位性のCIは100%を跨いでいます。"
              f"少点・高分散を前提に。")


if __name__ == "__main__":
    main()
