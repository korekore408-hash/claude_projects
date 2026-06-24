# -*- coding: utf-8 -*-
"""
CI（GitHub Actions）用の40分更新ドライバ。
-------------------------------------------------------------------------
ローカルでは serve_odds.py のサーバが /update で展示・結果・EVを返すが、
クラウド静的配信ではサーバが無いため、ここで collect_update を直接呼び、
その結果を today.html が読む静的ファイル update.json として書き出す。

  - collect_update(hd, with_odds=True): 当日全場の展示+結果+鉄板EVを取得
  - update.json: {"date","fetched_at","races":{race_id: rec}} を boatrace/ 直下へ
    （today.html は従来 /update が返していたのと同じ形を ./update.json から読む）

使い方: python ci_update.py            # TZ=Asia/Tokyo の今日
        python ci_update.py 20260624
"""
import os
import sys
import json
import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
import serve_odds


def main():
    hd = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    races = serve_odds.collect_update(hd, with_odds=True)
    out = {
        "date": hd,
        "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "races": races,
    }
    with open("update.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    nres = sum(1 for r in races.values() if r.get("status") == "result")
    nev = sum(1 for r in races.values() if r.get("ev") is not None)
    print(f"wrote update.json hd={hd} races={len(races)} result={nres} ev={nev}")


if __name__ == "__main__":
    main()
