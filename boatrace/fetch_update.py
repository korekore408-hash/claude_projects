# -*- coding: utf-8 -*-
"""
30分毎データ取得（③）
-------------------------------------------------------------------------
当日の展示・結果・鉄板レースの実オッズ(EV)を取得して before キャッシュと
オッズCSVを更新する。Windowsタスクスケジューラから 8:00〜23:00 に30分毎で呼ぶ。

動作:
  ① まず起動中のサーバ http://localhost:8787/update?odds=1 を叩く
     （サーバのロックを通すので手動「更新」ボタンと衝突しない＝キャッシュ安全）。
  ② サーバが落ちていれば collect_update を直接実行（サーバ無しでも取得は続く）。

使い方: py -3.13 fetch_update.py        # 今日
        py -3.13 fetch_update.py 20260624
"""
import os
import sys
import datetime
import urllib.request


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    hd = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    url = f"http://localhost:8787/update?date={hd}&odds=1"
    try:
        with urllib.request.urlopen(url, timeout=900) as r:
            n = len(r.read())
        print(f"[{ts}] server /update OK hd={hd} ({n} bytes)")
        return
    except Exception as e:
        print(f"[{ts}] server unreachable ({e}) → 直接取得")

    import serve_odds
    out = serve_odds.collect_update(hd, with_odds=True)
    nres = sum(1 for v in out.values() if v.get("status") == "result")
    nev = sum(1 for v in out.values() if v.get("ev") is not None)
    print(f"[{ts}] direct collect_update OK hd={hd} races={len(out)} result={nres} ev={nev}")


if __name__ == "__main__":
    main()
