"""说话人轮次归属：把逐字时间轴按重叠时长分配到说话人轮次并合并成块。

归属规则：
- 正时长字符按区间 [start, end) 与各轮次 [t_start, t_end) 的重叠毫秒数
  判定，归属重叠时长最大的轮次；重叠时长并列时取较早的轮次；
  与所有轮次都不重叠则归属为 null（保留为未归属块）。
- 零时长字符（start == end）按所在时点归属：时点位于某轮次区间内部归该
  轮次；恰好落在两轮次相接边界时归**右侧**轮次；落在间隙则不归属。
- 相邻且归属相同（含同为未归属）的字符合并为一个块。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right

from .errors import InvalidTimelineError
from .schemas import RevisedCharTimeline, SpeakerBlock, SpeakerTurnsRequest


def validate_speaker_turns(req: SpeakerTurnsRequest) -> None:
    """校验说话人轮次：非负、起止不倒置、不越界、按起始时间排序且互不重叠。

    前置条件：请求已通过与 /align 相同的输入校验，total_duration 非负。
    """
    total = req.total_duration
    for idx, turn in enumerate(req.speaker_turns):
        if turn.start < 0 or turn.end < 0:
            raise InvalidTimelineError(
                f"第 {idx} 个说话人轮次（speaker_id={turn.speaker_id!r}）"
                f"的起止时间不能为负数：start={turn.start}, end={turn.end}")
        if turn.end < turn.start:
            raise InvalidTimelineError(
                f"第 {idx} 个说话人轮次（speaker_id={turn.speaker_id!r}）"
                f"的结束时间 {turn.end} 不能早于起始时间 {turn.start}")
        if turn.start > total or turn.end > total:
            raise InvalidTimelineError(
                f"第 {idx} 个说话人轮次（speaker_id={turn.speaker_id!r}）"
                f"的时间 [{turn.start}, {turn.end}) 超出录音总时长 {total}")

    for idx in range(1, len(req.speaker_turns)):
        prev = req.speaker_turns[idx - 1]
        cur = req.speaker_turns[idx]
        if cur.start < prev.start:
            raise InvalidTimelineError(
                f"说话人轮次必须按起始时间排序：第 {idx - 1} 个 start="
                f"{prev.start}，第 {idx} 个 start={cur.start}")
        # 左闭右开：end == 下一个 start（端点相接）不算重叠。
        if cur.start < prev.end:
            raise InvalidTimelineError(
                f"说话人轮次时间区间不得重叠：第 {idx - 1} 个 "
                f"[{prev.start},{prev.end}) 与第 {idx} 个 "
                f"[{cur.start},{cur.end}) 重叠")


def build_speaker_blocks(
        text: str,
        chars: list[RevisedCharTimeline],
        turns: list) -> list[SpeakerBlock]:
    """把逐字时间轴归属到轮次，并合并相邻同归属字符为块。"""
    owners = _assign_owners(chars, turns)
    # 归属身份取说话人标识：相邻字符归到同一 speaker_id（含相邻轮次共用
    # 同一标识）即合并；同为未归属（None）同样合并。
    speakers = [
        turns[owner].speaker_id if owner is not None else None
        for owner in owners
    ]

    blocks: list[SpeakerBlock] = []
    run_start = 0
    for i in range(1, len(speakers) + 1):
        if i == len(speakers) or speakers[i] != speakers[run_start]:
            blocks.append(SpeakerBlock(
                speaker_id=speakers[run_start],
                start_index=run_start,
                end_index=i,
                text=text[run_start:i],
                start=chars[run_start].start,
                end=chars[i - 1].end,
            ))
            run_start = i
    return blocks


def _assign_owners(chars: list[RevisedCharTimeline],
                   turns: list) -> list[int | None]:
    """用二分查找求每个修订字符归属的轮次下标（无归属为 None）。

    轮次按时间排序且互不重叠，故每个字符只需考察与自身时间相交的连续
    轮次区间；按下标升序枚举并以严格大于更新最优，重叠并列时保留较早轮次。
    """
    starts = [t.start for t in turns]
    ends = [t.end for t in turns]
    owners: list[int | None] = []
    for ch in chars:
        s, e = ch.start, ch.end
        if s == e:
            # 唯一可能包含时点 s 的轮次：start <= s 的最后一个；
            # bisect_right 使 s 恰好等于某轮次 start 时命中右侧轮次。
            k = bisect_right(starts, s) - 1
            if k >= 0 and s < ends[k]:
                owners.append(k)
            else:
                owners.append(None)
            continue

        # 与 [s, e) 相交的轮次：end > s 且 start < e。
        left = bisect_right(ends, s)
        right = bisect_left(starts, e)
        best: int | None = None
        best_overlap = 0
        for k in range(left, right):
            overlap = min(ends[k], e) - max(starts[k], s)
            if overlap > best_overlap:  # 严格大于：并列保留更早轮次
                best, best_overlap = k, overlap
        owners.append(best)
    return owners
