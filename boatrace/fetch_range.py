# -*- coding: utf-8 -*-
"""
期間まとめて：番組表・競走成績を「開始日〜終了日」でDL→解凍→CSV化
-------------------------------------------------------------------------
できること:
  - 指定期間の「番組表(B)」と「競走成績(K)」を1日ずつ取得
  - LZH解凍 → 固定長テキストを表(CSV)に変換
  - 全期間を縦に連結した「まとめCSV」を作成
  - 途中で止まっても、もう一度実行すれば続きから再開（DL済みはスキップ）
  - サーバーに配慮して1日ごとに少し待つ

使い方:
  boatrace フォルダの中に置いて
  py -3.13 fetch_range.py
  （下の「設定」の開始日・終了日・種類を変えるだけ）
"""

import os
import csv
import glob
import time
import datetime
import requests
import lhafile


# ============================================================
# 設定（ここだけ書き換える）
# ============================================================

START_DATE = "2025-08-01"   # 開始日（この日を含む）
END_DATE   = "2025-08-31"   # 終了日（この日を含む）

# 取得する種類: "program"=番組表 / "results"=競走成績 / "both"=両方
WHICH = "both"

WAIT_SECONDS = 1.5          # 1日ごとの待ち時間（秒）。サーバーへの配慮
SAVE_DIR = "data"           # 保存先フォルダ


# ============================================================
# 以下はロジック
# ============================================================

DATA_CONFIG = {
    "program": {"url_dir": "B", "prefix": "b", "label": "番組表"},
    "results": {"url_dir": "K", "prefix": "k", "label": "競走成績"},
}


def daterange(start: datetime.date, end: datetime.date):
    """start から end まで1日ずつ日付を返す。"""
    days = (end - start).days
    for i in range(days + 1):
        yield start + datetime.timedelta(days=i)


def build_url(date: datetime.date, data_type: str) -> str:
    conf = DATA_CONFIG[data_type]
    yyyymm = date.strftime("%Y%m")
    yymmdd = date.strftime("%y%m%d")
    return f"http://www1.mbrace.or.jp/od2/{conf['url_dir']}/{yyyymm}/{conf['prefix']}{yymmdd}.lzh"


def download_lzh(url: str, save_path: str) -> bool:
    """LZHをDLして保存。成功=True、データ無し等=False。
    すでに保存済みならDLせずTrue（再開用）。"""
    if os.path.exists(save_path) and os.path.getsize(save_path) > 100:
        print(f"    （既にDL済み: {os.path.basename(save_path)}）")
        return True
    headers = {"User-Agent": "Mozilla/5.0 (boatrace-study-script)"}
    try:
        res = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        print(f"    × 通信エラー: {e}")
        return False
    if res.status_code != 200 or len(res.content) < 100:
        print(f"    × データなし (HTTP {res.status_code})")
        return False
    with open(save_path, "wb") as f:
        f.write(res.content)
    print(f"    ○ DL: {os.path.basename(save_path)} ({len(res.content):,} バイト)")
    return True


def extract_text(lzh_path: str) -> str:
    archive = lhafile.Lhafile(lzh_path)
    names = archive.namelist()
    if not names:
        raise ValueError("アーカイブが空")
    return archive.read(names[0]).decode("cp932", errors="replace")


# ---- 番組表(B)を1行ずつ切り出すための位置（確定済み） ----
def parse_program_line(line: str) -> dict:
    def cut(a, b):
        return line[a:b].strip()
    return {
        "艇番": cut(0, 1), "登番": cut(2, 6),
        "選手名": cut(6, 10).replace("\u3000", ""),
        "年齢": cut(10, 12), "支部": cut(12, 14), "体重": cut(14, 16),
        "級別": cut(16, 18),
        "全国勝率": cut(19, 23), "全国2率": cut(24, 29),
        "当地勝率": cut(30, 34), "当地2率": cut(35, 40),
        "モーター番号": cut(41, 43), "モーター2率": cut(44, 49),
        "ボート番号": cut(50, 52), "ボート2率": cut(53, 58),
        "今節成績ほか": cut(59, 73),
    }


import re
def is_racer_line(line: str) -> bool:
    return re.match(r"^[1-6] \d{4}", line) is not None

def detect_place(line: str):
    if "ボートレース" not in line:
        return None
    after = re.sub(r"\s", "", line.split("ボートレース", 1)[1])
    m = re.match(r"^([^\d０-９]+)", after)
    return m.group(1) if m else None

def detect_race_no(line: str):
    z2h = str.maketrans("０１２３４５６７８９", "0123456789")
    m = re.search(r"(\d{1,2})\s*[RＲ]", line.translate(z2h))
    return m.group(1) if m else None


def program_text_to_rows(text: str, date_str: str) -> list:
    rows = []
    place = race_no = None
    for line in text.splitlines():
        p = detect_place(line)
        if p:
            place = p
        r = detect_race_no(line)
        if r:
            race_no = r
        if is_racer_line(line):
            row = {"日付": date_str, "会場": place, "レース": race_no}
            row.update(parse_program_line(line))
            rows.append(row)
    return rows


def process_one_day(date: datetime.date, data_type: str) -> list:
    """1日分: DL→解凍→（番組表なら）行リスト化。CSV(1日分)も保存。"""
    conf = DATA_CONFIG[data_type]
    url = build_url(date, data_type)
    lzh_path = os.path.join(SAVE_DIR, os.path.basename(url))
    if not download_lzh(url, lzh_path):
        return []
    text = extract_text(lzh_path)
    # 解凍テキストも保存（確認用）
    txt_path = os.path.splitext(lzh_path)[0] + ".txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)

    if data_type == "program":
        return program_text_to_rows(text, date.strftime("%Y-%m-%d"))
    # 競走成績(K)のCSV化は構造が別なので、まずはテキスト保存のみ（次段で対応）
    return []


def save_csv(rows: list, path: str):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", encoding="cp932", newline="", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    start = datetime.datetime.strptime(START_DATE, "%Y-%m-%d").date()
    end = datetime.datetime.strptime(END_DATE, "%Y-%m-%d").date()
    os.makedirs(SAVE_DIR, exist_ok=True)

    types = ["program", "results"] if WHICH == "both" else [WHICH]
    print(f"期間: {start} 〜 {end} （{(end-start).days+1} 日間） / 対象: {types}")
    print("=" * 60)

    all_program_rows = []
    for date in daterange(start, end):
        print(f"[{date}]")
        for data_type in types:
            rows = process_one_day(date, data_type)
            if data_type == "program":
                all_program_rows.extend(rows)
        time.sleep(WAIT_SECONDS)  # サーバーへの配慮

    # 番組表のまとめCSV
    if all_program_rows:
        out = os.path.join(SAVE_DIR, "番組表_まとめ.csv")
        save_csv(all_program_rows, out)
        print("=" * 60)
        print(f"○ 番組表まとめCSV: {out}")
        print(f"  合計 {len(all_program_rows):,} 行")
    print("完了。")


if __name__ == "__main__":
    main()
