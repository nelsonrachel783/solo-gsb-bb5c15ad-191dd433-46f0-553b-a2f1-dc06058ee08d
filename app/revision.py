"""修订差异：把全局对齐结果整理成人工修订相对识别稿的有序改动块。

规则：
- 未匹配的识别字符 → 删除；
- 无来源（computed）的修订字符 → 插入；
- 匹配但来源字符不同 → 替换；
- 以未改动的匹配字符为边界，把连续改动合并为一块；
- 块内同时出现识别侧与修订侧文字（替换或插删混合）时类型为 replace。
"""
from __future__ import annotations

from .schemas import RecognizedChar, RevisedCharTimeline, RevisionBlock


def build_revision_diff(
        recognized: list[RecognizedChar],
        chars: list[RevisedCharTimeline]) -> list[RevisionBlock]:
    """按原文顺序返回改动块；无改动时返回空列表。"""
    # 边界对 (识别下标, 修订下标)：未改动的匹配字符；末尾加哨兵。
    boundaries = [
        (item.matched_index, j)
        for j, item in enumerate(chars)
        if item.source == "matched" and item.recognized_char == item.char
    ]
    boundaries.append((len(recognized), len(chars)))

    blocks: list[RevisionBlock] = []
    prev_i = prev_j = -1
    for bound_i, bound_j in boundaries:
        rec_start, rec_end = prev_i + 1, bound_i
        rev_start, rev_end = prev_j + 1, bound_j
        if rec_start < rec_end or rev_start < rev_end:
            blocks.append(_build_block(
                recognized, chars, rec_start, rec_end, rev_start, rev_end))
        prev_i, prev_j = bound_i, bound_j
    return blocks


def _build_block(
        recognized: list[RecognizedChar],
        chars: list[RevisedCharTimeline],
        rec_start: int, rec_end: int,
        rev_start: int, rev_end: int) -> RevisionBlock:
    if rec_start == rec_end:
        block_type = "insert"
    elif rev_start == rev_end:
        block_type = "delete"
    else:
        block_type = "replace"

    # 块时间：涉及的原识别字符与修订时间轴字符的最早起点、最晚终点。
    starts = [it.start for it in recognized[rec_start:rec_end]]
    ends = [it.end for it in recognized[rec_start:rec_end]]
    starts.extend(c.start for c in chars[rev_start:rev_end])
    ends.extend(c.end for c in chars[rev_start:rev_end])

    return RevisionBlock(
        type=block_type,
        recognized_start=rec_start,
        recognized_end=rec_end,
        revised_start=rev_start,
        revised_end=rev_end,
        recognized_text="".join(
            it.char for it in recognized[rec_start:rec_end]),
        revised_text="".join(c.char for c in chars[rev_start:rev_end]),
        start=min(starts),
        end=max(ends),
    )
