# -*- coding: utf-8 -*-
"""
競走成績テキスト → CSV に変換するスクリプト
-------------------------------------------------------------------------
k*.txt（固定長テキスト, 複数会場分が連結されている）から以下を抽出して CSV に保存する。
  選手行：着順・艇番・登番・選手名・モーター番号・ボート番号
          展示タイム・進入コース・スタートタイミング・レースタイム
  払戻金：各会場ヘッダ内の払戻金サマリから 3連単・3連複・2連単・2連複

使い方:
  py -3 k_to_csv.py                   # data/k*.txt の最初のファイルを処理
  py -3 k_to_csv.py data/k250809.txt  # ファイルを指定
"""

import os
import re
import csv
import glob
import sys

DEFAULT_TXT = "data/k250801.txt"

# ── 正規表現 ──────────────────────────────────────────────────────────────────

# 選手結果行: "  01  1 3617 竹　田　　広　樹 70   16  7.00   1    0.18     1.51.8"
# 名前は全角文字(U+3000 含む)なので、半角スペース [ \t] だけを区切りに使う
RACER_RE = re.compile(
    r'^\s{2}(\d{2})\s{2}([1-6])\s(\d{4})\s'   # 着順, 艇番, 登番
    r'(.+?)'                                     # 選手名 (非貪欲)
    r'[ \t]+(\d{1,3})[ \t]+(\d{1,3})'           # モーター番号, ボート番号
    r'[ \t]+(\d\.\d{2})'                         # 展示タイム
    r'[ \t]+([1-6])'                             # 進入コース
    r'[ \t]+(\d\.\d{2})'                         # スタートタイミング
    r'[ \t]+(.+?)[ \t]*$'                        # レースタイム (DNF は ". ." など)
)

# 非完走行（着順が数字でない）: フライング F / 出遅れ L / 失格系 S0-9 / 欠場系 K0-9
#   "  F   1 4677 片　橋　　幸　貴 15   52 ..."  /  "  K1  1 5213 湯　淺..."
# 着順コードと艇番・登番・選手名だけを拾う（flying_rate 等の母数・F回数用）。
NONFIN_RE = re.compile(
    r'^\s{2}(F|L|S\d|K\d)\s+([1-6])\s(\d{4})\s'   # 着順コード, 艇番, 登番
    r'(.+?)[ \t]+\d'                               # 選手名（後続の数字直前まで）
)

# レースヘッダ行: "   1R       予選Ａ組..." (先頭スペース1〜5個で払戻金サマリ行と区別)
RACE_HDR_RE = re.compile(r'^\s{1,5}(\d{1,2})R\s+\D')

# 決まり手: 列見出し行 "  着 艇 登番 …ﾚｰｽﾀｲﾑ 逃げ" の末尾に1着艇の決まり手が入る。
KIMARITE_RE = re.compile(r'ﾚｰｽﾀｲﾑ\s+(\S+)\s*$')

# 気象: レースヘッダ行 "  1R  予選…  H1800m  晴　  風  北西　 1m  波　  1cm" 末尾。
#   天候=晴/曇り/雨/雪、風向=9方位(北/南/東/西/北東/…/無風)、風速=Nm、波高=Ncm。
#   生値はレース内で全艇共通＝条件付きロジットで打ち消されるため、特徴量側で
#   枠との交互作用にして使う。ここでは raw 値だけ CSV に残す。全件100%パース確認済。
WEATHER_RE = re.compile(
    r'(\d+)m\s+(\S+?)\s+風\s+(\S+?)[　\s]+(\d+)m\s+波[　\s]+(\d+)cm')


def parse_weather(line):
    """レースヘッダ行から気象 dict を返す。該当しなければ空 dict。"""
    m = WEATHER_RE.search(line)
    if not m:
        return {}
    return {
        '距離':   int(m.group(1)),
        '天候':   m.group(2),
        '風向':   m.group(3),
        '風速':   int(m.group(4)),
        '波高':   int(m.group(5)),
    }

# 払戻金サマリ行: "           1R  1-6-2   37940    1-2-6    6330    1-6    7930    1-6    3810"
PAYOUT_RE = re.compile(
    r'^\s+(\d{1,2})R[ \t]+'
    r'([\d-]+)[ \t]+(\d+)[ \t]+'   # 3連単: 組合 / 配当
    r'([\d-]+)[ \t]+(\d+)[ \t]+'   # 3連複: 組合 / 配当
    r'([\d-]+)[ \t]+(\d+)[ \t]+'   # 2連単: 組合 / 配当
    r'([\d-]+)[ \t]+(\d+)'         # 2連複: 組合 / 配当
)

# 会場セクション区切り行: "大　村［成績］      8/ 1  ..."
# ［ = U+FF3B (FULLWIDTH LEFT SQUARE BRACKET), ］ = U+FF3D
SECTION_RE = re.compile(r'^(.+?)［成績］')

DATE_RE = re.compile(r'(\d{4})/\s*(\d{1,2})/\s*(\d{1,2})')


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def find_txt(arg=None):
    if arg and os.path.exists(arg):
        return arg
    if os.path.exists(DEFAULT_TXT):
        return DEFAULT_TXT
    cands = sorted(glob.glob("data/k*.txt"))
    return cands[0] if cands else None


def open_txt(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    except UnicodeDecodeError:
        with open(path, encoding="cp932") as f:
            return f.read().splitlines()


def clean_name(s):
    return s.replace('　', '').strip()


def clean_racetime(s):
    s = s.strip()
    return re.sub(r'[ \t]+', ' ', s)


# ── メイン ────────────────────────────────────────────────────────────────────

def main():
    path = find_txt(sys.argv[1] if len(sys.argv) > 1 else None)
    if not path:
        print("× テキストが見つかりません。data フォルダに k*.txt はありますか？")
        return

    stem = os.path.splitext(os.path.basename(path))[0]
    csv_path = f"data/{stem}.csv"

    print("変換元:", path)
    lines = open_txt(path)

    venue = None
    date = None
    current_race = None
    payouts = {}   # race_no(int) → dict (会場ごとにリセット)
    kimarite = {}  # race_no(int) → 決まり手（会場ごとにリセット）
    weather = {}   # race_no(int) → 気象 dict（会場ごとにリセット）
    rows = []

    for line in lines:
        # ── 会場セクション区切り（新会場の開始） ──────────────────────────
        m = SECTION_RE.match(line)
        if m:
            venue = clean_name(m.group(1))
            date = None         # 日付は次の行で再取得
            current_race = None
            payouts = {}        # 払戻金は会場ごとにリセット
            kimarite = {}       # 決まり手も会場ごとにリセット
            weather = {}        # 気象も会場ごとにリセット
            continue

        # ── 日付（yyyy/ m/ d 形式） ────────────────────────────────────────
        if date is None:
            m = DATE_RE.search(line)
            if m:
                date = f"{m.group(1)}/{int(m.group(2))}/{int(m.group(3))}"

        # ── 払戻金サマリ行 ────────────────────────────────────────────────
        m = PAYOUT_RE.match(line)
        if m:
            rn = int(m.group(1))
            payouts[rn] = {
                '3連単_組合': m.group(2), '3連単_配当': int(m.group(3)),
                '3連複_組合': m.group(4), '3連複_配当': int(m.group(5)),
                '2連単_組合': m.group(6), '2連単_配当': int(m.group(7)),
                '2連複_組合': m.group(8), '2連複_配当': int(m.group(9)),
            }
            continue

        # ── レースヘッダ行（気象もこの行に入る） ──────────────────────────
        m = RACE_HDR_RE.match(line)
        if m:
            current_race = int(m.group(1))
            wx = parse_weather(line)
            if wx:
                weather[current_race] = wx
            continue

        # ── 列見出し行から決まり手を取得（"…ﾚｰｽﾀｲﾑ 逃げ"） ──────────────────
        if current_race is not None and 'ﾚｰｽﾀｲﾑ' in line:
            mk = KIMARITE_RE.search(line)
            if mk:
                kimarite[current_race] = mk.group(1)
            continue

        # ── 選手結果行 ────────────────────────────────────────────────────
        if current_race is not None:
            m = RACER_RE.match(line)
            if m:
                p = payouts.get(current_race, {})
                rows.append({
                    '会場':               venue,
                    '日付':               date,
                    'レース':             current_race,
                    '着順':               int(m.group(1)),
                    '艇番':               int(m.group(2)),
                    '登番':               m.group(3),
                    '選手名':             clean_name(m.group(4)),
                    'status':             'finish',
                    'モーター番号':       int(m.group(5)),
                    'ボート番号':         int(m.group(6)),
                    '展示タイム':         m.group(7),
                    '進入コース':         int(m.group(8)),
                    'スタートタイミング': m.group(9),
                    'レースタイム':       clean_racetime(m.group(10)),
                    '決まり手':           kimarite.get(current_race, ''),
                    **weather.get(current_race, {}),
                    **p,
                })
                continue

            # 非完走行（F/L/失格/欠場）。着順・ST は付かないので status のみ記録する。
            m = NONFIN_RE.match(line)
            if m:
                rows.append({
                    '会場':   venue,
                    '日付':   date,
                    'レース': current_race,
                    '着順':   '',
                    '艇番':   int(m.group(2)),
                    '登番':   m.group(3),
                    '選手名': clean_name(m.group(4)),
                    'status': m.group(1).strip(),
                })

    if not rows:
        print("× 選手行が見つかりませんでした。")
        return

    # 完走行・非完走行で列数が異なるため、出現した全キーを順序保持で集める。
    fieldnames = []
    for r in rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)
    with open(csv_path, "w", encoding="cp932", newline="", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    races = len({(r['会場'], r['レース']) for r in rows})
    venues = len({r['会場'] for r in rows})
    print(f"○ CSVを保存しました: {csv_path}")
    print(f"  会場数: {venues}  レース数: {races}  選手行: {len(rows)} 行 / 列: {len(fieldnames)} 列")
    print()
    print("--- 先頭3行プレビュー ---")
    print(",".join(fieldnames))
    for row in rows[:3]:
        print(",".join(str(row.get(k, '')) for k in fieldnames))


if __name__ == "__main__":
    main()
