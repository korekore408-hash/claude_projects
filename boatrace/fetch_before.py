# -*- coding: utf-8 -*-
"""
展示（直前情報）・結果 取得（当日の「更新」ボタン用）
=========================================================================
当日は K-file（前日確定）がまだ無いので、boatrace 公式サイトの
  ・直前情報（展示）  beforeinfo  … 展示タイム / チルト / 部品交換 /
                                    スタート展示(進入コース・ST) / 天候風波
  ・レース結果        raceresult  … 着順 / 決まり手 / 2連単・3連単の配当
を HTML から取得する。fetch_odds.py と同じ作法（requests + 正規表現）。

取得元（2024〜2026 で構造確認済み）:
  展示: https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={R}&jcd={場:02d}&hd={YYYYMMDD}
  結果: https://www.boatrace.jp/owpc/pc/race/raceresult?rno={R}&jcd={場:02d}&hd={YYYYMMDD}

パース方針（生HTMLが手元で見られない環境向けに、崩れにくい手掛かりを使う）:
  ・枠の特定は class="is-boatColor{1..6}"（全ページ共通で安定）。
  ・展示タイムは「小数2桁(6.78 等)」が他項目(体重52.0/チルト-0.5=小数1桁)と
    判別できる唯一の値なので値パターンで拾い、チルトは展示タイム直後の小数1桁。
  ・スタート展示は class="table1_boatImage1Number / ...Time"（進入順）。
  ・天候は class="weather1_bodyUnitLabelData / ...Title / is-wind{n}"。
  ・配当は「実際の着順から作った組番」をHTML内で探し、直後の金額を取る
    （組番が分かっているので最も確実）。

race_id は予測CSVと同形式: f"{jcd:02d}{YYYYMMDD}{rno:02d}"。

使い方:
  py -3.13 fetch_before.py --debug --jcd 1 --rno 1 --date 2024-06-01
      # 1レースの展示+結果をパースして中身を表示（選択子の検証用）
  py -3.13 fetch_before.py --date 2026-06-20 --jcd 1 2 --races 1-12
      # CSV保存（serve 経由でなくバッチ取得したい場合）
"""

import re
import csv
import os
import json
import time
import argparse
import datetime

import requests

WAIT_SECONDS = 0.6
HEADERS = {"User-Agent": "Mozilla/5.0 (boatrace-study-script)"}

URL_BEFORE = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={r}&jcd={jcd}&hd={hd}"
URL_RESULT = "https://www.boatrace.jp/owpc/pc/race/raceresult?rno={r}&jcd={jcd}&hd={hd}"

KIMARITE = ("逃げ", "差し", "まくり差し", "まくり", "抜き", "恵まれ")


# ============================================================
# 取得（HTML 文字列を返す。失敗時 None）
# ============================================================

def _get(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException:
        return None
    if res.status_code != 200:
        return None
    return res.content.decode("utf-8", "replace")


# ============================================================
# 展示（直前情報）
# ============================================================

def _boat_blocks(html):
    """is-boatColor{1..6} の出現位置で本文を 6 ブロックに割る。
    返り値 {枠: ブロックHTML}（最初に出てくる艇順テーブル＝展示テーブルを優先）。"""
    marks = [(m.start(), int(m.group(1)))
             for m in re.finditer(r"is-boatColor([1-6])", html)]
    # 展示行はほぼ等間隔。最終枠のブロックはEOFまで伸びてページ末尾の凡例
    # (label4 の部品名一覧)を飲み込み部品交換を誤検出するので、典型的な行長で上限を設ける。
    gaps = [marks[i + 1][0] - marks[i][0] for i in range(len(marks) - 1)]
    row_len = max(gaps) if gaps else 2000
    blocks = {}
    for i, (pos, w) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else min(len(html), pos + row_len)
        if w not in blocks:                      # 各枠の最初の出現＝展示行
            blocks[w] = html[pos:end]
    return blocks


def parse_beforeinfo(html):
    """beforeinfo HTML → 展示情報 dict（取れない項目は None / 空）。
    {time:[6.78,..], tilt:[-0.5,..], parts:[[..],..],   # 枠1..6順
     course:[進入コース(枠ごと)], st:[展示ST(枠ごと)],
     weather:{tenki,winddir,wind,wave,temp}}"""
    if not html:
        return None
    time_, tilt, parts = [None] * 6, [None] * 6, [None] * 6
    blocks = _boat_blocks(html)
    if not blocks:                               # 出走表テーブルが無い＝データ無しページ
        return None
    for w in range(1, 7):
        b = blocks.get(w, "")
        # 展示タイム＝小数2桁（6.78 等）。体重/調整重量/チルトは小数1桁なので区別可。
        mt = re.search(r"\b([4-7]\.\d{2})\b", b)
        if mt:
            time_[w - 1] = float(mt.group(1))
            # チルトは展示タイム直後に現れる小数1桁（-0.5〜3.0）。
            mc = re.search(r">\s*(-?[0-3]\.\d)\s*<", b[mt.end():])
            if mc:
                tilt[w - 1] = float(mc.group(1))
        # 部品交換（ピストン/リング/ギアケース/キャブレター/電気系/シリンダ 等）
        pj = re.findall(r"(ピストン|リング|ギ[ヤア]ケース|キャブレター|電気系|"
                        r"シリンダ[ー]?|プロペラ|ボ[ーア]ト|その他)", b)
        if pj:
            parts[w - 1] = list(dict.fromkeys(pj))     # 重複除去・順序維持

    # スタート展示（進入コース順に 枠番 と ST）。Number=枠, Time=ST(.09 / F.01)。
    nums = re.findall(r"table1_boatImage1Number[^>]*>\s*([1-6])\s*<", html)
    sts = re.findall(r"table1_boatImage1Time(?:Inner)?[^>]*>\s*([F]?\.?\-?[0-9]+)\s*<", html)
    course = [None] * 6
    st = [None] * 6
    for pos, (wstr, sstr) in enumerate(zip(nums, sts), start=1):
        w = int(wstr)
        course[w - 1] = pos                        # 進入コース（1=イン）
        st[w - 1] = _parse_st(sstr)

    weather = _parse_weather(html)
    # 直前情報がまだ何も無い（展示前で展示タイム/進入/天候すべて空）なら None＝未取得扱い。
    if (not any(x is not None for x in time_)
            and not any(x is not None for x in course)
            and not weather.get("tenki")):
        return None
    return {"time": time_, "tilt": tilt, "parts": parts,
            "course": course, "st": st, "weather": weather}


def _parse_st(s):
    """'.09' -> 0.09 / 'F.01' -> -0.01 / 'L' などは None。"""
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
    """天候/風向/風速/波高/気温。weather1_* クラスから抽出。"""
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
    winddir = int(md.group(1)) if md else None     # 1..16（風向の番号）
    return {"tenki": tenki, "winddir": winddir, "wind": wind,
            "wave": wave, "temp": temp}


def _num(m):
    return float(m.group(1)) if m else None


# ============================================================
# 結果
# ============================================================

# 着順は全角数字（１２３…）で描画される。半角化用。
_ZEN = "０１２３４５６７８９"


def _zen2han(s):
    return "".join(str(_ZEN.index(c)) if c in _ZEN else c for c in s)


def parse_result(html):
    """raceresult HTML → {fin:[着順 枠1..6], order:[1着枠,2着枠,3着枠..],
    km:決まり手, po2:2連単配当, po3:3連単配当}。未確定/無ければ None。

    実HTML構造（2026 公式 raceresult）:
      <td class="is-fs14">１</td>                      ← 着順は全角数字 / Ｆ/Ｌ/失/欠
      <td class="is-fs14 is-fBold is-boatColor4">4</td> ← is-boatColor は td自身・中身が枠番
    配当:
      <span class="numberSet1_number is-type4">4</span>…  ← 組番は色付きボックス（文字列でない）
      <td><span class="is-payout1">&yen;2,970</span></td>
    """
    if not html:
        return None
    # 着順td（全角/半角数字・Ｆ/Ｌ/失/欠）→ 直後の is-boatColor td（中身＝枠番）
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
        fin[w - 1] = int(c) if c.isdigit() else None   # 非完走(F/L/失/欠)は None
        if len(seen) == 6:
            break
    if not any(f == 1 for f in fin):               # 1着が無い＝未確定
        return None
    order = sorted([w for w in range(1, 7) if fin[w - 1]],
                   key=lambda w: fin[w - 1])
    # 決まり手（まくり差し を まくり/差し より先に当てる）
    km = ""
    mk = re.search(r"(まくり差し|逃げ|差し|まくり|抜き|恵まれ)", html)
    if mk:
        km = mk.group(1)
    # 配当: numberSet1_row の数字ボックス列 → 直後の is-payout 金額。
    rows = _payout_rows(html)
    po3 = next((a for n, a in rows if len(n) == 3), None)
    po2 = next((a for n, a in rows if len(n) == 2), None)
    return {"fin": fin, "order": order, "km": km, "po2": po2, "po3": po3}


def _payout_rows(html):
    """払戻テーブルの各行 (組番digits, 金額) を文書順に返す。
    3連単は先頭の3桁行・2連単は先頭の2桁行（いずれも連複より前に出る）。"""
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
# 1レース取得（serve_before / バッチ共用）
# ============================================================

def fetch_race(jcd, rno, hd, want_before=True, want_result=True):
    """1レース分の展示+結果を取得して dict を返す。status は result/before/none。"""
    out = {"ex": None, "result": None, "status": "none"}
    if want_result:
        out["result"] = parse_result(_get(URL_RESULT.format(r=rno, jcd=f"{jcd:02d}", hd=hd)))
        time.sleep(WAIT_SECONDS)
    if want_before:                                # 結果が出ていても展示は「その日調べた情報」として取得
        out["ex"] = parse_beforeinfo(_get(URL_BEFORE.format(r=rno, jcd=f"{jcd:02d}", hd=hd)))
        time.sleep(WAIT_SECONDS)
    out["status"] = "result" if out["result"] else ("before" if out["ex"] else "none")
    return out


# ============================================================
# debug / CLI
# ============================================================

def _debug(jcd, rno, hd):
    print(f"== beforeinfo jcd{jcd:02d} {rno}R {hd} ==")
    print(json.dumps(parse_beforeinfo(_get(URL_BEFORE.format(r=rno, jcd=f"{jcd:02d}", hd=hd))),
                     ensure_ascii=False, indent=2))
    print(f"== raceresult jcd{jcd:02d} {rno}R {hd} ==")
    print(json.dumps(parse_result(_get(URL_RESULT.format(r=rno, jcd=f"{jcd:02d}", hd=hd))),
                     ensure_ascii=False, indent=2))


def parse_races(spec):
    if "-" in spec:
        a, b = spec.split("-"); return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",") if x]


def main():
    ap = argparse.ArgumentParser(description="boatrace 公式から 展示・結果 を取得")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--jcd", nargs="*", type=int, default=list(range(1, 25)))
    ap.add_argument("--races", default="1-12")
    ap.add_argument("--rno", type=int, help="--debug 用の単一レース")
    ap.add_argument("--debug", action="store_true", help="1レースをパースして表示")
    args = ap.parse_args()
    hd = datetime.datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y%m%d")

    if args.debug:
        _debug(args.jcd[0], args.rno or 1, hd)
        return

    rows = []
    for jcd in args.jcd:
        got = False
        for rno in parse_races(args.races):
            d = fetch_race(jcd, rno, hd)
            if d["status"] == "none":
                if rno == 1 and not got:
                    print(f"  jcd{jcd:02d}: 取得なし → skip"); break
                continue
            got = True
            rows.append({"race_id": f"{jcd:02d}{hd}{rno:02d}", **d})
            print(f"  jcd{jcd:02d} {rno:2d}R: {d['status']}")
    os.makedirs("data/before", exist_ok=True)
    out = os.path.join("data", "before", f"before_{hd}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({r["race_id"]: {"ex": r["ex"], "result": r["result"],
                                  "status": r["status"]} for r in rows},
                  f, ensure_ascii=False)
    print(f"○ 保存: {out}（{len(rows)}レース）")


if __name__ == "__main__":
    main()
