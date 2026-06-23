# -*- coding: utf-8 -*-
"""
グレード公式取得（②）
-------------------------------------------------------------------------
B-file 出走表ヘッダからのグレード判定は、SG/G1/G2/G3 の明示マーカーが
無い節（例: 鳴門グランドチャンピオン=SG だが無印で『一般』表示）を取りこぼす。
そこで公式の開催インデックスから、場コードごとの正しいグレードを取得する。

取得元: https://www.boatrace.jp/owpc/pc/race/index?hd=YYYYMMDD
  各開催場のグレードは <td ... class="is-SGa">/is-G1a/is-G2a/is-G3a/is-ippan>
  のセルで示され、直後に raceindex?jcd=NN（場コード）リンクが続く。

返り値: fetch_grades(hd) -> {場コード2桁: 'SG'|'G1'|'G2'|'G3'|'一般'}
通信失敗時は空dict（呼び出し側は B-file 判定にフォールバック）。
"""
import re
import datetime

import requests

URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={hd}"
HEADERS = {"User-Agent": "Mozilla/5.0 (boatrace-study-script)"}

# 公式クラストークン → 表示グレード
_GRADE = {"SGa": "SG", "G1a": "G1", "G2a": "G2", "G3a": "G3", "ippan": "一般"}
# グレードセル直後の jcd を拾う（is-SGa など → 次に現れる raceindex?jcd=NN）
_CELL_RE = re.compile(r'class="is-(SGa|G1a|G2a|G3a|ippan)\b')
_JCD_RE = re.compile(r'raceindex\?jcd=(\d{1,2})')


def fetch_grades(hd: str):
    """hd(YYYYMMDD) の各開催場グレードを {場コード2桁: グレード} で返す。失敗時 {}。"""
    try:
        res = requests.get(URL.format(hd=hd), headers=HEADERS, timeout=30)
    except requests.RequestException:
        return {}
    if res.status_code != 200:
        return {}
    t = res.content.decode("utf-8", "replace")
    out = {}
    for m in _CELL_RE.finditer(t):
        grade = _GRADE[m.group(1)]
        mj = _JCD_RE.search(t, m.end())          # そのセル以降で最初の jcd
        if not mj:
            continue
        code = f"{int(mj.group(1)):02d}"
        out.setdefault(code, grade)              # 同場の先頭セルを採用
    return out


if __name__ == "__main__":
    import sys
    hd = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    g = fetch_grades(hd)
    print(f"hd={hd}  開催 {len(g)}場")
    for code in sorted(g):
        print(f"  {code}: {g[code]}")
