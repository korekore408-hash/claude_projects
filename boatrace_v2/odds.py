# -*- coding: utf-8 -*-
"""オッズ取得・厳格パース・スナップショット保存（v2）。

v1 からの改善:
  - セル数を厳格検証（3連単=120 / 2tf=45 or 30）。不一致は「構造変化の疑い」として
    空を返し警告する（位置ベース復元のサイレント誤マッピングを防ぐ）— T3
  - odds < 1.0 の異常値は棄却 — T3
  - 保存は data/odds/odds_snap_YYYYMMDD.csv への**追記**（fetched_at 付きスナップショット。
    上書きしないので取得時点の履歴が残り、購入時点フィルタに使える）— T2
  - 取得は net.polite_get 経由（同時3本・間隔つき）で並列化 — T7/T8

使い方:
  python odds.py --date 2026-07-01                 # 今日・全場スナップショット取得
  python odds.py --date 2026-07-01 --jcd 1 12 --races 1-12
"""
import argparse
import csv
import datetime
import os
import re
from concurrent.futures import ThreadPoolExecutor

try:
    from . import config, net
except ImportError:
    import config
    import net

URL_3T = "https://www.boatrace.jp/owpc/pc/race/odds3t?rno={r}&jcd={jcd}&hd={hd}"
URL_2T = "https://www.boatrace.jp/owpc/pc/race/odds2tf?rno={r}&jcd={jcd}&hd={hd}"

ODDS_RE = re.compile(r'oddsPoint[^>]*>\s*([0-9]+\.[0-9]+|[0-9]+|欠場|---|\-)\s*<')

CELLS_3T = 120          # 3連単ページの oddsPoint セル数
CELLS_2TF = (45, 30)    # 2tfページ: 2連単30+2連複15（2連複が無い構成も許容）


# ---------------- 組番マッピング（文書順位置 → 組番） ----------------

def trifecta_combo(p):
    """3連単: 位置 p(0..119) → (1着,2着,3着)。"""
    a = (p % 6) + 1
    k = p // 6
    others = [x for x in range(1, 7) if x != a]
    b = others[k // 4]
    thirds = [x for x in others if x != b]
    return a, b, thirds[k % 4]


def exacta_combo(p):
    """2連単: 位置 p(0..29) → (1着,2着)。"""
    a = (p % 6) + 1
    others = [x for x in range(1, 7) if x != a]
    return a, others[p // 6]


# ---------------- パース（厳格検証つき） ----------------

def extract_points(html):
    """oddsPoint セル値を文書順で返す（float / 欠場・未発売は None）。"""
    vals = []
    for m in ODDS_RE.finditer(html):
        s = m.group(1)
        try:
            vals.append(float(s))
        except ValueError:
            vals.append(None)
    return vals


def parse_trifecta(html, ctx=""):
    """3連単 {combo: odds}。未発売=空dict。セル数不一致=構造変化の疑い→空dict+警告。"""
    pts = extract_points(html)
    if not pts:
        return {}
    if len(pts) != CELLS_3T:
        print(f"[odds][warn] 3連単セル数 {len(pts)} != {CELLS_3T} {ctx} → 構造変化の疑い・破棄")
        return {}
    return {trifecta_combo(p): v for p, v in enumerate(pts)
            if v is not None and v >= 1.0}


def parse_exacta(html, ctx=""):
    """2連単 {combo: odds}（2tfページ先頭30セル）。セル数不一致は破棄+警告。"""
    pts = extract_points(html)
    if not pts:
        return {}
    if len(pts) not in CELLS_2TF:
        print(f"[odds][warn] 2tfセル数 {len(pts)} not in {CELLS_2TF} {ctx} → 構造変化の疑い・破棄")
        return {}
    return {exacta_combo(p): v for p, v in enumerate(pts[:30])
            if v is not None and v >= 1.0}


# ---------------- 取得 ----------------

def fetch_race_odds(jcd, rno, hd):
    """1レースの (3連単dict, 2連単dict) を polite fetch で取得。"""
    ctx = f"jcd{jcd:02d} {rno}R {hd}"
    h3 = net.polite_get(URL_3T.format(r=rno, jcd=f"{jcd:02d}", hd=hd))
    h2 = net.polite_get(URL_2T.format(r=rno, jcd=f"{jcd:02d}", hd=hd))
    tri = parse_trifecta(h3, ctx) if h3 else {}
    exa = parse_exacta(h2, ctx) if h2 else {}
    return tri, exa


# ---------------- スナップショット保存・読込 ----------------

SNAP_COLS = ["race_id", "bet_type", "combo", "odds", "fetched_at"]


def snap_path(hd):
    return os.path.join(config.ODDS_SNAP_DIR, f"odds_snap_{hd}.csv")


def append_snapshot(hd, rid, tri, exa, fetched_at=None):
    """取得結果を追記保存（上書きしない）。返り値=書いた行数。"""
    config.ensure_dirs()
    fetched_at = fetched_at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    p = snap_path(hd)
    new = not os.path.exists(p)
    n = 0
    with open(p, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SNAP_COLS)
        if new:
            w.writeheader()
        for combo, o in sorted(tri.items()):
            w.writerow({"race_id": rid, "bet_type": "3t",
                        "combo": "-".join(map(str, combo)), "odds": o,
                        "fetched_at": fetched_at})
            n += 1
        for combo, o in sorted(exa.items()):
            w.writerow({"race_id": rid, "bet_type": "2t",
                        "combo": "-".join(map(str, combo)), "odds": o,
                        "fetched_at": fetched_at})
            n += 1
    return n


def load_snapshots(hd):
    """v2スナップショット → {(rid, bt, combo): [(fetched_at, odds), ...]}（時刻昇順）。
    v2 が無い日は v1 の odds_YYYYMMDD.csv をフォールバック読込（fetched_at はあるが
    上書き保存のため最終値のみ＝履歴なしの1点として扱う）。"""
    out = {}
    p = snap_path(hd)
    legacy = False
    if not os.path.exists(p):
        p = os.path.join(config.V1_ODDS_DIR, f"odds_{hd}.csv")
        legacy = True
    if not os.path.exists(p):
        return out, legacy
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                o = float(r["odds"])
            except (ValueError, KeyError):
                continue
            if o < 1.0:
                continue
            key = (r["race_id"], r["bet_type"], r["combo"])
            out.setdefault(key, []).append((r.get("fetched_at", ""), o))
    for v in out.values():
        v.sort()
    return out, legacy


# ---------------- CLI（全場スナップショット収集） ----------------

def parse_races(spec):
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",") if x]


def collect(hd, jcds, races):
    """開催場判定（R1）→ 開催場の全レースを並列取得して追記保存。"""
    fetched_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENCY) as ex:
        probe = list(ex.map(lambda j: (j, fetch_race_odds(j, races[0], hd)), jcds))
    held, total = [], 0
    for jcd, (tri, exa) in probe:
        if tri or exa:
            held.append(jcd)
            rid = f"{jcd:02d}{hd}{races[0]:02d}"
            total += append_snapshot(hd, rid, tri, exa, fetched_at)
    print(f"開催場: {held or 'なし'}")
    todo = [(j, r) for j in held for r in races[1:]]
    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENCY) as ex:
        for (jcd, rno), (tri, exa) in zip(todo, ex.map(
                lambda t: fetch_race_odds(t[0], t[1], hd), todo)):
            rid = f"{jcd:02d}{hd}{rno:02d}"
            total += append_snapshot(hd, rid, tri, exa, fetched_at)
    print(f"○ スナップショット追記 {total}行 → {snap_path(hd)}")


def main():
    ap = argparse.ArgumentParser(description="オッズをスナップショット取得（v2・追記保存）")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--jcd", nargs="*", type=int, default=list(range(1, 25)))
    ap.add_argument("--races", default="1-12")
    args = ap.parse_args()
    hd = args.date.replace("-", "")
    collect(hd, args.jcd, parse_races(args.races))


if __name__ == "__main__":
    main()
