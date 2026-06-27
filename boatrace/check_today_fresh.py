"""today.html が「JSTの今日」を対象にしているかを判定する鮮度チェック。

用途: GitHub Actions の鮮度監視(boatrace-freshness.yml)から呼ぶ。
  - today.html に埋め込まれた payload の "base":"YYYY-MM-DD" を読む。
  - JST の今日と一致 → fresh（exit 0）。
  - 不一致 or 取得不能 → stale（exit 1）。監視側はこの時だけ daily を再実行する。

依存なし（標準ライブラリのみ）。py -3.13 で動作。

使い方:
  py -3 check_today_fresh.py                 # today.html を判定
  py -3 check_today_fresh.py --html x.html   # ファイル指定
  py -3 check_today_fresh.py --today 2026-06-26  # 比較対象日を上書き（テスト用）
"""
import argparse
import re
import sys
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
BASE_RE = re.compile(r'"base"\s*:\s*"(\d{4}-\d{2}-\d{2})"')


def extract_base(html_path):
    """today.html から対象日(base)を取り出す。見つからなければ None。"""
    try:
        with open(html_path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"stale: today.html を読めない（{e}）")
        return None
    m = BASE_RE.search(text)
    if not m:
        print("stale: today.html に base マーカーが見つからない")
        return None
    return m.group(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="today.html", help="判定対象のHTML（既定 today.html）")
    ap.add_argument("--today", default=None, help="比較対象日 YYYY-MM-DD（既定=JSTの今日）")
    args = ap.parse_args()

    today = args.today or datetime.now(JST).strftime("%Y-%m-%d")
    base = extract_base(args.html)
    if base is None:
        return 1

    if base == today:
        print(f"fresh: base={base} == JST今日={today}")
        return 0
    print(f"stale: base={base} != JST今日={today}（再生成が必要）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
