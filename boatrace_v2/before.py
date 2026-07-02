# -*- coding: utf-8 -*-
"""展示（直前情報）・当日結果の取得 v2。

v1 fetch_before.py で実証済みのパーサを踏襲しつつ、v2 の作法に合わせる:
  - 取得は net.polite_get 経由（同時3本・間隔つき — T7/T8）
  - 保存は data/before/before_YYYYMMDD.json（レースごとに fetched_at 付き）
  - 読込は v2 → v1（../boatrace/data/before/）の順でマージ（段階移行）

展示は締切直前スケジューラ（scheduler.py）が発走前ブースト時に取得し、
結果は発走後に取得して当日画面の的中表示に使う。

使い方:
  python before.py --date 2026-07-02                # 全場の展示+結果を一括取得
  python before.py --date 2026-07-02 --jcd 12 --races 1-12
"""
import argparse
import datetime
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

try:
    from . import config, net
except ImportError:
    import config
    import net

URL_BEFORE = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={r}&jcd={jcd}&hd={hd}"
URL_RESULT = "https://www.boatrace.jp/owpc/pc/race/raceresult?rno={r}&jcd={jcd}&hd={hd}"


# ============================================================
# 展示（beforeinfo）パース — 手掛かりは v1 で 2024〜2026 構造確認済み
# ============================================================

def _boat_blocks(html):
    """is-boatColor{1..6} の出現位置で本文を6ブロックに割る（各枠の最初の出現＝展示行）。"""
    marks = [(m.start(), int(m.group(1)))
             for m in re.finditer(r"is-boatColor([1-6])", html)]
    gaps = [marks[i + 1][0] - marks[i][0] for i in range(len(marks) - 1)]
    row_len = max(gaps) if gaps else 2000     # 最終枠が凡例まで飲み込むのを防ぐ上限
    blocks = {}
    for i, (pos, w) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else min(len(html), pos + row_len)
        if w not in blocks:
            blocks[w] = html[pos:end]
    return blocks


def parse_beforeinfo(html):
    """beforeinfo HTML → {time,tilt,parts,course,st,weather}（枠1..6順）。未掲載は None。"""
    if not html:
        return None
    time_, tilt, parts = [None] * 6, [None] * 6, [None] * 6
    blocks = _boat_blocks(html)
    if not blocks:
        return None
    for w in range(1, 7):
        b = blocks.get(w, "")
        # 展示タイム＝小数2桁（6.78等）。体重/チルトは小数1桁なので区別できる。
        mt = re.search(r"\b([4-7]\.\d{2})\b", b)
        if mt:
            time_[w - 1] = float(mt.group(1))
            mc = re.search(r">\s*(-?[0-3]\.\d)\s*<", b[mt.end():])
            if mc:
                tilt[w - 1] = float(mc.group(1))
        pj = re.findall(r"(ピストン|リング|ギ[ヤア]ケース|キャブレター|電気系|"
                        r"シリンダ[ー]?|プロペラ|ボ[ーア]ト|その他)", b)
        if pj:
            parts[w - 1] = list(dict.fromkeys(pj))

    # スタート展示（進入コース順の 枠番 と ST）
    nums = re.findall(r"table1_boatImage1Number[^>]*>\s*([1-6])\s*<", html)
    sts = re.findall(r"table1_boatImage1Time(?:Inner)?[^>]*>\s*([F]?\.?\-?[0-9]+)\s*<", html)
    course, st = [None] * 6, [None] * 6
    for pos, (wstr, sstr) in enumerate(zip(nums, sts), start=1):
        w = int(wstr)
        course[w - 1] = pos
        st[w - 1] = _parse_st(sstr)

    weather = _parse_weather(html)
    if (not any(x is not None for x in time_)
            and not any(x is not None for x in course)
            and not weather.get("tenki")):
        return None                            # 展示前＝未取得扱い
    return {"time": time_, "tilt": tilt, "parts": parts,
            "course": course, "st": st, "weather": weather}


def _parse_st(s):
    """'.09' -> 0.09 / 'F.01' -> -0.01 / 'L' 等は None。"""
    s = s.strip()
    if not s or s in ("L", "-"):
        return None
    f = s.startswith("F")
    s = s.lstrip("F")
    if not s.lstrip(".-").replace(".", "").isdigit():
        return None
    v = float(s if s.startswith(("0", "-")) else ("0" + s if s.startswith(".") else s))
    return -v if f else v


def _parse_weather(html):
    seg = html
    ms = re.search(r'class="weather1[^"]*"', html)
    if ms:
        seg = html[ms.start():ms.start() + 4000]
    tenki = None
    mt = re.search(r"weather1_bodyUnitLabelTitle[^>]*>\s*(晴|曇り|曇|雨|雪|風|霧)", seg)
    if mt:
        tenki = "曇り" if mt.group(1) == "曇" else mt.group(1)
    wind = _num(re.search(r"([0-9]+(?:\.[0-9]+)?)\s*m", seg))
    wave = _num(re.search(r"([0-9]+)\s*cm", seg))
    temp = _num(re.search(r"([0-9]+(?:\.[0-9]+)?)\s*℃", seg))
    md = re.search(r"is-wind(\d+)", seg)
    return {"tenki": tenki, "winddir": int(md.group(1)) if md else None,
            "wind": wind, "wave": wave, "temp": temp}


def _num(m):
    return float(m.group(1)) if m else None


# ============================================================
# 結果（raceresult）パース
# ============================================================

_ZEN = "０１２３４５６７８９"


def _zen2han(s):
    return "".join(str(_ZEN.index(c)) if c in _ZEN else c for c in s)


def parse_result(html):
    """raceresult HTML → {fin, order, km, po2, po3}。未確定/無ければ None。"""
    if not html:
        return None
    pairs = re.findall(
        r"<td[^>]*>\s*([０-９0-9ＦFＬL失欠]+)\s*</td>\s*<td[^>]*is-boatColor([1-6])",
        html)
    fin = [None] * 6
    seen = set()
    for chaku, wstr in pairs:
        w = int(wstr)
        if w in seen:
            continue
        seen.add(w)
        c = _zen2han(chaku)
        fin[w - 1] = int(c) if c.isdigit() else None
        if len(seen) == 6:
            break
    if not any(f == 1 for f in fin):
        return None
    order = sorted([w for w in range(1, 7) if fin[w - 1]], key=lambda w: fin[w - 1])
    km = ""
    mk = re.search(r"(まくり差し|逃げ|差し|まくり|抜き|恵まれ)", html)
    if mk:
        km = mk.group(1)
    rows = _payout_rows(html)
    po3 = next((a for n, a in rows if len(n) == 3), None)
    po2 = next((a for n, a in rows if len(n) == 2), None)
    return {"fin": fin, "order": order, "km": km, "po2": po2, "po3": po3}


def _payout_rows(html):
    rows = []
    for rm in re.finditer(r"numberSet1_row[^>]*>(.*?)</div>", html, re.S):
        nums = re.findall(r'numberSet1_number is-type\d">\s*(\d)', rm.group(1))
        if not nums:
            continue
        am = re.search(r'is-payout\d">\s*&yen;\s*([0-9,]+)',
                       html[rm.end(): rm.end() + 400])
        rows.append((nums, int(am.group(1).replace(",", "")) if am else None))
    return rows


# ============================================================
# 取得・保存・読込
# ============================================================

def fetch_race(jcd, rno, hd, want_before=True, want_result=True):
    """1レース分の展示+結果を polite 取得。status は result/before/none。"""
    out = {"ex": None, "result": None, "status": "none",
           "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    if want_result:
        out["result"] = parse_result(
            net.polite_get(URL_RESULT.format(r=rno, jcd=f"{jcd:02d}", hd=hd)))
    if want_before:
        out["ex"] = parse_beforeinfo(
            net.polite_get(URL_BEFORE.format(r=rno, jcd=f"{jcd:02d}", hd=hd)))
    out["status"] = "result" if out["result"] else ("before" if out["ex"] else "none")
    return out


def before_path(hd):
    return os.path.join(config.BEFORE_DIR, f"before_{hd}.json")


def load_day(hd):
    """{rid: {ex, result, status, fetched_at?}}。v1 → v2 の順に読み v2 で上書き。"""
    out = {}
    for p in (os.path.join(config.V1_BEFORE_DIR, f"before_{hd}.json"), before_path(hd)):
        try:
            with open(p, encoding="utf-8") as f:
                out.update(json.load(f))
        except (OSError, ValueError):
            continue
    return out


def save_races(hd, recs):
    """recs={rid: rec} を v2 ファイルへマージ保存。"""
    config.ensure_dirs()
    p = before_path(hd)
    cur = {}
    try:
        with open(p, encoding="utf-8") as f:
            cur = json.load(f)
    except (OSError, ValueError):
        pass
    cur.update(recs)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False)
    return len(recs)


def fetch_and_save(hd, rids, want_before=True, want_result=True):
    """複数レースを並列取得してマージ保存。返り値 {rid: rec}（status!=none のみ）。"""
    def one(rid):
        jcd, rno = int(rid[:2]), int(rid[10:12])
        return rid, fetch_race(jcd, rno, hd, want_before, want_result)
    recs = {}
    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENCY) as ex:
        for rid, rec in ex.map(one, rids):
            if rec["status"] != "none":
                recs[rid] = rec
    if recs:
        save_races(hd, recs)
    return recs


# ============================================================
# CLI
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="展示・当日結果の取得（v2）")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--jcd", nargs="*", type=int, default=list(range(1, 25)))
    ap.add_argument("--races", default="1-12")
    args = ap.parse_args()
    hd = args.date.replace("-", "")
    if "-" in args.races:
        a, b = args.races.split("-")
        races = list(range(int(a), int(b) + 1))
    else:
        races = [int(x) for x in args.races.split(",") if x]

    # R1 で開催場を判定してから全レースへ（odds.collect と同じ方針）
    probe = fetch_and_save(hd, [f"{j:02d}{hd}{races[0]:02d}" for j in args.jcd])
    held = sorted({int(rid[:2]) for rid in probe})
    print(f"開催場: {held or 'なし'}")
    rest = [f"{j:02d}{hd}{r:02d}" for j in held for r in races[1:]]
    recs = fetch_and_save(hd, rest)
    print(f"○ 保存: {before_path(hd)}（{len(probe) + len(recs)}レース）")


if __name__ == "__main__":
    main()
