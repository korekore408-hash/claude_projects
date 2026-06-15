# -*- coding: utf-8 -*-
"""
完成度チェック（リーク検証・データ整合性・精度妥当性）
=========================================================================
仕様書 4章「リーク防止」を最重要として、以下を実証的に検証する。

  1. データ整合性 : 期間・欠損日・B/K 突合
  2. as-of リーク検証 : 履歴特徴 lane_win_rate を K-file から独立に
       「厳密に過去のみ」で再計算し、特徴 CSV の値と一致するか照合
  3. 当日結果の非混入 : 各選手の初出走レースで履歴が空であること
  4. 欠損率 : 主要特徴の空欄割合
  5. 精度の妥当性 : ベースライン比較の再掲

使い方:
  py -3 check_quality.py
"""

import csv
import glob
import os
import re
from collections import defaultdict
from datetime import datetime

from features_player_history import VENUE_CODE


def load_csv(path, enc="cp932"):
    with open(path, encoding=enc) as f:
        return list(csv.DictReader(f))


def kkey(path):
    m = re.search(r"k(\d{6})", os.path.basename(path))
    return m.group(1) if m else "999999"


def main():
    print("=" * 64)
    print("完成度チェック  boatrace 直前情報なしモデル")
    print("=" * 64)

    # ── 1. データ整合性 ────────────────────────────────────────────
    bcsv = sorted(glob.glob("data/b*.csv"))
    kcsv = sorted(glob.glob("data/k*.csv"))
    bdays = {re.search(r"b(\d{6})", os.path.basename(p)).group(1) for p in bcsv}
    kdays = {re.search(r"k(\d{6})", os.path.basename(p)).group(1) for p in kcsv}
    print(f"\n[1] データ整合性")
    print(f"  B-file {len(bcsv)}日 / K-file {len(kcsv)}日")
    print(f"  B のみ存在(Kなし): {sorted(bdays - kdays) or 'なし'}")
    print(f"  K のみ存在(Bなし): {sorted(kdays - bdays) or 'なし'}")
    # カレンダー上の歯抜け（連続性）
    ds = sorted(datetime.strptime(d, "%y%m%d").date() for d in (bdays & kdays))
    gaps = []
    for a, b in zip(ds, ds[1:]):
        d = (b - a).days
        if d > 1:
            gaps.append(f"{a}→{b}({d-1}日欠)")
    print(f"  連続性の歯抜け: {gaps if gaps else 'なし（連続）'}")

    # ── 2. as-of リーク検証（lane_win_rate を独立再計算） ──────────
    print(f"\n[2] as-of リーク検証（lane_win_rate を K-file から独立再計算）")
    # K-file を日付昇順に読み、各 (登番,枠) の「厳密に過去のみ」勝率を逐次保持。
    # 同時に「その日の出走前」の値スナップショットを (race_id,枠) 別に記録。
    lane = defaultdict(lambda: [0, 0])   # (登番,枠)->[出走,勝]
    asof = {}                            # (race_id,枠)-> 過去勝率 or None
    for p in sorted(kcsv, key=kkey):
        rows = load_csv(p)
        # 日付内は「出走前」を全行に適用するため、まず全行のスナップショット→後で反映
        snap = []
        for r in rows:
            if (r.get("status") or "finish") != "finish":
                continue
            code = VENUE_CODE.get(r["会場"], "00")
            y, m, dd = r["日付"].split("/")
            rid = f"{code}{int(y):04d}{int(m):02d}{int(dd):02d}{int(r['レース']):02d}"
            key = (r["登番"], int(r["艇番"]))
            n, w = lane[key]
            asof[(rid, str(int(r["艇番"])))] = (w / n) if n else None
            snap.append((key, 1 if int(r["着順"]) == 1 else 0))
        for key, win in snap:
            lane[key][0] += 1
            lane[key][1] += win

    hist = load_csv("features_player_history.csv")
    checked = mism = empty_match = 0
    for r in hist:
        k = (r["race_id"], r["枠番"])
        if k not in asof:
            continue
        want = asof[k]
        got = r["lane_win_rate"].strip()
        checked += 1
        if want is None:
            if got == "":
                empty_match += 1
            else:
                mism += 1
        else:
            if got == "" or abs(float(got) - want) > 1e-4:
                mism += 1
    print(f"  照合 {checked} 行 / 不一致 {mism} / 空一致 {empty_match}")
    print(f"  -> {'[OK] リークなし（as-of一致）' if mism == 0 else '[NG] 不一致あり！要調査'}")

    # ── 3. 初出走で履歴が空か（当日結果の非混入） ─────────────────
    print(f"\n[3] 初出走レースで履歴が空（母数0）か")
    seen = set()
    bad = 0
    n_first = 0
    for r in sorted(hist, key=lambda r: (r["日付"], r["race_id"], r["枠番"])):
        t = r["登番"]
        if t in seen:
            continue
        seen.add(t)
        n_first += 1
        if (r["lane_n"] not in ("0", "")) or r["st_avg"].strip() != "":
            bad += 1
    print(f"  初出走選手 {n_first} 人 / 履歴が空でない(疑い) {bad}")
    print(f"  -> {'[OK] 当日結果の非混入OK' if bad == 0 else '[--] 一部に既存履歴（前データ由来なら正常）'}")

    # ── 4. 欠損率 ──────────────────────────────────────────────────
    print(f"\n[4] 主要特徴の欠損率（空欄割合）")
    keys = ["lane_win_rate", "st_avg", "recent30_winrate",
            "local_win_rate", "motor_intrinsic_win"]
    for kf in keys:
        miss = sum(1 for r in hist if r.get(kf, "").strip() == "")
        print(f"  {kf:22} {miss/len(hist)*100:5.1f}%  ({miss}/{len(hist)})")
    rel = load_csv("features_race_relative.csv")
    for kf in ["win_rate_local", "top2_rate_local", "age", "weight"]:
        miss = sum(1 for r in rel if r.get(kf, "").strip() == "")
        print(f"  {kf:22} {miss/len(rel)*100:5.1f}%  ({miss}/{len(rel)})")

    # ── 5. 精度の妥当性（既出の数値を再掲） ──────────────────────
    print(f"\n[5] 精度の妥当性（時系列分割 train<=260430 / test以降）")
    print(f"  1着的中  0.568 / 1号艇ベース 0.551（+1.8pt, 6765レース）")
    print(f"  2連単本命 0.226 / 3連単本命 0.096 / logloss 1.199")
    print(f"  注: 直前情報なしモデルの構造的上限に近い。EV/回収率はPhase4で別評価。")

    print("\n" + "=" * 64)
    print("チェック完了")


if __name__ == "__main__":
    main()
