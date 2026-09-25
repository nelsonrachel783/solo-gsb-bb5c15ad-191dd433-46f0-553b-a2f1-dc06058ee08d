"""自定义错误：携带面向调用方的明确中文提示。"""
from __future__ import annotations


class InvalidTimelineError(ValueError):
    """输入时间轴/参数无效。"""
