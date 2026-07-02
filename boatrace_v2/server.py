# -*- coding: utf-8 -*-
"""配信サーバ v2 — T6（トークン認証・配信ホワイトリスト）。

v1 serve_odds.py からのセキュリティ改善:
  - LAN公開（--bind が 127.0.0.1/localhost 以外）は SERVE_TOKEN 必須。
    未設定なら起動を拒否する（無認証で全ファイル配信になる事故を防ぐ）
  - 静的配信は ALLOW_FILES のホワイトリストのみ（data/ のCSV・ログは配信しない）
  - /odds は race_id を厳格検証し、取得結果はスナップショットにも追記保存

スマホ対応:
  - 一度 ?token=xxxx でアクセスすると Cookie に保存され、以後はトークンなしのURLでも
    開ける（ブックマーク可）
  - LAN内での自分のIPは lan_ip() で検出（app.py --lan がURLを表示する）

使い方:
  python server.py                                  # localhost:8788（トークン不要）
  SERVE_TOKEN=xxxx python server.py --bind 0.0.0.0  # LAN公開（?token=xxxx 必須）
"""
import argparse
import datetime
import json
import os
import socket
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    from . import config, odds
except ImportError:
    import config
    import odds

TOKEN = os.environ.get("SERVE_TOKEN", "")
COOKIE_NAME = "nb_token"
ALLOW_FILES = {"/today.html", "/today.json"}   # 配信は data/web/ のこの2つだけ
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def lan_ip():
    """LAN内で他端末から見える自分のIP（UDP connect は実送信なし）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "<PCのIPアドレス>"
    finally:
        s.close()


def parse_race_id(rid):
    if not (rid and len(rid) == 12 and rid.isdigit()):
        return None
    jcd = int(rid[:2])
    if not 1 <= jcd <= 24:
        return None
    return jcd, rid[2:10], int(rid[10:12])


class Handler(BaseHTTPRequestHandler):
    def _cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                return v
        return ""

    def do_GET(self):
        u = urlparse(self.path)
        set_cookie = False
        if TOKEN:
            q_token = (parse_qs(u.query).get("token") or [""])[0]
            given = (q_token or self.headers.get("X-Auth-Token", "")
                     or self._cookie_token())
            if given != TOKEN:
                return self._json({"error": "unauthorized",
                                   "hint": "?token=... を付けてアクセス"}, 401)
            set_cookie = bool(q_token)      # 正しいトークンをCookieに保存（スマホ用）
        p = u.path.rstrip("/") or "/"
        if p == "/odds":
            return self._odds(u)
        if p in ("/", "/index.html"):
            p = "/today.html"
        if p not in ALLOW_FILES or not os.path.exists(p.lstrip("/")):
            return self._json({"error": "not found"}, 404)
        return self._file(p, set_cookie)

    def _file(self, p, set_cookie):
        with open(p.lstrip("/"), "rb") as f:
            data = f.read()
        ctype = ("text/html; charset=utf-8" if p.endswith(".html")
                 else "application/json; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        if set_cookie:
            self.send_header("Set-Cookie",
                             f"{COOKIE_NAME}={TOKEN}; Path=/; Max-Age=2592000; HttpOnly")
        self.end_headers()
        self.wfile.write(data)

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


def check_bind(bind):
    """LAN公開（loopback 以外へのバインド）はトークン必須 — T6。"""
    if bind not in LOOPBACK and not TOKEN:
        raise SystemExit(
            "エラー: LAN公開（--bind が localhost 以外）には環境変数 SERVE_TOKEN が必須です。\n"
            "例: SERVE_TOKEN=$(python -c 'import secrets;print(secrets.token_urlsafe(16))') "
            "python server.py --bind 0.0.0.0")


def serve(bind="127.0.0.1", port=8788):
    """配信を開始（ブロッキング）。app.py からも呼ばれる。"""
    check_bind(bind)
    config.ensure_dirs()
    os.chdir(config.WEB_DIR)      # 配信ルート＝生成物ディレクトリのみ
    srv = ThreadingHTTPServer((bind, port), Handler)
    if bind in LOOPBACK:
        print(f"[{config.APP_TITLE}] 配信中: http://127.0.0.1:{port}/ （このPCのみ）")
    else:
        print(f"[{config.APP_TITLE}] LAN公開中。スマホ・他端末はこのURLを開く:")
        print(f"  http://{lan_ip()}:{port}/?token={TOKEN}")
        print("  （一度開けばCookieに保存され、以後は ?token なしでもOK。"
              "つながらない場合はPC側ファイアウォールで Python の受信を許可）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")


def main():
    ap = argparse.ArgumentParser(description="v2 配信サーバ（認証・ホワイトリスト付き）")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()
    serve(args.bind, args.port)


if __name__ == "__main__":
    main()
