# -*- coding: utf-8 -*-
"""today.json を Cloudflare KV へ書き込む（クラウド配信・ビルド枠を消費しない）。

v1 ci_update.py の push_kv と同じ仕組み・同じ Secrets を使う:
  CF_ACCOUNT_ID / CF_KV_NAMESPACE_ID / CF_API_TOKEN（KV Edit 権限）
3つ揃っていなければ何もしない（ローカル実行では常に no-op）。
書いたキーは Pages Function（functions/api/v2.js）が GET /api/v2 で配信する。

使い方（GitHub Actions から）:
  python push_kv.py                     # data/web/today.json → キー v2_today
"""
import argparse
import os
import sys

import requests

try:
    from . import config
except ImportError:
    import config


def push(payload_str, key="v2_today"):
    """成功 True / Secrets 未設定 None / 失敗 False。"""
    acc = os.environ.get("CF_ACCOUNT_ID")
    ns = os.environ.get("CF_KV_NAMESPACE_ID")
    tok = os.environ.get("CF_API_TOKEN")
    if not (acc and ns and tok):
        return None
    url = (f"https://api.cloudflare.com/client/v4/accounts/{acc}"
           f"/storage/kv/namespaces/{ns}/values/{key}")
    try:
        r = requests.put(url, data=payload_str.encode("utf-8"),
                         headers={"Authorization": f"Bearer {tok}",
                                  "Content-Type": "text/plain"},
                         timeout=30)
        if r.status_code == 200:
            return True
        print(f"KV push failed: {r.status_code} {r.text[:200]}")
    except requests.RequestException as e:
        print(f"KV push error: {e}")
    return False


def main():
    ap = argparse.ArgumentParser(description="today.json を Cloudflare KV へ配信")
    ap.add_argument("--file", default=os.path.join(config.WEB_DIR, "today.json"))
    ap.add_argument("--key", default="v2_today")
    args = ap.parse_args()
    try:
        with open(args.file, encoding="utf-8") as f:
            payload = f.read()
    except OSError as e:
        sys.exit(f"読込失敗: {e}（先に report.py を実行してください）")
    ok = push(payload, args.key)
    if ok is None:
        print("::warning::CF_* Secrets 未設定 → KV push をスキップ（配信は更新されません）")
    elif ok:
        print(f"○ KV push 完了: key={args.key}（{len(payload)} bytes）")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
