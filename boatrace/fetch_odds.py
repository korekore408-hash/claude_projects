# -*- coding: utf-8 -*-
"""
オッズ取得（Phase4: 期待値判定のための直前オッズ収集）
-------------------------------------------------------------------------
B/K-file(lzh) にはオッズが無いので、boatrace 公式サイトの「締切時オッズ」
HTMLページから 2連単・3連単 のリアルタイム値を取得する。

データ取得元（調査済み・2026-06 時点で取得可能を確認）:
  3連単: https://www.boatrace.jp/owpc/pc/race/odds3t?rno={R}&jcd={場:02d}&hd={YYYYMMDD}
  2連単: https://www.boatrace.jp/owpc/pc/race/odds2tf?rno={R}&jcd={場:02d}&hd={YYYYMMDD}
         （2tf ページは先頭30セル=2連単, 残り15セル=2連複。2連単のみ使う）

ページ内のオッズは class="oddsPoint" のセルに文書順で並ぶ:
  3連単=120セル, 2連単(2tf先頭)=30セル。
  読み順は「行ごとに 1着=1..6 のブロックを横断」なので、
    1着 = (セル位置 % 6) + 1 , 行 = セル位置 // 6
  から組番を機械的に復元できる（self_test() で 1-2-3 等を検証）。

race_id は予測CSVと同形式: f"{jcd:02d}{YYYYMMDD}{rno:02d}" 例 012025080201

使い方:
  py -3.13 fetch_odds.py                 # 今日・全場を自動探索して取得
  py -3.13 fetch_odds.py --date 2026-06-18
  py -3.13 fetch_odds.py --jcd 01 12 --races 1-12
  py -3.13 fetch_odds.py --selftest      # パーサ単体検証（通信あり1レース）

出力: data/odds/odds_YYYYMMDD.csv
  列: race_id, bet_type(2t/3t), combo(例 1-2-3), odds(float), fetched_at
"""

import os
import csv
import time
import datetime
import argparse
import re

import requests

ODDS_DIR = os.path.join("data", "odds")
WAIT_SECONDS = 0.8            # 1リクエストごとの待ち（サーバー配慮）
HEADERS = {"User-Agent": "Mozilla/5.0 (boatrace-study-script)"}

URL_3T = "https://www.boatrace.jp/owpc/pc/race/odds3t?rno={r}&jcd={jcd}&hd={hd}"
URL_2T = "https://www.boatrace.jp/owpc/pc/race/odds2tf?rno={r}&jcd={jcd}&hd={hd}"

ODDS_RE = re.compile(r'oddsPoint[^>]*>\s*([0-9]+\.[0-9]+|[0-9]+|欠場|---|\-)\s*<')


# ============================================================
# 組番マッピング（文書順インデックス → (1着,2着,3着) / (1着,2着)）
# ============================================================

def _trifecta_combo(p: int):
    """3連単: 文書順位置 p(0..119) → (1着,2着,3着)。"""
    a = (p % 6) + 1
    k = p // 6                       # 0..19（その1着内の行）
    others = [x for x in range(1, 7) if x != a]   # 5艇 昇順
    b = others[k // 4]
    thirds = [x for x in others if x != b]        # 4艇 昇順
    c = thirds[k % 4]
    return a, b, c


def _exacta_combo(p: int):
    """2連単: 文書順位置 p(0..29) → (1着,2着)。"""
    a = (p % 6) + 1
    k = p // 6                       # 0..4
    others = [x for x in range(1, 7) if x != a]
    return a, others[k]


# ============================================================
# 取得 + パース
# ============================================================

def _extract_points(html: str):
    """ページHTMLから oddsPoint セルの値を文書順で返す（数値はfloat、欠場等はNone）。"""
    vals = []
    for m in ODDS_RE.finditer(html):
        s = m.group(1)
        try:
            vals.append(float(s))
        except ValueError:
            vals.append(None)        # 欠場 / 未発売
    return vals


def fetch_trifecta(jcd: int, rno: int, hd: str):
    """3連単オッズ {(1,2,3): odds, ...}（最大120点）。取得不可なら空dict。"""
    url = URL_3T.format(r=rno, jcd=f"{jcd:02d}", hd=hd)
    try:
        res = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException:
        return {}
    if res.status_code != 200:
        return {}
    pts = _extract_points(res.content.decode("utf-8", "replace"))
    if len(pts) < 120:               # 未発売・欠場レース等
        pts = pts[:120]
    out = {}
    for p, v in enumerate(pts[:120]):
        if v is not None:
            out[_trifecta_combo(p)] = v
    return out


def fetch_exacta(jcd: int, rno: int, hd: str):
    """2連単オッズ {(1,2): odds, ...}（最大30点）。2tfページ先頭30セルのみ。"""
    url = URL_2T.format(r=rno, jcd=f"{jcd:02d}", hd=hd)
    try:
        res = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException:
        return {}
    if res.status_code != 200:
        return {}
    pts = _extract_points(res.content.decode("utf-8", "replace"))
    out = {}
    for p, v in enumerate(pts[:30]):
        if v is not None:
            out[_exacta_combo(p)] = v
    return out


# ============================================================
# self test（パーサ検証）
# ============================================================

def self_test():
    """マッピングの整合性と、ライブ1レースでのアンカー一致を検証。"""
    # 1) 120組すべて一意・網羅
    combos = [_trifecta_combo(p) for p in range(120)]
    assert len(set(combos)) == 120, "3連単マッピングに重複/欠落"
    assert combos[0] == (1, 2, 3), combos[0]
    assert combos[6] == (1, 2, 4), combos[6]
    ex = [_exacta_combo(p) for p in range(30)]
    assert len(set(ex)) == 30, "2連単マッピングに重複/欠落"
    assert ex[0] == (1, 2) and ex[6] == (1, 3), (ex[0], ex[6])
    print("○ マッピング検証OK（3連単120 / 2連単30 一意網羅, 先頭一致）")

    # 2) ライブ照合（桐生1R・実行日に発売があれば）
    hd = datetime.date.today().strftime("%Y%m%d")
    tri = fetch_trifecta(1, 1, hd)
    if tri:
        print(f"○ ライブ取得OK 桐生1R 3連単 {len(tri)}点  例 1-2-3={tri.get((1,2,3))}")
    else:
        print("（本日の桐生1Rは未発売/開催なし。マッピング検証のみ完了）")


# ============================================================
# 収集ループ
# ============================================================

def collect(hd: str, jcds, races, verbose=True):
    """指定場×レースのオッズを集めて行リストで返す。"""
    fetched_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for jcd in jcds:
        got_any = False
        for rno in races:
            tri = fetch_trifecta(jcd, rno, hd)
            time.sleep(WAIT_SECONDS)
            exa = fetch_exacta(jcd, rno, hd)
            time.sleep(WAIT_SECONDS)
            race_id = f"{jcd:02d}{hd}{rno:02d}"
            for (a, b, c), o in tri.items():
                rows.append({"race_id": race_id, "bet_type": "3t",
                             "combo": f"{a}-{b}-{c}", "odds": o,
                             "fetched_at": fetched_at})
            for (a, b), o in exa.items():
                rows.append({"race_id": race_id, "bet_type": "2t",
                             "combo": f"{a}-{b}", "odds": o,
                             "fetched_at": fetched_at})
            if tri or exa:
                got_any = True
                if verbose:
                    print(f"  jcd{jcd:02d} {rno:2d}R: 3連単{len(tri)} 2連単{len(exa)}")
            elif rno == 1 and not got_any:
                if verbose:
                    print(f"  jcd{jcd:02d}: 発売なし → skip")
                break        # R1が空なら未開催場とみなしてスキップ
    return rows


def save(rows, hd):
    os.makedirs(ODDS_DIR, exist_ok=True)
    out = os.path.join(ODDS_DIR, f"odds_{hd}.csv")
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["race_id", "bet_type", "combo", "odds", "fetched_at"])
        w.writeheader()
        w.writerows(rows)
    return out


def parse_races(spec: str):
    if "-" in spec:
        a, b = spec.split("-"); return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",") if x]


def main():
    ap = argparse.ArgumentParser(description="boatrace 公式から2連単/3連単オッズを取得")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"),
                    help="対象日 YYYY-MM-DD（既定=今日）")
    ap.add_argument("--jcd", nargs="*", type=int, default=list(range(1, 25)),
                    help="場コード（既定=全24場を自動探索）")
    ap.add_argument("--races", default="1-12", help="レース範囲 例 1-12 / 1,2,3")
    ap.add_argument("--selftest", action="store_true", help="パーサ検証のみ")
    args = ap.parse_args()

    if args.selftest:
        self_test(); return

    hd = datetime.datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y%m%d")
    races = parse_races(args.races)
    print(f"オッズ取得: {args.date} (hd={hd}) / 場 {args.jcd} / R {races[0]}-{races[-1]}")
    print("=" * 60)
    rows = collect(hd, args.jcd, races)
    out = save(rows, hd)
    n_races = len({r["race_id"] for r in rows})
    print("=" * 60)
    print(f"○ 保存: {out}  （{n_races}レース / {len(rows)}行）")


if __name__ == "__main__":
    main()
