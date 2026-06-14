# -*- coding: utf-8 -*-
"""
競艇予想アプリ Phase 0：公式データを1日分ダウンロード → 解凍 → 中身表示
-------------------------------------------------------------------------
やること:
  1. ボートレース公式の配布データ（番組表 or 競走成績）を1日分ダウンロード
  2. LZH形式を解凍してテキスト（Shift-JIS）を取り出す
  3. ファイルとして保存し、中身の先頭を画面に表示する

事前準備（初回だけ。コマンドプロンプト/ターミナルで実行）:
  pip install requests lhafile

使い方:
  下の「設定」の TARGET_DATE と DATA_TYPE を変えて、このファイルを実行するだけ。
  例)  python download_one_day.py
"""

import os
import datetime
import requests
import lhafile


# ============================================================
# 設定（ここだけ書き換えればOK）
# ============================================================

# 取得したい日付（"YYYY-MM-DD"）。まずは過去の日付で試すのが安全。
#   ・番組表 …… 開催日の前日くらいから取得可能
#   ・競走成績 … レースが終わった後（基本は翌日以降）に取得可能
TARGET_DATE = "2025-08-09"

# 取得するデータの種類: "program"（番組表＝出走前情報）or "results"（競走成績＝結果）
DATA_TYPE = "program"

# 保存先フォルダ（無ければ自動で作成）
SAVE_DIR = "data"

# 画面に表示する行数（中身の先頭だけ確認する用）
PREVIEW_LINES = 40


# ============================================================
# 以下はロジック。最初はそのままでOK
# ============================================================

# データ種類ごとの設定:
#   url_dir  … URL上のフォルダ名（B=番組表 / K=競走成績）
#   prefix   … ファイル名の先頭文字（b / k）
DATA_CONFIG = {
    "program": {"url_dir": "B", "prefix": "b", "label": "番組表"},
    "results": {"url_dir": "K", "prefix": "k", "label": "競走成績"},
}


def build_url(date: datetime.date, data_type: str) -> str:
    """日付とデータ種類から、ダウンロード先URLを組み立てる。

    URLの規則（公式配布サーバー）:
      http://www1.mbrace.or.jp/od2/{B|K}/{YYYYMM}/{b|k}{YYMMDD}.lzh
      例) 2025-08-09 の番組表:
          http://www1.mbrace.or.jp/od2/B/202508/b250809.lzh
    """
    conf = DATA_CONFIG[data_type]
    yyyymm = date.strftime("%Y%m")   # 例: 202508
    yymmdd = date.strftime("%y%m%d")  # 例: 250809
    filename = f"{conf['prefix']}{yymmdd}.lzh"
    url = f"http://www1.mbrace.or.jp/od2/{conf['url_dir']}/{yyyymm}/{filename}"
    return url


def download_lzh(url: str, save_path: str) -> bool:
    """URLからLZHファイルをダウンロードして save_path に保存する。
    成功したら True、その日のデータが無い等で失敗したら False を返す。
    """
    # User-Agent を付けておく（付けないと弾くサーバーがあるため）
    headers = {"User-Agent": "Mozilla/5.0 (boatrace-study-script)"}

    print(f"  ダウンロード中: {url}")
    try:
        res = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        print(f"  × 通信エラー: {e}")
        return False

    # 404 など = その日のファイルが存在しない（開催日でない/まだ未公開 など）
    if res.status_code != 200:
        print(f"  × データが見つかりません (HTTP {res.status_code})。日付を変えて試してください。")
        return False

    # 中身が空、またはHTMLが返ってきた場合（エラーページ）の簡易チェック
    if len(res.content) < 100:
        print("  × ファイルが小さすぎます。データが無い可能性があります。")
        return False

    with open(save_path, "wb") as f:
        f.write(res.content)
    print(f"  ○ 保存しました: {save_path} ({len(res.content):,} バイト)")
    return True


def extract_lzh(lzh_path: str) -> str:
    """LZHファイルを解凍し、中のテキスト（Shift-JIS）を文字列で返す。
    競艇の配布LZHには通常テキストファイルが1つだけ入っている。
    """
    archive = lhafile.Lhafile(lzh_path)
    names = archive.namelist()
    print(f"  解凍: アーカイブ内のファイル = {names}")

    if not names:
        raise ValueError("アーカイブが空でした。")

    # 最初のファイルを取り出す
    inner_name = names[0]
    raw_bytes = archive.read(inner_name)

    # 文字コードは Shift-JIS（cp932）。読めない文字があっても止まらないようにする
    text = raw_bytes.decode("cp932", errors="replace")
    return text


def main():
    # 文字列の日付を datetime に変換
    date = datetime.datetime.strptime(TARGET_DATE, "%Y-%m-%d").date()
    conf = DATA_CONFIG[DATA_TYPE]

    print("=" * 60)
    print(f"対象日付  : {date}")
    print(f"データ種類: {conf['label']}（{DATA_TYPE}）")
    print("=" * 60)

    # 保存フォルダを用意
    os.makedirs(SAVE_DIR, exist_ok=True)

    # 1) ダウンロード
    url = build_url(date, DATA_TYPE)
    lzh_name = os.path.basename(url)                       # 例: b250809.lzh
    lzh_path = os.path.join(SAVE_DIR, lzh_name)
    print("\n[1] ダウンロード")
    if not download_lzh(url, lzh_path):
        print("\n中断しました。TARGET_DATE を別の日（少し前の日付）に変えて再実行してみてください。")
        return

    # 2) 解凍
    print("\n[2] 解凍")
    text = extract_lzh(lzh_path)
    txt_path = os.path.splitext(lzh_path)[0] + ".txt"      # 例: b250809.txt
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  ○ テキストとして保存しました（UTF-8）: {txt_path}")

    # 3) 中身を表示
    print("\n[3] 中身プレビュー（先頭 %d 行）" % PREVIEW_LINES)
    print("-" * 60)
    lines = text.splitlines()
    for line in lines[:PREVIEW_LINES]:
        print(line)
    print("-" * 60)
    print(f"  全体の行数: {len(lines):,} 行")
    print("\n完了。全文は次のファイルで確認できます → %s" % txt_path)


if __name__ == "__main__":
    main()
