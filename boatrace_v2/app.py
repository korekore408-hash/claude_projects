# -*- coding: utf-8 -*-
"""Nishio-boooto — 1コマンド起動のアプリ本体。

これ1つで以下が動く:
  1. scheduler: 当日オッズのスナップショット収集（定期スイープ＋締切5分前ブースト）
  2. report:    更新のたび（＋最低1分毎）に today.html / today.json を再生成
  3. server:    ブラウザ向け配信（http://127.0.0.1:8788/ — LAN公開はトークン必須）

前提: v1 daily.py が当日の predict_win.csv を生成済みであること。
較正曲線（calibration.py）が無い場合は生の p_win で動き、画面に警告を出す。

使い方:
  python app.py                          # 今日を最終レースまで
  python app.py --ev-min 1.2 --hon-min 0
  python app.py --no-fetch               # 収集せず配信のみ（取得済みデータの閲覧）
  SERVE_TOKEN=xxxx python app.py --bind 0.0.0.0   # LAN公開
"""
import argparse
import datetime
import threading
import time

try:
    from . import config, report, scheduler, server
except ImportError:
    import config
    import report
    import scheduler
    import server


def main():
    ap = argparse.ArgumentParser(description=f"{config.APP_TITLE}（v2 アプリ起動）")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--ev-min", type=float, default=1.5)
    ap.add_argument("--hon-min", type=float, default=0.60)
    ap.add_argument("--lead", type=int, default=5, help="締切ブースト（発走何分前）")
    ap.add_argument("--sweep-min", type=int, default=30, help="定期スイープ間隔（分）")
    ap.add_argument("--no-fetch", action="store_true", help="収集せず配信のみ")
    args = ap.parse_args()
    server.check_bind(args.bind)          # トークン無しのLAN公開は起動前に拒否
    config.ensure_dirs()
    hd = args.date.replace("-", "")

    def refresh():
        report.build(hd, ev_min=args.ev_min, hon_min=args.hon_min)

    refresh()                             # 起動直後に一度描画（データ無しでも警告画面が出る）
    print(f"[{config.APP_TITLE}] EV>={args.ev_min:g} / 本命>={args.hon_min:g} / {hd}")

    if not args.no_fetch:
        threading.Thread(
            target=scheduler.run,
            kwargs=dict(hd=hd, lead=args.lead, sweep_min=args.sweep_min,
                        on_update=refresh),
            daemon=True, name="scheduler").start()

    def ticker():                          # 取得が無くても鮮度表示を進める
        while True:
            time.sleep(60)
            try:
                refresh()
            except Exception as e:
                print(f"[app] 画面再生成失敗: {e}")

    threading.Thread(target=ticker, daemon=True, name="report-ticker").start()

    server.serve(args.bind, args.port)     # ブロッキング（Ctrl+C で終了）


if __name__ == "__main__":
    main()
