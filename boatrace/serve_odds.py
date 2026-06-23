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
import sys
import json
import argparse
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import fetch_odds      # 同フォルダの取得・パース関数を再利用
import fetch_before    # 展示（直前情報）・結果の取得

# 公式サイトは1リクエスト約9秒（接続レイテンシ）だが同時接続は捌ける。
# 逐次だと全場で約70分かかるため並列取得する。多重押下は1本に直列化。
UPDATE_WORKERS = 10
_update_lock = threading.Lock()


def parse_race_id(rid: str):
    """race_id '012026061801' -> (jcd=1, hd='20260618', rno=1)。不正なら None。"""
    if not (rid and len(rid) == 12 and rid.isdigit()):
        return None
    return int(rid[:2]), rid[2:10], int(rid[10:12])


# 展示・結果のディスクキャッシュ（確定済みレースは再取得しない）
BEFORE_DIR = os.path.join("data", "before")


def _cache_path(hd):
    return os.path.join(BEFORE_DIR, f"before_{hd}.json")


def _load_cache(hd):
    try:
        with open(_cache_path(hd), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(hd, cache):
    os.makedirs(BEFORE_DIR, exist_ok=True)
    with open(_cache_path(hd), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def _rec(d):
    return {"ex": d["ex"], "result": d["result"], "status": d["status"]}


def _complete(rec):
    """結果確定＋展示取得済み＝これ以上変わらない（再取得不要）。"""
    return bool(rec and rec.get("status") == "result" and rec.get("ex"))


def _merge(old, new):
    """以前その日に調べた情報(old)に最新取得(new)を重ねる。展示・結果は消さずに残す。
    （レース終了後の取得で展示が空でも、朝に取れた展示を保持し続けるのが狙い。）"""
    if not old:
        return new
    if not new:
        return old
    out = dict(new)
    if not out.get("ex") and old.get("ex"):            # 展示は一度取れたら残す
        out["ex"] = old["ex"]
    if not out.get("result") and old.get("result"):    # 結果は確定情報なので残す
        out["result"] = old["result"]
    out["status"] = ("result" if out.get("result")
                     else ("before" if out.get("ex") else "none"))
    return out


def collect_update(hd, workers=UPDATE_WORKERS):
    """当日の全場・全レースの展示+結果を集める（並列取得）。
    その日にこれまで調べた展示・結果を **すべて引き継いで** 返し、ディスクにも保存する
    （リロード・サーバ再起動・レース終了後でも、朝に取れた展示が消えない）。
    手順: ①各場R1で開催場を判定（完成済みの場はキャッシュ流用、未完成のみ取得）
          ②開催場のR2..R12を並列取得（完成＝結果確定＋展示あり はキャッシュ流用）。
    取得した展示・結果は既存キャッシュにマージして欠損を防ぐ。"""
    cache = _load_cache(hd)
    out = dict(cache)            # その日これまでに調べた情報をすべて引き継ぐ

    def fetch_one(jr):
        jcd, rno = jr
        return jcd, rno, fetch_before.fetch_race(jcd, rno, hd)

    # ① 開催場判定。完成済みR1は流用、それ以外の場だけR1を並列取得。
    held = set()
    need_r1 = []
    for jcd in range(1, 25):
        rid = f"{jcd:02d}{hd}01"
        if _complete(out.get(rid)):
            held.add(jcd)                              # 既に完成→再取得不要・開催場として維持
        else:
            need_r1.append((jcd, 1))
    if need_r1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for jcd, _rno, d in ex.map(fetch_one, need_r1):
                rid = f"{jcd:02d}{hd}01"
                if d["status"] != "none":
                    held.add(jcd)
                    out[rid] = _merge(out.get(rid), _rec(d))
                elif rid in out:                       # 以前開催と判定済みなら維持
                    held.add(jcd)

    # ② 開催場の R2..R12。完成済みは流用、残りを並列取得してマージ。
    todo = []
    for jcd in sorted(held):
        for rno in range(2, 13):
            rid = f"{jcd:02d}{hd}{rno:02d}"
            if not _complete(out.get(rid)):
                todo.append((jcd, rno))
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for jcd, rno, d in ex.map(fetch_one, todo):
                rid = f"{jcd:02d}{hd}{rno:02d}"
                if d["status"] != "none":
                    out[rid] = _merge(out.get(rid), _rec(d))

    # その日に調べた展示・結果をすべて保存（再押下・再起動・リロードで復元）。
    _save_cache(hd, out)
    return out


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        p = urlparse(self.path).path.rstrip("/")
        if p == "/odds":
            return self._odds()
        if p == "/update":
            return self._update()
        return super().do_GET()

    def _update(self):
        qs = parse_qs(urlparse(self.path).query)
        date = (qs.get("date") or [""])[0]             # YYYYMMDD or YYYY-MM-DD
        hd = date.replace("-", "")
        if not (len(hd) == 8 and hd.isdigit()):
            hd = datetime.date.today().strftime("%Y%m%d")
        # 多重押下ガード: 既に取得中なら走らせず busy を返す（暴走防止）。
        if not _update_lock.acquire(blocking=False):
            return self._json({"busy": True,
                               "msg": "前回の更新がまだ取得中です。完了までお待ちください。"}, 409)
        try:
            races = collect_update(hd)
        finally:
            _update_lock.release()
        body = {"date": hd,
                "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "races": races}
        n_res = sum(1 for r in races.values() if r["status"] == "result")
        self.log_message("update hd=%s races=%d result=%d", hd, len(races), n_res)
        return self._json(body)

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
    # pythonw.exe（コンソール無し）で常駐起動すると stdout/stderr が None になり、
    # アクセスログ出力で落ちる。ログをファイルに固定する（コンソール無し＝Ctrl+Cで殺されない）。
    if sys.stdout is None or sys.stderr is None:
        os.makedirs("data", exist_ok=True)
        logf = open(os.path.join("data", "serve.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = logf
    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    host = "localhost" if args.bind in ("127.0.0.1", "localhost") else args.bind
    print(f"配信中: http://{host}:{args.port}/today.html")
    print("  オッズ更新ボタン → GET /odds?id=race_id（押した時だけ取得）")
    print("  更新ボタン       → GET /update?date=YYYYMMDD（当日の展示+結果を一括／確定分はキャッシュ）")
    print("  Ctrl+C で停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")


if __name__ == "__main__":
    main()
