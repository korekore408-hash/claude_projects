# -*- coding: utf-8 -*-
"""
+EV バックテスト：オッズが分かる前提で「model_prob × odds > 閾値」の組だけ買う戦略が
回収率100%を超えられるかを検証する。

突合する3点（すべて race_id キー）:
  1. モデル予想   predict_win_history.csv … 各レース・各枠の1着予想率 p_win（朝＝先読みなし）
  2. 確定オッズ   data/odds/odds_*.csv     … 3連単120点・2連単30点（boatrace-odds.yml が毎日記録）
  3. 実結果       data/k*.csv              … 着順（的中判定）

組合せ確率はサイト本体と同じ Plackett-Luce（plProbOf 相当）で p_win から合成。
買い目の払戻は確定オッズ基準（的中なら odds×100 円、1点100円賭け）。

使い方:
  python backtest_ev.py                 # 全期間
  python backtest_ev.py --from 2026-10-01
  python backtest_ev.py --thresholds 1.0 1.1 1.2 1.5 2.0

※ オッズのログが貯まる前（モデル履歴と日付が重ならない間）は「突合0件」と表示される。
  boatrace-odds.yml が毎日前日分を記録するので、数週間後に重なり次第この検証が回る。
"""
import os
import csv
import glob
import argparse
import itertools
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
PWH = os.path.join(HERE, "predict_win_history.csv")
DATA = os.path.join(HERE, "data")
ODDS_DIR = os.path.join(DATA, "odds")

# 競艇場 名称→コード（K-fileは場名、race_id/予想CSVはコード）。標準・不変。
VENUE = {"桐生": 1, "戸田": 2, "江戸川": 3, "平和島": 4, "多摩川": 5, "浜名湖": 6,
         "蒲郡": 7, "常滑": 8, "津": 9, "三国": 10, "びわこ": 11, "住之江": 12,
         "尼崎": 13, "鳴門": 14, "丸亀": 15, "児島": 16, "宮島": 17, "徳山": 18,
         "下関": 19, "若松": 20, "芦屋": 21, "福岡": 22, "唐津": 23, "大村": 24}


def plprob(ps, combo):
    """Plackett-Luce：p_win列(ps[0..5]) から順序組 combo(1-based) の確率。site plProbOf と同一。"""
    p = 1.0
    rem = sum(ps)
    for w in combo:
        if rem <= 0:
            return 0.0
        p *= ps[w - 1] / rem
        rem -= ps[w - 1]
    return p


def load_model():
    """race_id -> [p_win×6]（枠1..6順）。"""
    pw = collections.defaultdict(lambda: [0.0] * 6)
    if not os.path.exists(PWH):
        return pw
    with open(PWH, encoding="cp932") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            try:
                pw[row[0]][int(row[1]) - 1] = float(row[2])
            except (ValueError, IndexError):
                pass
    return pw


def load_odds(date_from=None):
    """race_id -> {'3t': {(a,b,c): odds}, '2t': {(a,b): odds}}。"""
    odds = collections.defaultdict(lambda: {"3t": {}, "2t": {}})
    for of in glob.glob(os.path.join(ODDS_DIR, "odds_*.csv")):
        with open(of, encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) < 4:
                    continue
                rid, bt, combo, od = row[0], row[1], row[2], row[3]
                if date_from and _rid_date(rid) < date_from:
                    continue
                try:
                    od = float(od)
                except ValueError:
                    continue
                if od <= 0 or bt not in ("3t", "2t"):
                    continue
                odds[rid][bt][tuple(int(x) for x in combo.split("-"))] = od
    return odds


def load_results(date_from=None):
    """race_id -> 着順[上位3枠]。K-fileは cp932・場名表記。"""
    res = {}
    for kf in glob.glob(os.path.join(DATA, "k*.csv")):
        with open(kf, encoding="cp932") as f:
            r = csv.reader(f)
            next(r, None)
            rows = list(r)
        byrace = collections.defaultdict(list)
        for row in rows:
            if len(row) >= 28:
                byrace[(row[0], row[1], row[2])].append(row)
        for (ven, date, rno), rws in byrace.items():
            code = VENUE.get(ven)
            if code is None:
                continue
            y, m, d = date.split("/")
            rid = f"{code:02d}{y}{int(m):02d}{int(d):02d}{int(rno):02d}"
            if date_from and f"{y}-{int(m):02d}-{int(d):02d}" < date_from:
                continue
            order = []
            for row in rws:
                try:
                    order.append((int(row[3]), int(row[4])))
                except ValueError:
                    pass
            order.sort()
            if len(order) >= 3:
                res[rid] = [lane for _, lane in order][:3]
    return res


def _rid_date(rid):
    """race_id(VVYYYYMMDDRR) -> 'YYYY-MM-DD'。"""
    return f"{rid[2:6]}-{rid[6:8]}-{rid[8:10]}"


def run(bt, joined, pw, odds, results, thresholds):
    label = "3連単" if bt == "3t" else "2連単"
    ntop = 3 if bt == "3t" else 2

    # 参照：全組フラット買いの回収率（=1-控除率。オッズ健全性チェック）
    s = r = n = 0
    for rid in joined:
        od = odds[rid][bt]
        if not od:
            continue
        act = tuple(results[rid][:ntop])
        for c, o in od.items():
            s += 100
            if c == act:
                r += o * 100
                n += 1
    if s == 0:
        print(f"\n== {label} ==  対象なし")
        return
    print(f"\n== {label} ==  全組買い基準: 回収率 {r / s * 100:.1f}%  (的中率 {n / (s / 100) * 100:.2f}%)")
    print(" 閾値T    買い点数    対象R    的中率    実現回収率   平均購入オッズ")
    for T in thresholds:
        stake = ret = hits = nbet = 0
        sumod = 0.0
        betraces = set()
        for rid in joined:
            od = odds[rid][bt]
            if not od:
                continue
            ps = pw[rid]
            act = tuple(results[rid][:ntop])
            for c, o in od.items():
                if plprob(ps, c) * o >= T:
                    nbet += 1
                    stake += 100
                    sumod += o
                    betraces.add(rid)
                    if c == act:
                        ret += o * 100
                        hits += 1
        if nbet == 0:
            print(f" {T:5.2f}         0        –         –           –          –")
            continue
        print(f" {T:5.2f}   {nbet:9,}  {len(betraces):6,}   {hits / nbet * 100:6.2f}%   "
              f"{ret / stake * 100:8.1f}%   {sumod / nbet:8.1f}")


def main():
    ap = argparse.ArgumentParser(description="+EVフィルタ（model_prob×odds）バックテスト")
    ap.add_argument("--from", dest="date_from", default=None, help="開始日 YYYY-MM-DD（含む）")
    ap.add_argument("--thresholds", nargs="*", type=float,
                    default=[0.0001, 0.8, 1.0, 1.1, 1.2, 1.5, 2.0, 3.0],
                    help="model_prob×odds の閾値（1.0=理論損益分岐・+EV）")
    args = ap.parse_args()

    pw = load_model()
    odds = load_odds(args.date_from)
    results = load_results(args.date_from)
    joined = [rid for rid in odds if rid in pw and rid in results and sum(pw[rid]) > 0]

    print(f"モデル予想 {len(pw):,}R / オッズ {len(odds):,}R / 結果 {len(results):,}R")
    print(f"突合（3点そろい）: {len(joined):,}R" + (f"  （{args.date_from}以降）" if args.date_from else ""))
    if not joined:
        print("\n突合0件：オッズのログがモデル予想履歴と重なるのを待ってください。")
        print("（boatrace-odds.yml が毎日前日分を data/odds/ に記録します）")
        return

    run("3t", joined, pw, odds, results, args.thresholds)
    run("2t", joined, pw, odds, results, args.thresholds)

    print("\n注意：これは確定オッズ（＝払戻の基準）での試算。実戦では締切前に賭けるためオッズが動き、")
    print("自分の投票がオッズを押し下げる（特に人気薄）。T=1.0が理論損益分岐＝それ未満は買わない前提。")


if __name__ == "__main__":
    main()
