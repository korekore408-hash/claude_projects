# -*- coding: utf-8 -*-
"""
当日予想 デイリー実行（取得→変換→特徴量→予測→ページ生成を一括）
=========================================================================
朝の出走表（B-file）が出たら、これ1本で当日の予想ページを更新する。

  1. fetch_range : 指定日のB（出走表）＋前日のK（結果）を取得
  2. convert_all : 追加分を CSV 化
  3. features_player_history / features_race_relative / race_flags : 全期間 as-of 再計算
  4. 予測:
     4a. train<=260430 固定で全レース予測（直近=OOSのまま honest な的中率/回収率）
     4b. 前日まで全データで再学習した最新モデルで予測（学習を毎日追加）
     4c. 当日のレース行だけ最新モデルに差し替え（過去はそのまま＝評価は honest）
  5. build_today : 当日予想メインページ（携帯向け）
  6. build_viewer --last-days 14 : 直近14日の詳細ビューア（当日が既定表示）

注: 全期間ウォークフォワード（毎日全データ再学習）は約97分かかり非現実的なため、
    上記の「当日のみ最新モデル差し替え」で学習追加を実現。検証では再学習しても
    1着的中率は約0.567で頭打ち（直前情報なしモデルの構造的上限）。

使い方:
  py -3 daily.py                 # 今日（システム日付）
  py -3 daily.py --date 2026-06-16
"""

import argparse
import csv
import datetime
import os
import subprocess
import sys


def run(args, **kw):
    print(f"\n$ {' '.join(args)}")
    r = subprocess.run([sys.executable] + args, **kw)
    if r.returncode != 0:
        print(f"× 失敗 (exit {r.returncode}): {args}")
        sys.exit(r.returncode)


def run_optional(args, **kw):
    """失敗してもパイプライン全体は止めない（PDF/ビューア等の非必須生成物用）。
    例: Linux(GitHub Actions)では today.pdf の日本語フォント(msgothic.ttc)が無く落ちるが、
    クラウド配信に PDF は不要なので警告だけ出して続行し、today.html の commit を妨げない。"""
    print(f"\n$ {' '.join(args)}")
    r = subprocess.run([sys.executable] + args, **kw)
    if r.returncode != 0:
        print(f"⚠ スキップ（exit {r.returncode}・続行）: {args}")


def merge_today(today, base="predict_win.csv", latest="predict_win_latest.csv"):
    """当日のレース行だけ、最新モデル(前日まで学習)の予測に差し替える。
    過去レースは base（固定split）のまま＝直近の評価は honest OOS を維持。"""
    ymd = today.strftime("%Y%m%d")
    with open(latest, encoding="cp932") as f:
        lat = {(r["race_id"], r["枠番"]): r for r in csv.DictReader(f)}
    with open(base, encoding="cp932") as f:
        rows = list(csv.DictReader(f))
        fn = list(rows[0].keys())
    nrep = 0
    for i, r in enumerate(rows):
        if r["race_id"][2:10] == ymd:
            k = (r["race_id"], r["枠番"])
            if k in lat:
                rows[i] = lat[k]
                nrep += 1
    with open(base, "w", encoding="cp932", newline="", errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=fn)
        w.writeheader()
        w.writerows(rows)
    print(f"  当日 {nrep} 行を最新モデル予測に差し替え")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="対象日 YYYY-MM-DD（既定=今日）")
    args = ap.parse_args()

    today = (datetime.date.fromisoformat(args.date) if args.date
             else datetime.date.today())
    prev = today - datetime.timedelta(days=1)
    print(f"=== 当日予想デイリー: {today} ===")

    # 0. 前日Kの強制取り直し（再発防止）。
    #    ナイター場(蒲郡/若松/大村等)は終了が遅く、前日の夕方に取得したKは不完全版になる。
    #    download_lzh は既存ファイルがあると再DLしないので、消してから取り直すことで
    #    翌朝に揃う完全版へ上書きする（前日結果の欠落を自動修復）。
    prev_key = prev.strftime("%y%m%d")
    for ext in ("lzh", "txt", "csv"):
        p = os.path.join("data", f"k{prev_key}.{ext}")
        if os.path.exists(p):
            os.remove(p)
            print(f"  前日K 再取得のため削除: {p}")

    # 1. 取得（前日Kで履歴を最新化 ＋ 当日Bで出走表）。当日Kはまだ無いので404でOK。
    run(["fetch_range.py", "--start", prev.isoformat(),
         "--end", today.isoformat(), "--which", "both"])
    # 2. 変換（未変換のみ）
    run(["convert_all.py"])
    # 2b. 非公式OpenAPIと公式B-fileの (艇番→登番) クロスチェックを毎日ログ化
    #     （data/openapi_crosscheck_log.csv に追記）。フォールバック本組込の前提条件＝
    #     n_diff=0 が数日続くのを自動で蓄積するための観測。OpenAPI today.json は「当日」専用
    #     なので、対象日がシステム日付と一致するときだけ実行。失敗してもパイプラインは止めない。
    if today == datetime.date.today():
        run_optional(["fetch_openapi.py", "--log", "--date", today.strftime("%Y%m%d")])
    # 3. 特徴量（全期間 as-of 再計算）
    run(["features_player_history.py"])
    # 当日の気象は「後で反映する」方針＝中立化（未反映）。場の荒れ度は構造的なので残す。
    run(["features_race_relative.py", "--neutral-weather-date", today.isoformat()])
    run(["race_flags.py"])
    # 4a. 評価用予測（train<=260430 固定。直近=OOSのまま honest な的中率/回収率）。
    run(["predict_combos.py", "--mode", "split", "--train-end", "260430"])
    # 4b. 当日用に「前日まで全データ」で再学習した最新モデルで予測（学習を毎日追加）。
    run(["predict_combos.py", "--mode", "split", "--train-end", prev_key,
         "--out", "predict_win_latest.csv"])
    # 4c. 当日のレース行だけ最新モデルの予測に差し替え（過去はそのまま）。
    merge_today(today)
    # 5-7. ページ生成（HTMLアプリ ＋ 携帯どこでも用PDF ＋ 詳細ビューア）
    run(["build_today.py", "--date", today.isoformat()])
    # PDF/ビューアは非必須（クラウド配信不要）。失敗しても today.html の commit を止めない。
    run_optional(["build_today_pdf.py", "--date", today.isoformat()])
    run_optional(["build_viewer.py", "--last-days", "14"])

    print(f"\n○ 完了:")
    print("  today.html … 当日予想アプリ（自宅LAN配信や保存用）")
    print("  today.pdf  … 携帯でどこでも開ける当日予想（OneDrive同期で外出先もOK）")
    print("  viewer.html… 直近14日の詳細ビューア（PC向け）")


if __name__ == "__main__":
    main()
