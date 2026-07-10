# -*- coding: utf-8 -*-
"""
競走成績テキスト k*.txt から全レースの払戻金（7券種）を抽出し、
「各券種を全通り（全組合せ）100円ずつ購入し続けた場合」の
総投資額・総回収額・回収率を計算する。

7券種と全通り購入時の組合せ数（6艇立て前提）:
  単勝      : 6 通り        → 600 円/レース
  複勝      : 6 通り        → 600 円/レース   （的中は上位2着=2点）
  2連複     : C(6,2)=15 通り → 1500 円/レース
  ワイド(拡連複): C(6,2)=15 通り → 1500 円/レース （的中は上位3着の3ペア=3点）
  2連単     : 6*5=30 通り    → 3000 円/レース
  3連複     : C(6,3)=20 通り → 2000 円/レース
  3連単     : 6*5*4=120 通り → 12000 円/レース

回収額 = そのレースで払い出された全的中票の配当合計（100円あたり配当をそのまま加算）。
特払い の場合は「全票に特払い額」が返るため、点数 × 特払い額 を回収額に加算する。
"""
import glob
import re
from collections import defaultdict

# 券種ラベル（全角）→ (キー, 1レースあたり点数)
BET_DEFS = {
    '単勝':   ('tansho',   6),
    '複勝':   ('fukusho',  6),
    '２連単': ('nirentan', 30),
    '２連複': ('nirenpuku',15),
    '拡連複': ('wide',     15),
    '３連単': ('sanrentan',120),
    '３連複': ('sanrenpuku',20),
}
ORDER = ['単勝', '複勝', '２連複', '拡連複', '２連単', '３連複', '３連単']

# 配当ペア（組合せ, 金額）を拾う。組合せは 1〜3個の艇番をハイフンで連結、金額は整数。
PAIR_RE = re.compile(r'(\d(?:-\d){0,2})\s+(\d+)')
SPECIAL_RE = re.compile(r'特払い\s*(\d+)')


def open_txt(path):
    try:
        return open(path, encoding='utf-8').read().splitlines()
    except UnicodeDecodeError:
        return open(path, encoding='cp932').read().splitlines()


def parse_file(path, stats, race_ids):
    lines = open_txt(path)
    current_bet = None      # 継続行（拡連複の2・3行目）用
    seen_races = set()      # (venue, race) は詳細ブロックで単勝が出た回数で数える
    venue = None
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # 券種ラベル行か判定
        label = None
        for lab in BET_DEFS:
            if s.startswith(lab):
                label = lab
                break
        if label:
            current_bet = label
            key, npts = BET_DEFS[label]
            body = s[len(label):]
            # 特払い
            msp = SPECIAL_RE.search(body)
            if msp:
                amt = int(msp.group(1))
                stats[key]['ret'] += npts * amt
                stats[key]['races'] += 1
                stats[key]['special'] += 1
                if label == '単勝':
                    stats['_race_count'] += 1
                continue
            pairs = PAIR_RE.findall(body)
            payout = sum(int(p) for _, p in pairs)
            stats[key]['ret'] += payout
            stats[key]['races'] += 1
            stats[key]['hits'] += len(pairs)
            if label == '単勝':
                stats['_race_count'] += 1
            continue
        # 継続行（拡連複の追加ペア）: 先頭がラベルでなく、艇番ハイフン組合せで始まる
        if current_bet == '拡連複':
            m = re.match(r'^(\d-\d)\s+(\d+)', s)
            if m:
                key = BET_DEFS['拡連複'][0]
                stats[key]['ret'] += int(m.group(2))
                stats[key]['hits'] += 1
                continue
        # それ以外の行が来たら継続状態を解除
        current_bet = None


def main():
    stats = {BET_DEFS[l][0]: defaultdict(int) for l in BET_DEFS}
    stats['_race_count'] = 0
    files = sorted(glob.glob('data/k*.txt'))
    for f in files:
        parse_file(f, stats, None)

    nrace = stats['_race_count']
    print(f"対象ファイル数: {len(files)}")
    print(f"対象レース数  : {nrace}  （detail の『単勝』出現数）")
    print()
    print(f"{'券種':<8}{'点数':>5}{'投資/R':>8}{'総投資':>14}{'総回収':>14}{'回収率':>9}{'特払':>6}")
    print('-' * 68)
    tot_inv = tot_ret = 0
    for lab in ORDER:
        key, npts = BET_DEFS[lab]
        st = stats[key]
        races = st['races']
        inv = races * npts * 100
        ret = st['ret']
        roi = ret / inv * 100 if inv else 0
        tot_inv += inv
        tot_ret += ret
        print(f"{lab:<8}{npts:>5}{npts*100:>8}{inv:>14,}{ret:>14,}{roi:>8.1f}%{st['special']:>6}")
    print('-' * 68)
    roi = tot_ret / tot_inv * 100 if tot_inv else 0
    print(f"{'合計(7種)':<7}{'':>5}{'':>8}{tot_inv:>14,}{tot_ret:>14,}{roi:>8.1f}%")


if __name__ == '__main__':
    main()
