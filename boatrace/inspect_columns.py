# -*- coding: utf-8 -*-
import os, re, glob

TXT_PATH = "data/b250809.txt"
NUM_LINES = 6

def find_txt():
    if os.path.exists(TXT_PATH):
        return TXT_PATH
    cands = sorted(glob.glob("data/b*.txt"))
    return cands[0] if cands else None

def is_racer_line(line):
    return re.match(r"^[1-6] \d{4}", line) is not None

def show_char(ch):
    if ch == " ":
        return "·"
    if ch == "\u3000":
        return "□"
    return ch

def dump_line(line):
    per_row = 10
    for start in range(0, len(line), per_row):
        chunk = line[start:start + per_row]
        cells = [f"{start + i:>2}:{show_char(c)}" for i, c in enumerate(chunk)]
        print("  " + "  ".join(cells))

def main():
    path = find_txt()
    if not path:
        print("× テキストが見つかりません。data フォルダに b****.txt はありますか？")
        print("  data フォルダの中身:", glob.glob("data/*"))
        return
    print("対象ファイル:", path)
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    racer_lines = [ln for ln in lines if is_racer_line(ln)]
    print(f"選手行の総数: {len(racer_lines)} 行")
    print("凡例:  · = 半角スペース  /  □ = 全角スペース")
    print("=" * 70)
    for n, ln in enumerate(racer_lines[:NUM_LINES], 1):
        print(f"\n--- 選手行 {n} （全 {len(ln)} 文字）---")
        print("  原文:", ln)
        dump_line(ln)

if __name__ == "__main__":
    main()