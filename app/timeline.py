"""时间轴构建：校验输入、全局对齐、并为新增字符在空档内均匀分配时间。"""
from __future__ import annotations

from .alignment import align_chars
from .errors import InvalidTimelineError
from .schemas import AlignRequest, RevisedCharTimeline


def validate_request(req: AlignRequest, max_recognized: int,
                     max_revised: int) -> None:
    """校验输入参数与时间轴；无效时抛出 InvalidTimelineError（明确中文信息）。"""
    if req.total_duration < 0:
        raise InvalidTimelineError(
            f"录音总时长 total_duration 必须为非负整数，当前为 {req.total_duration}")

    if len(req.recognized) > max_recognized:
        raise InvalidTimelineError(
            f"识别字符数量 {len(req.recognized)} 超过上限 {max_recognized}，"
            "可通过环境变量 MAX_RECOGNIZED_CHARS 调整")
    if len(req.revised_text) > max_revised:
        raise InvalidTimelineError(
            f"修订文本长度 {len(req.revised_text)} 超过上限 {max_revised}，"
            "可通过环境变量 MAX_REVISED_LENGTH 调整")

    for idx, item in enumerate(req.recognized):
        if len(item.char) != 1:
            raise InvalidTimelineError(
                f"第 {idx} 项识别字符必须且只能包含 1 个字符，当前为 {item.char!r}")
        if item.start < 0 or item.end < 0:
            raise InvalidTimelineError(
                f"第 {idx} 项字符 {item.char!r} 的起止时间不能为负数："
                f"start={item.start}, end={item.end}")
        if item.end < item.start:
            raise InvalidTimelineError(
                f"第 {idx} 项字符 {item.char!r} 的结束时间 {item.end} 不能早于"
                f"起始时间 {item.start}")
        if item.start > req.total_duration or item.end > req.total_duration:
            raise InvalidTimelineError(
                f"第 {idx} 项字符 {item.char!r} 的时间 [{item.start}, {item.end}] "
                f"超出录音总时长 {req.total_duration}")
        if idx > 0:
            prev = req.recognized[idx - 1]
            if item.start < prev.start:
                raise InvalidTimelineError(
                    f"识别字符必须按起始时间排序：第 {idx - 1} 项 start={prev.start}，"
                    f"第 {idx} 项 start={item.start}")
            if item.start < prev.end:
                raise InvalidTimelineError(
                    f"识别字符时间区间不得重叠：第 {idx - 1} 项 "
                    f"[{prev.start},{prev.end}] 与第 {idx} 项 "
                    f"[{item.start},{item.end}] 重叠")

    # 录音时长为 0 时不允许出现带正时长的字符（边界 0..0 仍合法）。
    if req.total_duration == 0 and any(
            (it.start != 0 or it.end != 0) for it in req.recognized):
        raise InvalidTimelineError(
            "录音总时长为 0 时，所有识别字符时间必须均为 0")


def _even_points(start: int, end: int, count: int) -> list[tuple[int, int]]:
    """在整数区间 [start, end] 内为 count 个相邻字符均匀切分（端点固定）。

    使用地板法取分界点，天然满足 start <= 各分界点 <= end 且非递减。
    """
    if count <= 0:
        return []
    if end == start:
        return [(start, end)] * count
    points = [start + ((end - start) * k // count) for k in range(count + 1)]
    return [(points[k], points[k + 1]) for k in range(count)]


def build_timeline(req: AlignRequest, backend: str) -> dict:
    """执行对齐并生成逐字时间轴。"""
    source = "".join(item.char for item in req.recognized)
    target = req.revised_text
    total = req.total_duration

    mapping = align_chars(source, target)

    # 锚点：对齐到识别字符（完全匹配或替换纠错）的修订字符。
    anchors: dict[int, int] = {}  # 修订下标 -> 识别下标
    for j, src_idx in enumerate(mapping):
        if src_idx >= 0:
            anchors[j] = src_idx

    matched_count = len(anchors)
    inserted_count = len(target) - matched_count
    deleted_count = len(source) - matched_count

    chars: list[RevisedCharTimeline] = []
    anchor_positions = sorted(anchors)

    for j, ch in enumerate(target):
        src_idx = anchors.get(j)
        if src_idx is not None:
            item = req.recognized[src_idx]
            chars.append(RevisedCharTimeline(
                char=ch,
                start=item.start,
                end=item.end,
                source="matched",
                matched_index=src_idx,
                recognized_char=item.char,
            ))
            continue

        # 找到前后相邻锚点，确定该新增字符所处空档的边界。
        prev_pos = next((p for p in reversed(anchor_positions) if p < j), None)
        next_pos = next((p for p in anchor_positions if p > j), None)

        if prev_pos is not None and next_pos is not None:
            gap_left = req.recognized[anchors[prev_pos]].end
            gap_right = req.recognized[anchors[next_pos]].start
        elif prev_pos is not None:
            gap_left = req.recognized[anchors[prev_pos]].end
            gap_right = total
        elif next_pos is not None:
            gap_left = 0
            gap_right = req.recognized[anchors[next_pos]].start
        else:
            gap_left, gap_right = 0, total

        # 空档内连续新增字符的数量及当前字符序号。
        run = j - (prev_pos if prev_pos is not None else -1)
        run_total = (next_pos if next_pos is not None else len(target)) - (
            prev_pos if prev_pos is not None else -1) - 1
        seg = _even_points(gap_left, gap_right, run_total)[run - 1]

        chars.append(RevisedCharTimeline(
            char=ch,
            start=seg[0],
            end=seg[1],
            source="computed",
        ))

    return {
        "total_duration": total,
        "chars": chars,
        "inserted_count": inserted_count,
        "deleted_count": deleted_count,
        "matched_count": matched_count,
        "backend": backend,
    }
