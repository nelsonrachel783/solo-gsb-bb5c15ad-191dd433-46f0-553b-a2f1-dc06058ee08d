"""字幕切分：在逐字时间轴上按字符数与时长上限把修订文本切成字幕段。

切分目标（依次）：
1. 段数最少；
2. 断点总分最高——断点左字为句末标点（。！？；）加 2000 分，
   再加上相邻字停顿毫秒值（截到 0～1000）；
3. 断点下标序列字典序最小。
"""
from __future__ import annotations

from .errors import InvalidTimelineError
from .schemas import CaptionSegment, RevisedCharTimeline

_SENTENCE_END_CHARS = "。！？；"
_PUNCT_BONUS = 2000
_MAX_PAUSE_BONUS = 1000


def _breakpoint_score(text: str, chars: list[RevisedCharTimeline],
                      pos: int) -> int:
    """断点 pos 的得分：左字为 text[pos-1]，右字为 text[pos]。"""
    score = 0
    if text[pos - 1] in _SENTENCE_END_CHARS:
        score += _PUNCT_BONUS
    pause = chars[pos].start - chars[pos - 1].end
    if pause < 0:
        pause = 0
    elif pause > _MAX_PAUSE_BONUS:
        pause = _MAX_PAUSE_BONUS
    return score + pause


def build_captions(text: str, chars: list[RevisedCharTimeline],
                   max_chars: int,
                   max_duration_ms: int) -> list[CaptionSegment]:
    """把修订文本的逐字时间轴切成字幕段，按原文顺序返回。"""
    n = len(text)
    if n == 0:
        return []

    # 单字为最小分段单位；其跨度超限则不存在任何合法切分。
    for idx, ch in enumerate(chars):
        span = ch.end - ch.start
        if span > max_duration_ms:
            raise InvalidTimelineError(
                f"第 {idx} 个字符 {ch.char!r} 的跨度 {span} 毫秒超过"
                f"单段时长上限 {max_duration_ms} 毫秒，无法切分字幕")

    # 反向 DP：best_count[i] / best_score[i] 为后缀 text[i:] 的最优
    # （最少段数, 最高断点总分）。时间轴单调不回退，故每个起点 i 的合法
    # 末字 j 是连续区间，超限即可停止枚举。
    best_count = [0] * (n + 1)
    best_score = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        seg_start = chars[i].start
        count: int | None = None
        score = 0
        for j in range(i, min(n, i + max_chars)):
            if chars[j].end - seg_start > max_duration_ms:
                break
            nxt = j + 1
            cand_count = 1 + best_count[nxt]
            cand_score = best_score[nxt]
            if nxt < n:
                cand_score += _breakpoint_score(text, chars, nxt)
            if (count is None or cand_count < count
                    or (cand_count == count and cand_score > score)):
                count, score = cand_count, cand_score
        # 单字必合法（前面已校验），循环至少执行一次。
        assert count is not None
        best_count[i] = count
        best_score[i] = score

    # 重建：每步在满足（段数, 总分）最优的末字中取最小断点下标，
    # 即得字典序最小的断点序列。
    segments: list[CaptionSegment] = []
    i = 0
    while i < n:
        seg_start = chars[i].start
        end = i
        for j in range(i, min(n, i + max_chars)):
            if chars[j].end - seg_start > max_duration_ms:
                break
            nxt = j + 1
            cand_score = best_score[nxt]
            if nxt < n:
                cand_score += _breakpoint_score(text, chars, nxt)
            if (1 + best_count[nxt] == best_count[i]
                    and cand_score == best_score[i]):
                end = j
                break
        segments.append(CaptionSegment(
            start_index=i,
            end_index=end + 1,
            text=text[i:end + 1],
            start=seg_start,
            end=chars[end].end,
        ))
        i = end + 1
    return segments
