# -*- coding: utf-8 -*-
"""結果・配当ローダ（v1 の data/k*.csv を読み取り再利用）。

決済は K-file の**実配当**と**公式組番**で行う（取得時オッズでの決済はしない — T2）。
"""
import csv
import glob
import os
import re

try:
    from . import config
except ImportError:
    import config


def _rid(row, code):
    y, m, d = row["日付"].split("/")
    return f"{code}{int(y):04d}{int(m):02d}{int(d):02d}{int(row['レース']):02d}"


def _combo(row, key):
    """公式印字の組合（'1-2' 等）。列名の揺れ（組合/組番）を吸収。"""
    return (row.get(f"{key}_組合") or row.get(f"{key}_組番") or "").strip()


def load_results(dates=None, data_dir=None):
    """K-file → {rid: {"date": "YYYY-MM-DD", "combo2": "1-2", "pay2": int,
                        "combo3": "1-2-3", "pay3": int}}。
    的中判定は公式の組合文字列（K-file 印字）で行う。印字が無い年式の
    ファイルは着順1-3の艇番から再構成する。dates={'YYYYMMDD',...} で絞り込み。"""
    data_dir = data_dir or config.V1_DATA
    yy = {d[2:] for d in dates} if dates else None
    out = {}
    orders = {}                     # rid -> {着順int: 艇番int}（組合印字なし時の再構成用）
    for kp in sorted(glob.glob(os.path.join(data_dir, "k*.csv"))):
        m = re.search(r"k(\d{6})", os.path.basename(kp))
        if not m or (yy is not None and m.group(1) not in yy):
            continue
        try:
            with open(kp, encoding="cp932") as f:
                for r in csv.DictReader(f):
                    if (r.get("status") or "finish") != "finish":
                        continue
                    code = config.VENUE_CODE.get(r["会場"])
                    if not code:
                        continue
                    rid = _rid(r, code)
                    fin = (r.get("着順") or "").strip()
                    if fin.isdigit():
                        try:
                            orders.setdefault(rid, {})[int(fin)] = int(r["艇番"])
                        except (ValueError, KeyError):
                            pass
                    if rid in out:
                        continue
                    try:
                        rec = {
                            "date": rid[2:6] + "-" + rid[6:8] + "-" + rid[8:10],
                            "combo2": _combo(r, "2連単"),
                            "pay2": int(r["2連単_配当"]),
                            "combo3": _combo(r, "3連単"),
                            "pay3": int(r["3連単_配当"]),
                        }
                    except (ValueError, KeyError):
                        continue
                    out[rid] = rec
        except OSError:
            continue
    # 組合印字が無いレースは着順1-3から再構成（同着等で3着まで揃わなければ空のまま）
    for rid, rec in out.items():
        od = orders.get(rid, {})
        if not rec["combo2"] and 1 in od and 2 in od:
            rec["combo2"] = f"{od[1]}-{od[2]}"
        if not rec["combo3"] and 1 in od and 2 in od and 3 in od:
            rec["combo3"] = f"{od[1]}-{od[2]}-{od[3]}"
    return out


def settle(bet_type, combo_str, result):
    """(的中したか, 100円あたり実払戻額)。result=load_results の1レース分 or None。"""
    if not result:
        return False, 0
    if bet_type == "2t":
        return (combo_str == result["combo2"], result["pay2"])
    return (combo_str == result["combo3"], result["pay3"])
