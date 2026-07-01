# -*- coding: utf-8 -*-
"""共通HTTPクライアント（polite fetch）。

v2 の全取得はここを経由する。
  - BoundedSemaphore(MAX_CONCURRENCY) で同時接続を 3 本に制限
  - 1リクエストごとに REQUEST_INTERVAL 秒の間隔
  - 失敗時は指数バックオフで RETRIES 回まで再試行
"""
import threading
import time

import requests

try:
    from . import config
except ImportError:
    import config

_GATE = threading.BoundedSemaphore(config.MAX_CONCURRENCY)
_HEADERS = {"User-Agent": config.USER_AGENT}


def polite_get(url, retries=None):
    """URL を取得して HTML 文字列を返す。失敗（非200含む）は None。"""
    retries = config.RETRIES if retries is None else retries
    for attempt in range(retries + 1):
        with _GATE:
            try:
                res = requests.get(url, headers=_HEADERS, timeout=config.TIMEOUT)
            except requests.RequestException:
                res = None
            time.sleep(config.REQUEST_INTERVAL)   # ゲート内で待つ＝実効レートも制限
        if res is not None and res.status_code == 200:
            return res.content.decode("utf-8", "replace")
        if res is not None and res.status_code == 404:
            return None                            # 未開催等。リトライ不要
        if attempt < retries:
            time.sleep(2 ** attempt)
    return None
