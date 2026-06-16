# -*- coding: utf-8 -*-
"""
当日予想 デイリー実行（取得→変換→特徴量→予測→ページ生成を一括）
=========================================================================
朝の出走表（B-file）が出たら、これ1本で当日の予想ページを更新する。

  1. fetch_range : 指定日のB（出走表）＋前日のK（結果）を取得
  2. convert_all : 追加分を CSV 化
  3. features_player_history / features_race_relative / race_flags : 全期間 as-of 再計算
  4. predict_combos --mode split : 当日を含む全レースの1着確率/strength を出力
  5. build_today : 当日予想メインページ（携帯向け）
  6. build_viewer --last-days 14 : 直近14日の詳細ビューア（当日が既定表示）

使い方:
  py -3 daily.py                 # 今日（システム日付）
  py -3 daily.py --date 2026-06-16
"""

import argparse
import datetime
import subprocess
import sys


def run(args, **kw):
    print(f"\n$ {' '.join(args)}")
    r = subprocess.run([sys.executable] + args, **kw)
    if r.returncode != 0:
        print(f"× 失敗 (exit {r.returncode}): {args}")
        sys.exit(r.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="対象日 YYYY-MM-DD（既定=今日）")
    args = ap.parse_args()

    today = (datetime.date.fromisoformat(args.date) if args.date
             else datetime.date.today())
    prev = today - datetime.timedelta(days=1)
    print(f"=== 当日予想デイリー: {today} ===")

    # 1. 取得（前日Kで履歴を最新化 ＋ 当日Bで出走表）。当日Kはまだ無いので404でOK。
    run(["fetch_range.py", "--start", prev.isoformat(),
         "--end", today.isoformat(), "--which", "both"])
    # 2. 変換（未変換のみ）
    run(["convert_all.py"])
    # 3. 特徴量（全期間 as-of 再計算）
    run(["features_player_history.py"])
    run(["features_race_relative.py"])
    run(["race_flags.py"])
    # 4. 予測（train<=260430 で学習し全レース出力。当日は finish 無し＝予想のみ）
    run(["predict_combos.py", "--mode", "split", "--train-end", "260430"])
    # 5-7. ページ生成（HTMLアプリ ＋ 携帯どこでも用PDF ＋ 詳細ビューア）
    run(["build_today.py", "--date", today.isoformat()])
    run(["build_today_pdf.py", "--date", today.isoformat()])
    run(["build_viewer.py", "--last-days", "14"])

    print(f"\n○ 完了:")
    print("  today.html … 当日予想アプリ（自宅LAN配信や保存用）")
    print("  today.pdf  … 携帯でどこでも開ける当日予想（OneDrive同期で外出先もOK）")
    print("  viewer.html… 直近14日の詳細ビューア（PC向け）")


if __name__ == "__main__":
    main()
