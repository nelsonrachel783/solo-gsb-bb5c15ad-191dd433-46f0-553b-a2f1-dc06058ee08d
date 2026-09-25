"""纯 Python 全局最小编辑代价对齐（与 C 内核算法、同分规则完全一致）。

步骤：
  1) 前向两行 DP 求最小编辑代价 E；
  2) 反向 DP 求后缀编辑距离 B（用 array('H') 即 uint16 紧凑存储）；
  3) 自 (0,0) 可行性贪心游走：能匹配/替换则匹配（优先较早原位置），
     否则尝试新增（保留识别字符），最后删除。

当共享库不可用（如无编译器的精简镜像）时作为后备实现。
"""
from __future__ import annotations

from array import array

_UINT16_MAX = 65535


def align_chars(source: str, target: str) -> list[int]:
    """返回 target 每个字符对齐到的 source 下标；-1 表示新增字符。"""
    m = len(source)
    n = len(target)
    width = n + 1

    if m + n > _UINT16_MAX - 1:  # 由上层限额提前拦截，这里兜底
        raise ValueError("输入过长，超出 Python 后备内核容量")

    # ---- 1) 前向两行 DP ----
    prev = list(range(width))
    cur = [0] * width
    for i in range(1, m + 1):
        cur[0] = i
        sc = source[i - 1]
        for j in range(1, width):
            d = prev[j - 1] + (0 if sc == target[j - 1] else 1)
            ins = cur[j - 1] + 1
            dele = prev[j] + 1
            best = d
            if ins < best:
                best = ins
            if dele < best:
                best = dele
            cur[j] = best
        prev, cur = cur, prev
    optimal = prev[n]

    if m == 0:
        return [-1] * n

    # ---- 2) 反向后缀编辑距离 B（扁平 uint16 数组，按行存储）----
    b = array('H', [0]) * ((m + 1) * width)

    def bidx(i: int, j: int) -> int:
        return i * width + j

    for j in range(width):
        b[bidx(m, j)] = n - j
    for i in range(m + 1):
        b[bidx(i, n)] = m - i
    for i in range(m - 1, -1, -1):
        row = i * width
        nxt = row + width
        sc = source[i]
        for j in range(n - 1, -1, -1):
            d = int(b[nxt + j + 1]) + (0 if sc == target[j] else 1)
            ins = int(b[row + j + 1]) + 1
            dele = int(b[nxt + j]) + 1
            best = d
            if ins < best:
                best = ins
            if dele < best:
                best = dele
            b[row + j] = best

    # ---- 3) 可行性贪心游走 ----
    out = [-1] * n
    i = j = 0
    spent = 0
    while i < m or j < n:
        if i < m and j < n:
            cd = 0 if source[i] == target[j] else 1
            diag_ok = spent + cd + int(b[(i + 1) * width + (j + 1)]) == optimal
        else:
            cd = 1
            diag_ok = False
        if diag_ok:
            out[j] = i
            spent += cd
            i += 1
            j += 1
        elif j < n and spent + 1 + int(b[i * width + (j + 1)]) == optimal:
            spent += 1
            j += 1               # 新增
        else:
            spent += 1
            i += 1               # 删除
    return out
