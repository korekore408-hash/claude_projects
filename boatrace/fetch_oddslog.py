# -*- coding: utf-8 -*-
"""オッズ時系列ログの取り出し（worker/odds_logger.js の /export を叩いてCSV化）。

  py -3 fetch_oddslog.py --base https://boatrace-odds-logger.XXX.workers.dev \
        --token <EXPORT_TOKEN> [--date 20260708] [--all]

出力: data/odds_log/oddslog_YYYYMMDD.csv
  列 = date,hhmm,jcd,rno,close,kind(2t/3t),combo,odds
  同じレース×組合せが hhmm 違いで複数行＝時系列。締切直前の行が最終オッズ相当。
"""
import argparse
import csv
import os

import requests


def export_date(base, token, date):
    r = requests.get(f"{base}/export", params={"date": date, "token": token}, timeout=60)
    r.raise_for_status()
    j = r.json()
    rows = []
    for hhmm, entries in sorted(j.get("snapshots", {}).items()):
        for e in entries:
            for kind, key in (("2t", "o2"), ("2f", "o2f"), ("3t", "o3")):
                for combo, odds in (e.get(key) or {}).items():
                    rows.append([date, hhmm, e["jcd"], e["rno"], e.get("close", ""),
                                 kind, combo, odds])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="workerのURL（末尾スラッシュなし）")
    ap.add_argument("--token", required=True)
    ap.add_argument("--date", help="YYYYMMDD（省略時 --all）")
    ap.add_argument("--all", action="store_true", help="保存済み全日付を取得")
    args = ap.parse_args()

    dates = [args.date] if args.date else None
    if dates is None:
        r = requests.get(f"{args.base}/dates", params={"token": args.token}, timeout=60)
        r.raise_for_status()
        dates = r.json()
        print(f"保存済み {len(dates)} 日分: {dates}")
    os.makedirs(os.path.join("data", "odds_log"), exist_ok=True)
    for d in dates:
        rows = export_date(args.base, args.token, d)
        out = os.path.join("data", "odds_log", f"oddslog_{d}.csv")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "hhmm", "jcd", "rno", "close", "kind", "combo", "odds"])
            w.writerows(rows)
        print(f"○ {out}  {len(rows)}行")


if __name__ == "__main__":
    main()
