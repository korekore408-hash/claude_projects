# -*- coding: utf-8 -*-
"""配信サーバ v2 — T6（トークン認証・配信ホワイトリスト）。

v1 serve_odds.py からのセキュリティ改善:
  - LAN公開（--bind が 127.0.0.1/localhost 以外）は SERVE_TOKEN 必須。
    未設定なら起動を拒否する（無認証で全ファイル配信になる事故を防ぐ）
  - 静的配信は ALLOW_FILES のホワイトリストのみ（data/ のCSV・ログは配信しない）
  - /odds は race_id を厳格検証し、取得結果はスナップショットにも追記保存

使い方:
  python server.py                                  # localhost:8788（トークン不要）
  SERVE_TOKEN=xxxx python server.py --bind 0.0.0.0  # LAN公開（?token=xxxx 必須）
"""
import argparse
import datetime
import json
import os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    from . import config, odds
except ImportError:
    import config
    import odds

TOKEN = os.environ.get("SERVE_TOKEN", "")
ALLOW_FILES = {"/index.html", "/today.html", "/update.json"}
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def parse_race_id(rid):
    if not (rid and len(rid) == 12 and rid.isdigit()):
        return None
    jcd = int(rid[:2])
    if not 1 <= jcd <= 24:
        return None
    return jcd, rid[2:10], int(rid[10:12])


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if TOKEN:
            q = parse_qs(u.query)
            given = (q.get("token") or [""])[0] or self.headers.get("X-Auth-Token", "")
            if given != TOKEN:
                return self._json({"error": "unauthorized"}, 401)
        p = u.path.rstrip("/") or "/"
        if p == "/odds":
            return self._odds(u)
        if p == "/":
            p = "/index.html"
        if p not in ALLOW_FILES or not os.path.exists(p.lstrip("/")):
            return self._json({"error": "not found"}, 404)
        self.path = p          # ホワイトリスト内のみ静的配信
        return super().do_GET()

    def _odds(self, u):
        rid = (parse_qs(u.query).get("id") or [""])[0]
        parsed = parse_race_id(rid)
        if not parsed:
            return self._json({"error": "bad id"}, 400)
        jcd, hd, rno = parsed
        tri, exa = odds.fetch_race_odds(jcd, rno, hd)
        fetched_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if tri or exa:
            odds.append_snapshot(hd, rid, tri, exa, fetched_at)   # 履歴にも残す
        return self._json({
            "id": rid,
            "2t": {f"{a}-{b}": o for (a, b), o in exa.items()},
            "3t": {f"{a}-{b}-{c}": o for (a, b, c), o in tri.items()},
            "fetched_at": fetched_at,
        })

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser(description="v2 配信サーバ（認証・ホワイトリスト付き）")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()
    if args.bind not in LOOPBACK and not TOKEN:
        raise SystemExit(
            "エラー: LAN公開（--bind が localhost 以外）には環境変数 SERVE_TOKEN が必須です。\n"
            "例: SERVE_TOKEN=$(python -c 'import secrets;print(secrets.token_urlsafe(16))') "
            "python server.py --bind 0.0.0.0")
    os.chdir(config.V2_DIR)
    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"配信中: http://{args.bind}:{args.port}/  "
          f"(認証: {'token必須' if TOKEN else 'なし=ローカルのみ'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")


if __name__ == "__main__":
    main()
