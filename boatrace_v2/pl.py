# -*- coding: utf-8 -*-
"""Plackett-Luce 展開（strength → 2連単/3連単の組合せ確率）。"""


def pl_top(strengths, kind, topk):
    """strengths（枠1..6 の順・正の値）から上位 topk の [(combo, p)] を返す。"""
    idx = [i for i in range(len(strengths)) if strengths[i] and strengths[i] > 0]
    tot = sum(strengths[i] for i in idx)
    combos = []
    if kind == 2:
        for i in idx:
            for j in idx:
                if j == i:
                    continue
                p = (strengths[i] / tot) * (strengths[j] / (tot - strengths[i]))
                combos.append(((i + 1, j + 1), p))
    else:
        for i in idx:
            for j in idx:
                if j == i:
                    continue
                rem = tot - strengths[i]
                pij = (strengths[i] / tot) * (strengths[j] / rem)
                for k in idx:
                    if k in (i, j):
                        continue
                    combos.append(((i + 1, j + 1, k + 1),
                                   pij * strengths[k] / (rem - strengths[j])))
    combos.sort(key=lambda x: -x[1])
    return combos[:topk]
