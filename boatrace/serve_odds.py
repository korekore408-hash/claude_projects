# -*- coding: utf-8 -*-
"""
ローカル配信サーバ（today.html 用・オッズ更新ボタンの中継）
-------------------------------------------------------------------------
today.html は静的ページなので、ブラウザから boatrace 公式へ直接 fetch すると
CORS でブロックされる。そこで PC 側でこの小サーバを立て、
  ・カレントフォルダのファイル（today.html 等）を配信
  ・GET /odds?id={race_id} で「押された時だけ」公式オッズを取得して JSON 返却
する。自動取得はしない（ボタン押下＝1リクエスト＝1レース分のみ）。

使い方:
  py -3.13 serve_odds.py                  # http://localhost:8787/today.html
  py -3.13 serve_odds.py --port 8787 --bind 0.0.0.0   # LANで携帯から
    （携帯は http://<PCのIP>:8787/today.html を開く。要ファイアウォール許可）

/odds の戻り値:
  {"id":"012026061801","2t":{"1-2":4.4,...},"3t":{"1-2-3":8.7,...},
   "fetched_at":"2026-06-18 21:00:12"}
"""

import os
import json
import argparse
import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import fetch_odds   # 同フォルダの取得・パース関数を再利用


def parse_race_id(rid: str):
    """race_id '012026061801' -> (jcd=1, hd='20260618', rno=1)。不正なら None。"""
    if not (rid and len(rid) == 12 and rid.isdigit()):
        return None
    return int(rid[:2]), rid[2:10], int(rid[10:12])


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path.rstrip("/") == "/odds":
            return self._odds()
        return super().do_GET()

    def _odds(self):
        qs = parse_qs(urlparse(self.path).query)
        rid = (qs.get("id") or [""])[0]
        parsed = parse_race_id(rid)
        if not parsed:
            return self._json({"error": "bad id"}, 400)
        jcd, hd, rno = parsed
        tri = fetch_odds.fetch_trifecta(jcd, rno, hd)
        exa = fetch_odds.fetch_exacta(jcd, rno, hd)
        body = {
            "id": rid,
            "2t": {f"{a}-{b}": o for (a, b), o in exa.items()},
            "3t": {f"{a}-{b}-{c}": o for (a, b, c), o in tri.items()},
            "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.log_message("odds id=%s 2t=%d 3t=%d", rid, len(exa), len(tri))
        return self._json(body)

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser(description="today.html 配信＋/odds 中継サーバ")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--bind", default="127.0.0.1", help="LAN配信は 0.0.0.0")
    args = ap.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    host = "localhost" if args.bind in ("127.0.0.1", "localhost") else args.bind
    print(f"配信中: http://{host}:{args.port}/today.html")
    print("  オッズ更新ボタン → GET /odds?id=race_id（押した時だけ取得）")
    print("  Ctrl+C で停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")


if __name__ == "__main__":
    main()
