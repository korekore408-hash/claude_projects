# -*- coding: utf-8 -*-
"""
data/ 内の B/K テキストを一括で CSV 化する。
=========================================================================
fetch_range.py が落とした b*.txt / k*.txt のうち、対応する .csv が
無い（または --force）ものだけを変換する。1ファイル=1日。

  - b*.txt → to_csv.py の解析ロジックで b*.csv
  - k*.txt → k_to_csv.py の解析ロジックで k*.csv

使い方:
  py -3 convert_all.py                 # 未変換の txt をすべて CSV 化
  py -3 convert_all.py --force         # 既存 csv も作り直す
  py -3 convert_all.py --start 260101 --end 260614
"""

import argparse
import csv
import glob
import os
import re

import to_csv
import k_to_csv


def key_of(path):
    m = re.search(r"[bk](\d{6})", os.path.basename(path))
    return m.group(1) if m else "999999"


def write_csv(rows, csv_path):
    fieldnames = []
    for r in rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)
    with open(csv_path, "w", encoding="cp932", newline="", errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def convert_program(txt_path, csv_path):
    """to_csv.py のロジックで番組表 txt → 行リスト。"""
    with open(txt_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    rows, place, race_no = [], None, None
    for line in lines:
        p = to_csv.detect_place(line)
        if p:
            place = p
        r = to_csv.detect_race_no(line)
        if r:
            race_no = r
        if to_csv.is_racer_line(line):
            row = {"会場": place, "レース": race_no}
            row.update(to_csv.parse_racer_line(line))
            rows.append(row)
    return rows


def convert_results(txt_path, csv_path):
    """k_to_csv.py のロジックで競走成績 txt → 行リスト。"""
    lines = k_to_csv.open_txt(txt_path)
    venue = date = current_race = None
    payouts, rows = {}, []
    for line in lines:
        m = k_to_csv.SECTION_RE.match(line)
        if m:
            venue = k_to_csv.clean_name(m.group(1))
            date = current_race = None
            payouts = {}
            continue
        if date is None:
            m = k_to_csv.DATE_RE.search(line)
            if m:
                date = f"{m.group(1)}/{int(m.group(2))}/{int(m.group(3))}"
        m = k_to_csv.PAYOUT_RE.match(line)
        if m:
            rn = int(m.group(1))
            payouts[rn] = {
                '3連単_組合': m.group(2), '3連単_配当': int(m.group(3)),
                '3連複_組合': m.group(4), '3連複_配当': int(m.group(5)),
                '2連単_組合': m.group(6), '2連単_配当': int(m.group(7)),
                '2連複_組合': m.group(8), '2連複_配当': int(m.group(9)),
            }
            continue
        m = k_to_csv.RACE_HDR_RE.match(line)
        if m:
            current_race = int(m.group(1))
            continue
        if current_race is not None:
            m = k_to_csv.RACER_RE.match(line)
            if m:
                p = payouts.get(current_race, {})
                rows.append({
                    '会場': venue, '日付': date, 'レース': current_race,
                    '着順': int(m.group(1)), '艇番': int(m.group(2)),
                    '登番': m.group(3), '選手名': k_to_csv.clean_name(m.group(4)),
                    'status': 'finish',
                    'モーター番号': int(m.group(5)), 'ボート番号': int(m.group(6)),
                    '展示タイム': m.group(7), '進入コース': int(m.group(8)),
                    'スタートタイミング': m.group(9),
                    'レースタイム': k_to_csv.clean_racetime(m.group(10)), **p,
                })
                continue
            m = k_to_csv.NONFIN_RE.match(line)
            if m:
                rows.append({
                    '会場': venue, '日付': date, 'レース': current_race,
                    '着順': '', '艇番': int(m.group(2)), '登番': m.group(3),
                    '選手名': k_to_csv.clean_name(m.group(4)),
                    'status': m.group(1).strip(),
                })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None, help="開始 YYMMDD（含む）")
    ap.add_argument("--end", default=None, help="終了 YYMMDD（含む）")
    ap.add_argument("--force", action="store_true", help="既存 csv も作り直す")
    args = ap.parse_args()

    def in_range(k):
        if args.start and k < args.start:
            return False
        if args.end and k > args.end:
            return False
        return True

    jobs = [("data/b*.txt", "b", convert_program),
            ("data/k*.txt", "k", convert_results)]
    total_ok = total_skip = total_empty = 0
    for pat, kind, fn in jobs:
        paths = sorted((p for p in glob.glob(pat) if in_range(key_of(p))),
                       key=key_of)
        for txt in paths:
            stem = os.path.splitext(os.path.basename(txt))[0]
            csv_path = f"data/{stem}.csv"
            if os.path.exists(csv_path) and not args.force:
                total_skip += 1
                continue
            rows = fn(txt, csv_path)
            if not rows:
                print(f"  ! 空: {txt}")
                total_empty += 1
                continue
            write_csv(rows, csv_path)
            total_ok += 1
        print(f"[{kind}] 対象 {len(paths)} 件")
    print(f"○ 変換 {total_ok} / スキップ(既存) {total_skip} / 空 {total_empty}")


if __name__ == "__main__":
    main()
