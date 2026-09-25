"""在修订文本中查找与引语编辑距离不超过阈值的连续片段。"""
from __future__ import annotations

from array import array


class _SuffixLCP:
    """后缀数组 + Kasai LCP + RMQ，用于 O(1) 查询两个后缀的最长公共前缀。"""

    __slots__ = ("rank", "tables")

    def __init__(self, pattern: str, text: str):
        # -1 为两个串之间的边界，-2 为结束边界，-3 表示后缀越界。
        values = (
            [ord(ch) for ch in pattern]
            + [-1]
            + [ord(ch) for ch in text]
            + [-2]
        )
        size = len(values)
        suffixes = list(range(size))
        rank = values[:]
        gap = 1

        while True:
            suffixes.sort(
                key=lambda i: (
                    rank[i],
                    rank[i + gap] if i + gap < size else -3,
                )
            )
            new_rank = [0] * size
            for pos in range(1, size):
                prev = suffixes[pos - 1]
                cur = suffixes[pos]
                prev_key = (
                    rank[prev],
                    rank[prev + gap] if prev + gap < size else -3,
                )
                cur_key = (
                    rank[cur],
                    rank[cur + gap] if cur + gap < size else -3,
                )
                new_rank[cur] = new_rank[prev] + (
                    1 if cur_key != prev_key else 0)

            rank = new_rank
            if rank[suffixes[-1]] == size - 1:
                break
            gap <<= 1

        self.rank = rank

        # Kasai 算法构造相邻后缀 LCP；lcp[i] 表示第 i、i+1 名后缀的 LCP。
        adjacent = [0] * (size - 1)
        common = 0
        for i in range(size):
            r = rank[i]
            if r == 0:
                common = 0
                continue
            j = suffixes[r - 1]
            while (
                i + common < size
                and j + common < size
                and values[i + common] == values[j + common]
            ):
                common += 1
            adjacent[r - 1] = common
            if common:
                common -= 1

        # 稀疏表，RMQ 取两个后缀排名之间的最小相邻 LCP。
        tables = [array("i", adjacent)]
        offset = 1
        while len(tables[-1]) > offset:
            prev = tables[-1]
            current_len = len(prev) - offset
            current = array(
                "i",
                (min(prev[i], prev[i + offset]) for i in range(current_len)),
            )
            tables.append(current)
            offset <<= 1
        self.tables = tables

    def lcp(self, first: int, second: int) -> int:
        """返回两个后缀位置的公共前缀长度。"""
        r1 = self.rank[first]
        r2 = self.rank[second]
        if r1 > r2:
            r1, r2 = r2, r1

        left, right = r1, r2 - 1
        length = right - left + 1
        level = length.bit_length() - 1
        table = self.tables[level]
        offset = 1 << level
        return min(table[left], table[right - offset + 1])


def find_quote_hits(quote: str, revised_text: str,
                    max_edits: int) -> list[tuple[int, int, int]]:
    """寻找全局最优的互不重叠近似引语命中。

    返回 ``(起始下标, 结束下标, 编辑次数)``，结束下标为半开区间。
    依次最大化命中数、最小化编辑次数总和；再按 (起始, 结束) 序列取字典序最小。
    """
    pattern_len = len(quote)
    text_len = len(revised_text)
    min_fragment_len = max(1, pattern_len - max_edits)
    max_fragment_len = pattern_len + max_edits

    if text_len == 0 or pattern_len > text_len + max_edits:
        return []

    text_start = pattern_len + 1  # 组合序列：pattern、分隔符、text、结束符
    suffix_lcp = _SuffixLCP(quote, revised_text)

    def distance(pattern_pos: int, text_pos: int, budget: int,
                 fragment_end: int) -> int | None:
        """返回从当前状态对齐到片段末尾后，最多还能剩余多少预算。"""
        common = suffix_lcp.lcp(pattern_pos, text_start + text_pos)
        common = min(
            common,
            pattern_len - pattern_pos,
            fragment_end - text_pos,
        )
        pattern_pos += common
        text_pos += common

        if pattern_pos == pattern_len and text_pos == fragment_end:
            return budget

        remaining_pattern = pattern_len - pattern_pos
        remaining_text = fragment_end - text_pos
        if abs(remaining_pattern - remaining_text) > budget:
            return None
        if budget == 0:
            return None

        best: int | None = None

        def consider(value: int | None) -> None:
            nonlocal best
            if value is not None and (best is None or value > best):
                best = value

        # 替换：消耗引语字符和文本字符各一个。
        if pattern_pos < pattern_len and text_pos < fragment_end:
            consider(distance(pattern_pos + 1, text_pos + 1, budget - 1,
                              fragment_end))

        # 删除：引语字符不出现在文本片段中。
        if pattern_pos < pattern_len:
            consider(distance(pattern_pos + 1, text_pos, budget - 1,
                              fragment_end))

        # 插入：文本片段中多消耗一个字符。
        if text_pos < fragment_end:
            consider(distance(pattern_pos, text_pos + 1, budget - 1,
                              fragment_end))

        return best

    # 每个起点对应的候选：(终点, 实际编辑次数)。
    candidates: list[list[tuple[int, int]]] = [[] for _ in range(text_len)]
    for end in range(1, text_len + 1):
        high_len = min(max_fragment_len, end)
        for length in range(min_fragment_len, high_len + 1):
            start = end - length
            remaining_edits = distance(0, start, max_edits, end)
            if remaining_edits is not None:
                candidates[start].append((end, max_edits - remaining_edits))

    # 加权区间调度 DP。命中片段使用半开区间，首尾相接不算重叠。
    hit_count = [0] * (text_len + 1)
    edit_sum = [0] * (text_len + 1)
    first_start = [text_len + 1] * (text_len + 1)
    first_end = [text_len + 1] * (text_len + 1)

    for start in range(text_len - 1, -1, -1):
        hit_count[start] = hit_count[start + 1]
        edit_sum[start] = edit_sum[start + 1]
        first_start[start] = first_start[start + 1]
        first_end[start] = first_end[start + 1]

        for end, edits in candidates[start]:
            candidate_count = 1 + hit_count[end]
            candidate_cost = edits + edit_sum[end]
            candidate_key = (candidate_count, -candidate_cost,
                             -start, -end)
            current_key = (
                hit_count[start], -edit_sum[start],
                -first_start[start], -first_end[start],
            )
            if candidate_key > current_key:
                hit_count[start] = candidate_count
                edit_sum[start] = candidate_cost
                first_start[start] = start
                first_end[start] = end

    hits: list[tuple[int, int, int]] = []
    pos = 0
    while pos < text_len and hit_count[pos] > 0:
        moved = False
        for end, edits in candidates[pos]:
            if (
                1 + hit_count[end] == hit_count[pos]
                and edits + edit_sum[end] == edit_sum[pos]
            ):
                hits.append((pos, end, edits))
                pos = end
                moved = True
                break
        if not moved:
            pos += 1

    return hits
