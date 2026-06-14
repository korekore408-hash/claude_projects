# -*- coding: utf-8 -*-
"""
番組表テキスト → CSV（表）に変換するスクリプト
-------------------------------------------------------------------------
inspect_columns.py で確定した「桁位置」を使って、各選手行を
艇番・登番・選手名・級別・勝率… の列に切り出し、CSVに保存する。

使い方:
  data フォルダがある場所（C:\\Users\\kore4）に置いて
  py -3.13 to_csv.py
"""

import os
import re
import csv
import glob
import sys

TXT_PATH = "data/b250809.txt"   # 変換元（番組表テキスト）
CSV_PATH = "data/b250809.csv"   # 出力先（表）


def find_txt(arg=None):
    if arg and os.path.exists(arg):
        return arg
    if os.path.exists(TXT_PATH):
        return TXT_PATH
    cands = sorted(glob.glob("data/b*.txt"))
    return cands[0] if cands else None


def is_racer_line(line: str) -> bool:
    """「艇番(1〜6) + 半角スペース + 登番4桁」で始まる行を選手行とみなす。"""
    return re.match(r"^[1-6] \d{4}", line) is not None


def parse_racer_line(line: str) -> dict:
    """確定した桁位置で1行を切り出して辞書にする。
    数字の項目は前後の空白を取り、空なら None にする。
    """
    def cut(start, end):
        return line[start:end].strip()

    return {
        "艇番":        cut(0, 1),
        "登番":        cut(2, 6),
        "選手名":      cut(6, 10).replace("\u3000", ""),  # 名前の全角スペースは除去
        "年齢":        cut(10, 12),
        "支部":        cut(12, 14),
        "体重":        cut(14, 16),
        "級別":        cut(16, 18),
        "全国勝率":    cut(19, 23),
        "全国2率":     cut(24, 29),
        "当地勝率":    cut(30, 34),
        "当地2率":     cut(35, 40),
        "モーター番号": cut(41, 43),
        "モーター2率": cut(44, 49),
        "ボート番号":  cut(50, 52),
        "ボート2率":   cut(53, 58),
        "今節成績ほか": cut(59, 73),  # 生のまま（後で整える）
    }


# 会場・レース番号も一緒に記録できると後で便利なので、
# 直近で見えている「会場名」と「レース番号」を覚えながら処理する。
def detect_place(line: str):
    """『ボートレース○○』を含む行から会場名を拾う。無ければ None。
    会場名の中に全角スペースが入る場合（例: 大\u3000村）にも対応する。
    """
    if "ボートレース" not in line:
        return None
    after = line.split("ボートレース", 1)[1]
    after = re.sub(r"\s", "", after)          # 半角・全角スペースをすべて除去
    m = re.match(r"^([^\d０-９]+)", after)     # 先頭の「数字でない部分」＝会場名
    return m.group(1) if m else None


def detect_race_no(line: str):
    """『○Ｒ』を含む行からレース番号を拾う（全角数字対応）。無ければ None。"""
    # 全角数字を半角に直してから探す
    z2h = str.maketrans("０１２３４５６７８９", "0123456789")
    s = line.translate(z2h)
    m = re.search(r"(\d{1,2})\s*[RＲ]", s)
    if m:
        return m.group(1)
    return None


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    path = find_txt(arg)
    if not path:
        print("× テキストが見つかりません。data フォルダに b****.txt はありますか？")
        print("  data フォルダの中身:", glob.glob("data/*"))
        return

    # 出力先は入力ファイル名（stem）から導く。
    stem = os.path.splitext(os.path.basename(path))[0]
    csv_path = f"data/{stem}.csv"

    print("変換元:", path)
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    rows = []
    place = None
    race_no = None

    for line in lines:
        # 会場名・レース番号の行なら、覚えておく
        p = detect_place(line)
        if p:
            place = p
        r = detect_race_no(line)
        if r:
            race_no = r

        # 選手行なら切り出してリストに追加
        if is_racer_line(line):
            row = {"会場": place, "レース": race_no}
            row.update(parse_racer_line(line))
            rows.append(row)

    if not rows:
        print("× 選手行が見つかりませんでした。")
        return

    # CSVに書き出し（Excelで開けるよう、日本語Windows向けに cp932 で保存）
    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", encoding="cp932", newline="", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"○ CSVを保存しました: {csv_path}")
    print(f"  選手行: {len(rows)} 行 / 列: {len(fieldnames)} 列")
    print()
    print("--- 先頭5行のプレビュー ---")
    print(",".join(fieldnames))
    for row in rows[:5]:
        print(",".join(str(row[k]) for k in fieldnames))


if __name__ == "__main__":
    main()
