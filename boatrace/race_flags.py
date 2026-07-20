# -*- coding: utf-8 -*-
"""
レース対象外フラグの生成
=========================================================================
予想の的中率を「クリーンなレース」だけで測るため、本番で前提が崩れた／
レースが乱れたレースを対象外として印を付ける。K-file から判定する。

対象外の条件:
  - レーン変更      : 進入コース ≠ 艇番（枠なりが崩れた）。完走艇で判定。
  - 転覆・失格系    : 出走したが完走しなかった艇（status S*/L*）を含む。
                      ※ 転覆を S/L から個別に切り分けるのは確実でないため、
                        完走しなかった艇を含むレースを安全側でまとめて対象外。
  - フライング      : status F を含む。
  - 欠場            : status K* を含む（出走艇が欠けた）。
  - 安定板          : 直前情報履歴(data/before/*.json の weather.stabilizer)から判定。
                      その履歴が無い過去日は 0 のまま（K/B-file には情報が無い）。

出力: race_flags.csv
  race_id, lane_changed, has_flying, has_dnf, has_absent, stabilizer,
  excluded, reason（日本語・「/」区切り）

使い方:
  py -3 race_flags.py
"""

import argparse
import csv
import glob
from collections import defaultdict

from features_player_history import VENUE_CODE


def race_id_of(r):
    code = VENUE_CODE.get(r["会場"], "00")
    y, m, d = r["日付"].split("/")
    return f"{code}{int(y):04d}{int(m):02d}{int(d):02d}{int(r['レース']):02d}"


def load_stabilizer(before_glob="data/before/before_*.json"):
    """直前情報キャッシュ(data/before/*.json)から安定板使用レースの race_id 集合を返す。
    fetch_before.py が weather.stabilizer を記録した日以降のレースだけ判定可能。
    履歴が無い過去日は空のまま（従来どおり stabilizer=0）で、判定を偽装しない。"""
    import json
    out = set()
    for p in sorted(glob.glob(before_glob)):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        for rid, v in (d.items() if isinstance(d, dict) else []):
            w = ((v or {}).get("ex") or {}).get("weather") or {}
            if w.get("stabilizer"):
                out.add(rid)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="data/k*.csv")
    ap.add_argument("--out", default="race_flags.csv")
    args = ap.parse_args()

    rows = defaultdict(list)
    for p in sorted(glob.glob(args.glob)):
        with open(p, encoding="cp932") as f:
            for r in csv.DictReader(f):
                rows[race_id_of(r)].append(r)

    stab_ids = load_stabilizer()   # 安定板使用レース（直前情報履歴から。無い日は空集合）

    out = []
    for rid, rs in rows.items():
        statuses = [(r.get("status") or "finish") for r in rs]
        fins = [r for r in rs if (r.get("status") or "finish") == "finish"]

        lane_changed = any(int(r["艇番"]) != int(r["進入コース"]) for r in fins)
        has_flying = any(s == "F" for s in statuses)
        has_dnf = any(s[0] in ("S", "L") for s in statuses if s != "finish")
        has_absent = any(s[0] == "K" for s in statuses if s != "finish")
        stabilizer = 1 if rid in stab_ids else 0   # 直前情報履歴から（無い過去日は0）

        reasons = []
        if lane_changed:
            reasons.append("レーン変更")
        if has_dnf:
            reasons.append("転覆・失格系")
        if has_flying:
            reasons.append("フライング")
        if has_absent:
            reasons.append("欠場")
        if stabilizer:
            reasons.append("安定板")
        excluded = 1 if reasons else 0

        out.append({
            "race_id": rid,
            "lane_changed": lane_changed and 1 or 0,
            "has_flying": has_flying and 1 or 0,
            "has_dnf": has_dnf and 1 or 0,
            "has_absent": has_absent and 1 or 0,
            "stabilizer": stabilizer,
            "excluded": excluded,
            "reason": " / ".join(reasons),
        })

    with open(args.out, "w", encoding="cp932", newline="", errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    nexc = sum(r["excluded"] for r in out)
    print(f"○ 出力: {args.out}（{len(out)} レース / 対象外 {nexc} / 対象 {len(out)-nexc}）")
    for key, lab in [("lane_changed", "レーン変更"), ("has_dnf", "転覆・失格系"),
                     ("has_flying", "フライング"), ("has_absent", "欠場"),
                     ("stabilizer", "安定板(直前情報履歴)")]:
        print(f"    {lab:16} {sum(r[key] for r in out)}")


if __name__ == "__main__":
    main()
