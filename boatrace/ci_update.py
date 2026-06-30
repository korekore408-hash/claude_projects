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


def push_kv(payload_str):
    """update.json を Cloudflare KV に書き込む（速度A: ビルド枠を消費せず即時配信）。
    必要な環境変数（GitHub Secrets 経由で workflow が渡す）:
      CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID, CF_API_TOKEN（KV Edit 権限のトークン）
    3つすべて揃っていなければ何もしない（=従来どおり update.json コミットにフォールバック）。
    戻り値: 成功 True / 未設定・失敗 False。
    """
    acc = os.environ.get("CF_ACCOUNT_ID")
    ns = os.environ.get("CF_KV_NAMESPACE_ID")
    tok = os.environ.get("CF_API_TOKEN")
    if not (acc and ns and tok):
        return False
    import requests
    url = (f"https://api.cloudflare.com/client/v4/accounts/{acc}"
           f"/storage/kv/namespaces/{ns}/values/update")
    try:
        r = requests.put(
            url, data=payload_str.encode("utf-8"),
            headers={"Authorization": f"Bearer {tok}",
                     "Content-Type": "text/plain"},
            timeout=30)
        if r.status_code == 200:
            return True
        print(f"KV push failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"KV push error: {e}")
    return False


def main():
    hd = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    races = serve_odds.collect_update(hd, with_odds=True)
    out = {
        "date": hd,
        "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "races": races,
    }
    payload = json.dumps(out, ensure_ascii=False)
    with open("update.json", "w", encoding="utf-8") as f:
        f.write(payload)
    nres = sum(1 for r in races.values() if r.get("status") == "result")
    nev = sum(1 for r in races.values() if r.get("ev") is not None)
    print(f"wrote update.json hd={hd} races={len(races)} result={nres} ev={nev}")

    # 速度A: KV へ即時配信。成功したら workflow にその旨を伝え、commit（=リビルド）を省ける。
    kv_ok = push_kv(payload)
    print(f"kv={'ok' if kv_ok else 'skip'}")
    go = os.environ.get("GITHUB_OUTPUT")
    if go:
        with open(go, "a", encoding="utf-8") as f:
            f.write(f"kv_ok={'1' if kv_ok else '0'}\n")


if __name__ == "__main__":
    main()
