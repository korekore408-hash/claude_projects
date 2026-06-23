# -*- coding: utf-8 -*-
"""
当日予想PDF（携帯でどこでも開ける・私有のまま）
=========================================================================
当日・前日・前々日の3日分を1つのPDFにまとめる（当日が先頭ページ）。
各レース: 本命(◎)・1着確率・2連単本命・3連単本命・前づけ警戒。
前日・前々日は結果（1着枠）と ◎的中(○/×) も併記する。

PDF はOneDrive/メール/どのアプリでもネイティブに開けるので、外出先でも私有の
まま閲覧できる。

使い方:
  py -3 build_today_pdf.py
  py -3 build_today_pdf.py --date 2026-06-16 --days 3 --out today.pdf
"""

import argparse
import csv
import itertools

from fpdf import FPDF
from build_today import venue_stats, _pl_rank, load_payouts

FONT = "C:/Windows/Fonts/msgothic.ttc"
LANE = {1: ((255, 255, 255), 0), 2: ((30, 30, 30), 1), 3: ((226, 59, 59), 1),
        4: ((47, 127, 214), 1), 5: ((242, 192, 37), 0), 6: ((40, 163, 90), 1)}
REL = ["当日", "前日", "前々日", "3日前", "4日前", "5日前"]


def load(path):
    with open(path, encoding="cp932") as f:
        return list(csv.DictReader(f))


def pl_top1(s, kind):
    idx = [i for i in range(6) if s[i] > 0]
    tot = sum(s)
    best, bp = None, -1.0
    for c in itertools.permutations(idx, kind):
        p, rem = 1.0, tot
        for i in c:
            p *= s[i] / rem
            rem -= s[i]
        if p > bp:
            best, bp = tuple(i + 1 for i in c), p
    return best


def wkbox(pdf, x, w, y0):
    """枠番号の色付きボックスを現在行に描画。"""
    (rgb, tcol) = LANE[w]
    pdf.set_fill_color(*rgb)
    pdf.set_draw_color(180, 180, 180)
    pdf.set_xy(x, y0)
    pdf.set_text_color(255 if tcol else 0, 255 if tcol else 0, 255 if tcol else 0)
    pdf.cell(6, 5.5, str(w), border=1, align="C", fill=True)
    pdf.set_text_color(0, 0, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--hist", default="features_player_history.csv")
    ap.add_argument("--date", default=None)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--stats-from", default="2026-01-01",
                    help="場別成績ページの集計開始日（既定2026-01-01）")
    ap.add_argument("--out", default="today.pdf")
    args = ap.parse_args()

    rel = load(args.rel)
    pred = {(r["race_id"], r["枠番"]): r for r in load(args.pred)}
    hist = {(r["race_id"], r["枠番"]): r for r in load(args.hist)}
    all_dates = sorted({r["日付"] for r in rel})
    base = args.date or all_dates[-1]
    keep = [d for d in all_dates if d <= base][-args.days:]
    keep_set = set(keep)

    races = {}
    for r in rel:
        if r["日付"] not in keep_set:
            continue
        rid = r["race_id"]
        pr = pred.get((rid, r["枠番"]), {})
        try:
            pm = round(float(pr.get("p_win")) * 1000)
        except (TypeError, ValueError):
            pm = None
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            fin = None
        rc = races.setdefault(rid, {"d": r["日付"], "c": r["場コード"], "v": r["会場"],
                                    "no": int(r["レース"]),
                                    "mz": int(r.get("field_maezuke_flag", 0) or 0),
                                    "b": {}})
        rc["b"][int(r["枠番"])] = (r["選手名"], pm, fin)

    # 日付 -> 会場 -> [rec]
    by_day = {d: {} for d in keep}
    for rid, rc in races.items():
        if len(rc["b"]) != 6 or any(rc["b"][w][1] is None for w in range(1, 7)):
            continue
        s = [rc["b"][w][1] for w in range(1, 7)]
        fins = [rc["b"][w][2] for w in range(1, 7)]
        # 完走艇だけで 1-2-3着 を決める（F/失格混在でも結果あり扱い）。
        order = sorted([w for w in range(1, 7)
                        if fins[w - 1] is not None and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        done = len(order) >= 1 and fins[order[0] - 1] == 1
        hm = max(range(6), key=lambda i: s[i]) + 1
        hit = None
        if done:
            hit = (hm == order[0]) \
                or (len(order) >= 2 and _pl_rank(s, 2, tuple(order[:2])) <= 5) \
                or (len(order) >= 3 and _pl_rank(s, 3, tuple(order[:3])) <= 10)
        rec = {"no": rc["no"], "mz": rc["mz"], "hm": hm,
               "nm": rc["b"][hm][0], "pw": round(s[hm - 1] / 10),
               "ex": pl_top1(s, 2), "tri": pl_top1(s, 3),
               "done": done, "win": order[0] if done else None, "hit": hit}
        by_day[rc["d"]].setdefault((rc["c"], rc["v"]), []).append(rec)
    for d in by_day:
        for v in by_day[d].values():
            v.sort(key=lambda x: x["no"])

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_font("jp", "", FONT)
    pdf.set_auto_page_break(True, margin=12)

    # 列レイアウト(mm)
    X = {"r": 12, "w": 21, "nm": 29, "pw": 66, "ex": 82, "tri": 104,
         "res": 130, "ok": 150, "mz": 168}

    def col_header(past):
        pdf.set_font("jp", "", 8)
        pdf.set_text_color(120, 120, 120)
        pdf.set_x(X["r"]); pdf.cell(9, 5, "R")
        pdf.set_x(X["nm"]); pdf.cell(36, 5, "本命")
        pdf.set_x(X["pw"]); pdf.cell(16, 5, "1着%")
        pdf.set_x(X["ex"]); pdf.cell(22, 5, "2連単")
        pdf.set_x(X["tri"]); pdf.cell(24, 5, "3連単")
        if past:
            pdf.set_x(X["res"]); pdf.cell(18, 5, "結果")
            pdf.set_x(X["ok"]); pdf.cell(16, 5, "的中")
        pdf.set_x(X["mz"]); pdf.cell(10, 5, "警", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    for di, d in enumerate(reversed(keep)):          # 当日→前日→前々日
        past = any(r["done"] for v in by_day[d].values() for r in v)
        pdf.add_page()
        pdf.set_font("jp", "", 15)
        rel_lab = REL[di] if di < len(REL) else d
        pdf.cell(0, 8, f"{rel_lab}  {d}" + ("（結果あり）" if past else ""),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("jp", "", 8)
        pdf.set_text_color(110, 110, 110)
        pdf.cell(0, 5, "直前情報なしモデル（朝の出走表のみ）/ ◎=本命 / 警=前づけ警戒"
                       + ("（的中=本命1着 / 2連単top5 / 3連単top10 圏内）" if past else ""),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

        for (code, vname), recs in sorted(by_day[d].items()):
            if pdf.get_y() > 250:
                pdf.add_page()
            pdf.ln(1)
            pdf.set_font("jp", "", 11)
            pdf.cell(0, 6, vname, new_x="LMARGIN", new_y="NEXT")
            col_header(past)
            pdf.set_font("jp", "", 9)
            for r in recs:
                if pdf.get_y() > 282:
                    pdf.add_page()
                y0 = pdf.get_y()
                pdf.set_x(X["r"]); pdf.cell(9, 6, f"{r['no']}R")
                wkbox(pdf, X["w"], r["hm"], y0)
                pdf.set_xy(X["nm"], y0); pdf.cell(36, 6, r["nm"][:7])
                pdf.set_xy(X["pw"], y0); pdf.cell(16, 6, f"{r['pw']}%")
                pdf.set_xy(X["ex"], y0); pdf.cell(22, 6, "-".join(map(str, r["ex"])) if r["ex"] else "")
                pdf.set_xy(X["tri"], y0); pdf.cell(24, 6, "-".join(map(str, r["tri"])) if r["tri"] else "")
                if r["done"]:
                    wkbox(pdf, X["res"], r["win"], y0)
                    hit = r["hit"]
                    pdf.set_xy(X["ok"], y0)
                    pdf.set_text_color(*(0, 150, 100) if hit else (200, 70, 70))
                    pdf.cell(16, 6, "的中" if hit else "×")
                    pdf.set_text_color(0, 0, 0)
                if r["mz"]:
                    pdf.set_text_color(200, 120, 0)
                    pdf.set_xy(X["mz"], y0); pdf.cell(10, 6, "警")
                    pdf.set_text_color(0, 0, 0)
                pdf.set_xy(pdf.l_margin, y0 + 6)

    # ── 場別成績ページ（収集データ全体） ──────────────────────────
    payout_all = load_payouts(sorted({r["日付"] for r in rel
                                      if r["日付"] >= args.stats_from}))
    vs = venue_stats(rel, pred, hist, payout_all, args.stats_from)
    if vs["n"]:
        pdf.add_page()
        pdf.set_font("jp", "", 15)
        pdf.cell(0, 8, f"場別成績  {vs['from']}〜{vs['to']}（{vs['n']}レース）",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("jp", "", 8); pdf.set_text_color(110, 110, 110)
        pdf.cell(0, 5, "本命=1着確率最大の枠 / ◎t3=上位1・3通り以内 / ≤5・≤20=変動買い目"
                       "（除外適用）以内に決着が入った割合 / 4月までは学習期間込みで的中やや高め",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0); pdf.ln(2)
        SX = [("v", 12, 22, "L"), ("n", 34, 13, "R"), ("win", 49, 15, "R"),
              ("e1", 66, 15, "R"), ("e3", 83, 15, "R"), ("e5", 100, 15, "R"),
              ("t1", 121, 15, "R"), ("t3", 138, 15, "R"), ("t10", 155, 17, "R")]
        HEAD = {"v": "会場", "n": "R数", "win": "本命", "e1": "2単◎", "e3": "t3",
                "e5": "≤5", "t1": "3単◎", "t3": "t3", "t10": "≤20"}
        BLUE, AMB = (28, 99, 176), (168, 115, 10)

        # venue_stats の行は [v,n,win,e1,e3,e5,ret2,t1,t3,t10,ret3]（11列）。
        # PDFは的中率9列のみ表示するので SX位置→行index を対応付ける（回収率は省略）。
        COLIDX = [0, 1, 2, 3, 4, 5, 7, 8, 9]

        def srow(a, y, bold=False, head=False):
            for i, (key, x, w, al) in enumerate(SX):
                v = HEAD[key] if head else (a[COLIDX[i]] if i < 2 else f"{a[COLIDX[i]]}%")
                if head:
                    pdf.set_text_color(120, 120, 120)
                elif key.startswith("e"):
                    pdf.set_text_color(*BLUE)
                elif key.startswith("t") and key != "n":
                    pdf.set_text_color(*AMB)
                else:
                    pdf.set_text_color(0, 0, 0)
                pdf.set_xy(x, y)
                pdf.cell(w, 6, str(v), align=al)
            pdf.set_text_color(0, 0, 0)

        y = pdf.get_y()
        srow(None, y, head=True)
        pdf.line(12, y + 6, 172, y + 6)
        y += 7
        for a in vs["rows"]:
            srow(a, y)
            y += 6
        pdf.line(12, y, 172, y)
        pdf.set_font("jp", "", 10)
        srow(vs["all"], y + 1)

    pdf.output(args.out)
    n = sum(len(v) for d in by_day for v in by_day[d].values())
    print(f"○ 当日予想PDF: {args.out}")
    print(f"  対象日 {list(reversed(keep))} / レース {n}")


if __name__ == "__main__":
    main()
