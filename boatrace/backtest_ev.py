# -*- coding: utf-8 -*-
"""
EV バックテスト (Phase4)
========================
モデル確率 × 実オッズ = EV。EV >= 閾値 の買い目だけ 100円 均等買いした場合の
回収率・的中率・点数を、実オッズ取得済みの期間について検証する。

買い目候補 = ライブUI(today.html)と同じ「2連単 上位5 / 3連単 上位10」。
realized payout = その買い目が的中したとき odds×100円 (締切オッズで賭けて締切オッズで決済)。
EV>=0.0 の行は「フィルタ無し=フラット買い」のベースライン (回収78-83%帯の再現)。

入力:
  predict_win_oos.csv … race_id, 枠, 登番, p_win, strength, finish_rank (honest OOS)
  data/odds/odds_YYYYMMDD.csv … race_id, bet_type(2t/3t), combo, odds, fetched_at

使い方:
  py -3.13 backtest_ev.py                      # 取得済みオッズ全期間
  py -3.13 backtest_ev.py --dates 20260604-20260617
  py -3.13 backtest_ev.py --csv bets.csv       # 採用ベットを明細出力
"""
import argparse
import csv
import glob
import os
from collections import defaultdict

from predict_combos import plackett_luce_top

PRED = "predict_win_oos.csv"
ODDS_DIR = "data/odds"
THRESHOLDS = [0.0, 1.0, 1.1, 1.2, 1.3]
TOP_2T = 5
TOP_3T = 10
STAKE = 100


def load_predictions(path):
    """race_id -> {'strength':[s1..s6 by 枠], 'order':[枠 of 1着,2着,3着]}"""
    boats = defaultdict(dict)        # rid -> {枠: strength}
    ranks = defaultdict(dict)        # rid -> {finish_rank: 枠}
    with open(path, encoding="cp932") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            rid, waku, _tou, _p, strength, fin = row[:6]
            try:
                waku = int(waku)
                s = float(strength)
            except ValueError:
                continue
            boats[rid][waku] = s
            try:
                ranks[rid][int(fin)] = waku
            except ValueError:
                pass  # F/失格/欠場 などは非数値
    races = {}
    for rid, bs in boats.items():
        if len(bs) < 6:
            continue  # 6艇そろわないレースは除外(strength 配列が作れない)
        strengths = [bs[w] for w in range(1, 7)]
        rk = ranks[rid]
        order = [rk.get(1), rk.get(2), rk.get(3)]
        races[rid] = {"strength": strengths, "order": order}
    return races


def load_odds(dates_filter):
    """(race_id, bet_type, combo) -> odds"""
    odds = {}
    files = sorted(glob.glob(os.path.join(ODDS_DIR, "odds_*.csv")))
    used = []
    for fp in files:
        hd = os.path.basename(fp)[5:13]   # odds_YYYYMMDD.csv
        if dates_filter and hd not in dates_filter:
            continue
        used.append(hd)
        with open(fp, encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                try:
                    o = float(row["odds"])
                except (ValueError, KeyError):
                    continue
                odds[(row["race_id"], row["bet_type"], row["combo"])] = o
    return odds, used


def candidates(strengths):
    """(bet_type, combo_str, p) のリスト = top5 2連単 + top10 3連単"""
    out = []
    for combo, p in plackett_luce_top(strengths, 2, TOP_2T):
        out.append(("2t", "-".join(map(str, combo)), p))
    for combo, p in plackett_luce_top(strengths, 3, TOP_3T):
        out.append(("3t", "-".join(map(str, combo)), p))
    return out


def actual_combo(order, bet_type):
    if bet_type == "2t":
        a, b = order[0], order[1]
        if a and b:
            return f"{a}-{b}"
    else:
        a, b, c = order
        if a and b and c:
            return f"{a}-{b}-{c}"
    return None


def run_backtest(pred_path=PRED, dates_filter=None, detail=False):
    """集計を実行し (meta, stats, detail_rows) を返す。オッズ無しは None。
    stats[threshold][key] (key=2t/3t/all) = {bets,staked,returned,hits,races,roi,hit_rate}。
    すべて素のint/float/strで JSON 化・HTML 埋め込み可。"""
    races = load_predictions(pred_path)
    odds, used_dates = load_odds(dates_filter)
    if not odds:
        return None

    acc = {t: {"2t": _blank(), "3t": _blank()} for t in THRESHOLDS}
    detail_rows = []
    rids_with_odds = sorted({k[0] for k in odds})
    evaluated = 0
    for rid in rids_with_odds:
        race = races.get(rid)
        if not race:
            continue
        evaluated += 1
        for bet_type, combo, p in candidates(race["strength"]):
            o = odds.get((rid, bet_type, combo))
            if o is None:
                continue  # そのオッズが未取得 (発売前/欠番) → 賭けられない
            ev = p * o
            won = (combo == actual_combo(race["order"], bet_type))
            for t in THRESHOLDS:
                if ev >= t:
                    st = acc[t][bet_type]
                    st["staked"] += STAKE
                    st["bets"] += 1
                    st["races"].add(rid)
                    if won:
                        st["returned"] += o * STAKE
                        st["hits"] += 1
            if detail and ev >= 1.0:
                detail_rows.append({
                    "race_id": rid, "bet_type": bet_type, "combo": combo,
                    "p": f"{p:.4f}", "odds": f"{o:.1f}", "ev": f"{ev:.3f}",
                    "won": int(won), "payout": f"{o*STAKE:.0f}" if won else "0",
                })

    stats = {}
    for t in THRESHOLDS:
        comb = _blank()
        row = {}
        for bt in ("2t", "3t"):
            st = acc[t][bt]
            row[bt] = _finalize(st)
            for k in ("bets", "staked", "returned", "hits"):
                comb[k] += st[k]
            comb["races"] |= st["races"]
        row["all"] = _finalize(comb)
        stats[t] = row

    meta = {
        "date_min": min(used_dates) if used_dates else "-",
        "date_max": max(used_dates) if used_dates else "-",
        "n_days": len(used_dates),
        "n_races_odds": len(rids_with_odds),
        "n_evaluated": evaluated,
        "top_2t": TOP_2T, "top_3t": TOP_3T, "stake": STAKE,
        "thresholds": THRESHOLDS,
    }
    return meta, stats, detail_rows


def _blank():
    return {"staked": 0, "returned": 0.0, "bets": 0, "hits": 0, "races": set()}


def _finalize(st):
    roi = (st["returned"] / st["staked"] * 100) if st["staked"] else 0.0
    hr = (st["hits"] / st["bets"] * 100) if st["bets"] else 0.0
    return {"bets": st["bets"], "staked": st["staked"],
            "returned": round(st["returned"], 1), "hits": st["hits"],
            "races": len(st["races"]), "roi": round(roi, 1), "hit_rate": round(hr, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default=PRED)
    ap.add_argument("--dates", default="", help="例 20260604-20260617 / 20260617")
    ap.add_argument("--csv", default="", help="採用ベット明細の出力先")
    args = ap.parse_args()

    dates_filter = None
    if args.dates:
        if "-" in args.dates:
            a, b = args.dates.split("-")
            dates_filter = {str(d) for d in range(int(a), int(b) + 1)}
        else:
            dates_filter = {args.dates}

    result = run_backtest(args.pred, dates_filter, detail=bool(args.csv))
    if result is None:
        print("オッズが見つかりません。fetch_odds.py で取得してください。")
        return
    meta, stats, detail_rows = result

    # ---- レポート ----
    print(f"\n=== EV バックテスト ===")
    print(f"期間: {meta['date_min']}〜{meta['date_max']}  ({meta['n_days']}日)")
    print(f"オッズ取得レース: {meta['n_races_odds']}  /  うち予測あり評価: {meta['n_evaluated']}")
    print(f"買い目候補: 2連単 上位{TOP_2T} + 3連単 上位{TOP_3T}  / 1点 {STAKE}円")
    print(f"※ EV>=0.0 はフィルタ無し(フラット買い)のベースライン\n")

    def line(label, st):
        print(f"  {label:5} 点数{st['bets']:6d}  対象{st['races']:5d}R  "
              f"的中{st['hits']:5d}({st['hit_rate']:5.1f}%)  "
              f"投資{st['staked']:9,d}  払戻{st['returned']:11,.0f}  回収率 {st['roi']:6.1f}%")

    for t in THRESHOLDS:
        tag = "フラット" if t == 0.0 else f"EV>={t:.1f}"
        print(f"[{tag}]")
        line("2連単", stats[t]["2t"])
        line("3連単", stats[t]["3t"])
        line("合計", stats[t]["all"])
        print()

    if args.csv and detail_rows:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
            w.writeheader(); w.writerows(detail_rows)
        print(f"採用ベット明細(EV>=1.0) {len(detail_rows)}件 → {args.csv}")


if __name__ == "__main__":
    main()
