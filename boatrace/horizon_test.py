# -*- coding: utf-8 -*-
"""
「回収率を上げる買い方は、1日でなく1週間・1ヶ月の長期スパンで考えると変化するのか？」の実証テスト。

データ: モデル予想 p_win (predict_win_history.csv) × 実結果+配当 (K-file data/k*.csv)
        ※モデル履歴と結果が同じ race_id で揃う期間だけを自動で突合する（オッズ不要）。

戦略ファミリ（「買い方」）:
  3連単: モデル確率上位 N 点買い  N ∈ {1,2,3,5,10,20}  ＋ ランク k 単点買い k ∈ {1,3,5,10,20,30}
  2連複: 上位 N 点買い            N ∈ {1,2,3}
  （組合せ確率はサイト本体と同じ Plackett-Luce＝plProbOf 相当で p_win から合成）

テストA: 同一戦略の実現回収率が 日/週/月 でどうブレるか（平均不変・分散縮小の確認）
テストB: 「その窓で最良だった買い方」は窓を変えると変わるか（＝日次の勝ちはノイズか）
テストC: 前半で選んだ最良戦略を後半に当てる（学習→検証の汎化）

結論（初回実測 2026-09-19〜10-17 / 4,427R）:
  期待回収率はスパンに依らず一定（≒控除率の壁75%前後）。窓を延ばしても新しい儲かる
  買い方は現れず、縮むのは実現ROIの分散だけ。短期の「勝ちパターン」を追うと固定戦略より
  むしろ悪化し（前日ベスト翌日採用 71.4% < 上位1点固定 73.5%）、学習窓で最良に見えた
  穴戦略は検証窓で崩壊（148.6%→58.3%）。＝長期スパンの効用は「幻を消す」こと。

使い方:
  python horizon_test.py
  python horizon_test.py --from 2026-09-19      # 突合期間を絞る
  python horizon_test.py --week 7               # 週窓の日数（既定7）
"""
import os
import csv
import glob
import argparse
import itertools
import collections
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
PWH = os.path.join(HERE, "predict_win_history.csv")
DATA = os.path.join(HERE, "data")

# 競艇場 名称→コード（K-fileは場名、race_id/予想CSVはコード）。標準・不変。
VENUE = {"桐生": 1, "戸田": 2, "江戸川": 3, "平和島": 4, "多摩川": 5, "浜名湖": 6,
         "蒲郡": 7, "常滑": 8, "津": 9, "三国": 10, "びわこ": 11, "住之江": 12,
         "尼崎": 13, "鳴門": 14, "丸亀": 15, "児島": 16, "宮島": 17, "徳山": 18,
         "下関": 19, "若松": 20, "芦屋": 21, "福岡": 22, "唐津": 23, "大村": 24}

TRIPLES = list(itertools.permutations(range(1, 7), 3))   # 120
PAIRS = list(itertools.combinations(range(1, 7), 2))     # 15


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


def load_results(date_from=None):
    """race_id -> (date 'YYYY-MM-DD', order[上位3枠], pay_tri, pay_fuku)。K-fileは cp932・場名表記。"""
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
            iso = f"{y}-{int(m):02d}-{int(d):02d}"
            if date_from and iso < date_from:
                continue
            rid = f"{code:02d}{y}{int(m):02d}{int(d):02d}{int(rno):02d}"
            order = []
            for row in rws:
                try:
                    order.append((int(row[3]), int(row[4])))
                except ValueError:
                    pass
            order.sort()
            lanes = [lane for _, lane in order]
            try:
                pay_tri = int(rws[0][21]) if rws[0][21].strip() else 0
            except (ValueError, IndexError):
                pay_tri = 0
            try:
                pay_fuku = int(rws[0][27]) if rws[0][27].strip() else 0
            except (ValueError, IndexError):
                pay_fuku = 0
            if len(lanes) >= 3:
                res[rid] = (iso, lanes[:3], pay_tri, pay_fuku)
    return res


# 戦略ファミリ（「買い方」）
STRATS = ([("triTopN", n) for n in (1, 2, 3, 5, 10, 20)]
          + [("triRank", k) for k in (1, 3, 5, 10, 20, 30)]
          + [("fukuTopN", n) for n in (1, 2, 3)])
TRI_KEYS = [k for k in STRATS if k[0] in ("triTopN", "triRank")]


def label(key):
    kind, p = key
    return {"triTopN": f"3単 上位{p}点", "triRank": f"3単 第{p}位のみ",
            "fukuTopN": f"2複 上位{p}点"}[kind]


def build_per_race(joined, pw, results):
    """rid -> {戦略キー: (stake, return)} を事前計算。"""
    per_race = {}
    for rid in joined:
        ps = pw[rid]
        _, order, pay_tri, pay_fuku = results[rid]
        act3 = tuple(order[:3])
        act2 = frozenset(order[:2])
        tri_scored = sorted(TRIPLES, key=lambda c: -plprob(ps, c))
        tri_rank = tri_scored.index(act3) + 1 if act3 in tri_scored else 999

        def fuku_p(c):
            return plprob(ps, (c[0], c[1])) + plprob(ps, (c[1], c[0]))
        fuku_scored = sorted(PAIRS, key=lambda c: -fuku_p(c))
        fuku_rank = next((i + 1 for i, c in enumerate(fuku_scored)
                          if frozenset(c) == act2), 999)
        d = {}
        for key in STRATS:
            kind, param = key
            if kind == "triTopN":
                stake = param * 100
                ret = pay_tri if (pay_tri > 0 and tri_rank <= param) else 0
            elif kind == "triRank":
                stake = 100
                ret = pay_tri if (pay_tri > 0 and tri_rank == param) else 0
            else:  # fukuTopN
                stake = param * 100
                ret = pay_fuku if (pay_fuku > 0 and fuku_rank <= param) else 0
            d[key] = (stake, ret)
        per_race[rid] = d
    return per_race


def roi(per_race, rids, key):
    s = sum(per_race[r][key][0] for r in rids)
    ret = sum(per_race[r][key][1] for r in rids)
    return (ret / s * 100) if s > 0 else None


def windows(dlist, size):
    return [dlist[i:i + size] for i in range(0, len(dlist), size)]


def test_a(per_race, joined, dates, by_date, wk):
    print("\n" + "=" * 72)
    print("【テストA】 同じ買い方でも、集計スパンが長いほど実現回収率は安定するか")
    print("=" * 72)
    for key in [("triTopN", 1), ("triTopN", 10), ("triRank", 30), ("fukuTopN", 1)]:
        print(f"\n◆ {label(key)}")
        for wsize, wname in [(1, "1日"), (wk, f"1週({wk}日)"), (len(dates), "全期間")]:
            rois = []
            for w in windows(dates, wsize):
                rids = [r for dd in w for r in by_date[dd]]
                v = roi(per_race, rids, key)
                if v is not None:
                    rois.append(v)
            if rois:
                sd = statistics.pstdev(rois) if len(rois) > 1 else 0.0
                print(f"   {wname:<9} 窓数{len(rois):2d}  平均ROI {statistics.mean(rois):6.1f}%  "
                      f"幅[{min(rois):6.1f}%〜{max(rois):7.1f}%]  SD {sd:5.1f}pt")
        print(f"   （全期間通算 {roi(per_race, joined, key):.1f}%）")


def test_b(per_race, joined, dates, by_date, wk):
    print("\n" + "=" * 72)
    print("【テストB】 『その窓で一番回収率が良かった買い方』は、窓を変えると変わるか")
    print("=" * 72)
    print("\n◆ 日次の『最良の買い方』（3連単戦略ファミリ内）")
    cnt = collections.Counter()
    for dd in dates:
        rids = by_date[dd]
        best = max(TRI_KEYS, key=lambda k: (roi(per_race, rids, k) or -1))
        cnt[best] += 1
        print(f"   {dd}  →  {label(best):<12} ROI {roi(per_race, rids, best):7.1f}%")
    print("   日次ベスト分布: " + " / ".join(f"{label(k)}×{c}" for k, c in cnt.most_common()))

    print(f"\n◆ 週次({wk}日)の『最良の買い方』")
    for w in windows(dates, wk):
        rids = [r for dd in w for r in by_date[dd]]
        best = max(TRI_KEYS, key=lambda k: (roi(per_race, rids, k) or -1))
        print(f"   {w[0]}〜{w[-1]}  →  {label(best):<12} ROI {roi(per_race, rids, best):7.1f}%")

    mbest = max(TRI_KEYS, key=lambda k: (roi(per_race, joined, k) or -1))
    print(f"\n◆ 全期間の『最良の買い方』  →  {label(mbest)}  ROI {roi(per_race, joined, mbest):.1f}%")

    print("\n◆ 持続性テスト: 『前日ベスト戦略』を翌日に使った場合の平均ROI")
    carried, fixed = [], []
    for i in range(len(dates) - 1):
        today, tmr = by_date[dates[i]], by_date[dates[i + 1]]
        bt = max(TRI_KEYS, key=lambda k: (roi(per_race, today, k) or -1))
        v = roi(per_race, tmr, bt)
        if v is not None:
            carried.append(v)
        v1 = roi(per_race, tmr, ("triTopN", 1))
        if v1 is not None:
            fixed.append(v1)
    if carried:
        print(f"   前日ベストを翌日採用: 平均 {statistics.mean(carried):.1f}%  （{len(carried)}日）")
        print(f"   常に『3単 上位1点』固定: 平均 {statistics.mean(fixed):.1f}%  （参照）")


def test_c(per_race, dates, by_date):
    print("\n" + "=" * 72)
    print("【テストC】 前半で選んだ最良戦略を、後半に当てる（学習→検証の汎化）")
    print("=" * 72)
    half = len(dates) // 2
    if half == 0:
        print("   日数が不足（2日以上必要）。スキップ。")
        return
    train_d, test_d = dates[:half], dates[half:]
    train = [r for dd in train_d for r in by_date[dd]]
    test = [r for dd in test_d for r in by_date[dd]]
    print(f"   学習 {train_d[0]}〜{train_d[-1]}（{len(train_d)}日 {len(train):,}R） / "
          f"検証 {test_d[0]}〜{test_d[-1]}（{len(test_d)}日 {len(test):,}R）")
    best_tr = max(TRI_KEYS, key=lambda k: (roi(per_race, train, k) or -1))
    print(f"\n   学習期間で最良 → {label(best_tr)}  学習ROI {roi(per_race, train, best_tr):.1f}%")
    print(f"   それを検証期間に適用 → ROI {roi(per_race, test, best_tr):.1f}%")
    best_te = max(TRI_KEYS, key=lambda k: (roi(per_race, test, k) or -1))
    med = statistics.median([roi(per_race, test, k) for k in TRI_KEYS
                             if roi(per_race, test, k) is not None])
    print(f"   （参考）検証期間で“後知恵”最良 → {label(best_te)} {roi(per_race, test, best_te):.1f}%")
    print(f"   （参考）全戦略を検証期間で評価した中央値 ROI {med:.1f}%")


def main():
    ap = argparse.ArgumentParser(description="集計スパン別・最適な買い方の変化テスト")
    ap.add_argument("--from", dest="date_from", default=None, help="突合開始日 YYYY-MM-DD（含む）")
    ap.add_argument("--week", type=int, default=7, help="週窓の日数（既定7）")
    args = ap.parse_args()

    pw = load_model()
    results = load_results(args.date_from)
    joined = sorted(rid for rid in pw if rid in results and sum(pw[rid]) > 0)
    if not joined:
        print("突合0件：モデル予想履歴と結果(K-file)が同じ日付で重なっていません。")
        return
    dates = sorted({results[rid][0] for rid in joined})
    by_date = collections.defaultdict(list)
    for rid in joined:
        by_date[results[rid][0]].append(rid)

    print(f"突合レース: {len(joined):,}R  期間 {dates[0]}〜{dates[-1]}（{len(dates)}日）")
    per_race = build_per_race(joined, pw, results)
    test_a(per_race, joined, dates, by_date, args.week)
    test_b(per_race, joined, dates, by_date, args.week)
    test_c(per_race, dates, by_date)

    print("\n注意：これは配当ベース（オッズ抜き）の試算。期待値はスパンに依らず一定で、長期化で")
    print("縮むのは実現ROIの分散のみ。『オッズが分かれば+EVで100%超えるか』は backtest_ev.py で別途検証。")


if __name__ == "__main__":
    main()
