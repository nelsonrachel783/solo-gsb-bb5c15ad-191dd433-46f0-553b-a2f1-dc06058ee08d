"""按校准点对逐字时间轴做分段线性重映射（旧时间 → 新时间）。"""
from __future__ import annotations

from bisect import bisect_right

from .errors import InvalidTimelineError
from .schemas import RetimeRequest, RevisedCharTimeline


def validate_calibrations(req: RetimeRequest) -> None:
    """校验校准点：数量、取值范围与两列时间的严格递增。

    前置条件：请求已通过与 /align 相同的输入校验，total_duration 非负。
    """
    total = req.total_duration
    for idx, point in enumerate(req.calibrations):
        if not (0 < point.at_ms < total) or not (0 < point.corrected_ms < total):
            raise InvalidTimelineError(
                f"第 {idx} 个校准点的时间必须严格位于开区间 "
                f"(0, total_duration)=(0, {total}) 内："
                f"at_ms={point.at_ms}, corrected_ms={point.corrected_ms}")

    for idx in range(1, len(req.calibrations)):
        prev, cur = req.calibrations[idx - 1], req.calibrations[idx]
        if cur.at_ms <= prev.at_ms:
            raise InvalidTimelineError(
                f"校准点的 at_ms 必须严格递增：第 {idx - 1} 个为 {prev.at_ms}，"
                f"第 {idx} 个为 {cur.at_ms}")
        if cur.corrected_ms <= prev.corrected_ms:
            raise InvalidTimelineError(
                f"校准点的 corrected_ms 必须严格递增：第 {idx - 1} 个为 "
                f"{prev.corrected_ms}，第 {idx} 个为 {cur.corrected_ms}")


def _remap(t: int, at: list[int], corrected: list[int], total: int) -> int:
    """分段线性映射旧时间 t 到新时间，向下取整；录音首尾固定为 0、total。

    at/corrected 含隐式端点 (0, 0) 与 (total, total)。所有运算均为整数运算，
    校准点（含端点）所在位置必然精确命中，无浮点误差。
    """
    if t <= 0:
        return 0
    if t >= total:
        return total
    # 右端点已在 at 中，故 j 一定落在含 t 的段 [at[j-1], at[j]] 上。
    j = bisect_right(at, t)
    left_at, right_at = at[j - 1], at[j]
    left_new, right_new = corrected[j - 1], corrected[j]
    return left_new + (right_new - left_new) * (t - left_at) // (
        right_at - left_at)


def apply_calibrations(req: RetimeRequest,
                       chars: list[RevisedCharTimeline]
                       ) -> list[RevisedCharTimeline]:
    """对逐字时间轴逐字重映射，保留文字、来源与匹配下标。"""
    total = req.total_duration
    at = [0] + [p.at_ms for p in req.calibrations] + [total]
    corrected = [0] + [p.corrected_ms for p in req.calibrations] + [total]

    result: list[RevisedCharTimeline] = []
    for ch in chars:
        result.append(ch.model_copy(update={
            "start": _remap(ch.start, at, corrected, total),
            "end": _remap(ch.end, at, corrected, total),
        }))
    return result
