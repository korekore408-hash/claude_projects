# -*- coding: utf-8 -*-
"""今日(指定日)の AI予想（10万戦略）と 鉄板×EV ピックの【実結果】を評価する。
K-file の着順を既存 predict_win.csv に注入して features 再構築なしで集計。
使い方: py -3.13 eval_today.py --date 2026-06-23 [--ev 1.5]"""
import argparse
import csv
import datetime

from build_today import load, load_payouts
from features_player_history import VENUE_CODE
from make_ai_yosou import build_races, allocate, summarize, regime_table


def inject_results(pred, kpath, ymd):
    """K-file CSV から着順を pred[(rid,枠)]['finish_rank'] に注入。"""
    n = 0
    for r in load(kpath):
        if (r.get("status") or "") != "finish":
            continue
        code = VENUE_CODE.get(r["会場"], "00")
        y, mo, dd = r["日付"].split("/")
        rid = f"{code}{int(y):04d}{int(mo):02d}{int(dd):02d}{int(r['レース']):02d}"
        if rid[2:10] != ymd:
            continue
        w = str(int(r["艇番"]))
        k = (rid, w)
        if k in pred:
            pred[k]["finish_rank"] = r["着順"]
            n += 1
    return n


def load_odds_csv(hd):
    import os
    p = f"data/odds/odds_{hd}.csv"
    out = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rid = r["race_id"]; parts = tuple(int(x) for x in r["combo"].split("-"))
            try:
                o = float(r["odds"])
            except ValueError:
                continue
            out.setdefault(rid, {"2t": {}, "3t": {}})[r["bet_type"]][parts] = o
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--ev", type=float, default=1.5)
    args = ap.parse_args()
    d = datetime.date.fromisoformat(args.date)
    ymd = d.strftime("%Y%m%d")
    kk = d.strftime("%y%m%d")

    pred = {(r["race_id"], r["枠番"]): r for r in load("predict_win.csv")}
    meta = {(r["race_id"], r["枠番"]): r for r in load("features_race_relative.csv")}
    hist = {(r["race_id"], r["枠番"]): r for r in load("features_player_history.csv")}
    n = inject_results(pred, f"data/k{kk}.csv", ymd)
    payout = load_payouts([args.date])
    print(f"結果注入: {n}艇  payout: {len(payout)}R")

    races = build_races(ymd, pred, meta, hist, payout)
    nres = sum(1 for r in races if r["has_res"])
    sel, spent = allocate(races)
    # 結果のあるものだけで集計
    sel_res = [r for r in sel if r["has_res"]]
    s = summarize(sel_res)
    print(f"\n=== {args.date} AI予想 10万戦略の実結果 ===")
    print(f"  選定{len(sel)}R(投資見込{spent:,}円) / 結果確定{len(sel_res)}R")
    print(f"  投資{s['stake']:,}円  払戻{s['pay']:,}円  回収率{s['ret']}%  "
          f"(2連単的中{s['h2']} / 3連単的中{s['h3']})")
    print("  --- 区分別 ---")
    for row in regime_table(sel_res):
        lab, nn, st, pay, ret, h2, h3 = row
        print(f"   {lab}: {nn}R 投資{st:,} 払戻{pay:,} 回収{ret}% (2単{h2}/3単{h3})")

    # 鉄板×EV ピックの結果
    odds = load_odds_csv(ymd)
    if odds:
        tot_st = tot_pay = nb = nhit = 0
        for r in races:
            if r["regime"] != "鉄板" or not r["has_res"]:
                continue
            o = odds.get(r["rid"])
            if not o:
                continue
            order = r["order"]
            act2 = tuple(order[:2]) if len(order) >= 2 else None
            act3 = tuple(order[:3]) if len(order) >= 3 else None
            for combo, p in r["ex2"]:
                od = o["2t"].get(combo)
                if od is None or p * od < args.ev:
                    continue
                nb += 1; tot_st += 100
                if act2 and combo == act2:
                    nhit += 1; tot_pay += r["po"][0]
            for combo, p in r["ex3"]:
                od = o["3t"].get(combo)
                if od is None or p * od < args.ev:
                    continue
                nb += 1; tot_st += 100
                if act3 and combo == act3:
                    nhit += 1; tot_pay += r["po"][1]
        ret = round(tot_pay / tot_st * 100, 1) if tot_st else 0
        print(f"\n=== 鉄板×EV≥{args.ev} ピックの実結果 ===")
        print(f"  {nb}点 投資{tot_st:,}円  払戻{tot_pay:,}円  回収率{ret}%  的中{nhit}本")
    else:
        print("\n(今日の実オッズキャッシュが無いので EV ピック評価はスキップ)")


if __name__ == "__main__":
    main()
