"""采访逐字稿校时 API。"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import alignment
from .captions import build_captions
from .entity_links import build_entity_links, validate_entity_links
from .errors import InvalidTimelineError
from .schemas import (
    AlignRequest,
    AlignResponse,
    CaptionSegment,
    CaptionsRequest,
    EntityLinksRequest,
    EntityLinksResponse,
    HealthResponse,
    LocateQuoteRequest,
    QuoteHit,
    RetimeRequest,
    RevisionBlock,
    SpeakerBlock,
    SpeakerTurnsRequest,
)
from .quote import find_quote_hits
from .retime import apply_calibrations, validate_calibrations
from .revision import build_revision_diff
from .speaker import build_speaker_blocks, validate_speaker_turns
from .timeline import build_timeline, validate_request


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


MAX_RECOGNIZED_CHARS = _env_int("MAX_RECOGNIZED_CHARS", 20000)
MAX_REVISED_LENGTH = _env_int("MAX_REVISED_LENGTH", 20000)
MAX_QUOTE_LENGTH = _env_int("MAX_QUOTE_LENGTH", 20000)

app = FastAPI(
    title="采访逐字稿校时 API",
    description=(
        "按最少编辑代价将人工修订文本与按时间排序的识别字符全局对齐，"
        "为每个修订字符生成起止毫秒时间轴。"
    ),
    version="1.0.0",
)


@app.exception_handler(InvalidTimelineError)
async def invalid_timeline_handler(_request: Request,
                                   exc: InvalidTimelineError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_handler(_request: Request,
                             exc: RequestValidationError) -> JSONResponse:
    """把 Pydantic 校验错误转成明确的中文提示。"""
    messages = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if p != "body") or "请求体"
        etype = err["type"]
        if etype == "missing":
            messages.append(f"缺少必填字段：{loc}")
        elif etype in ("int_type", "int_parsing"):
            if loc.endswith(("max_edits", "max_chars")):
                messages.append(f"字段 {loc} 必须是整数")
            else:
                messages.append(f"字段 {loc} 必须是整数毫秒时间戳")
        elif etype == "string_type":
            messages.append(f"字段 {loc} 必须是字符串")
        elif etype == "string_too_short":
            messages.append(f"字段 {loc} 必须是非空字符串")
        elif etype in ("greater_than_equal", "less_than_equal"):
            if loc.endswith("max_edits"):
                range_hint = "，允许范围为 0～2"
            elif loc.endswith("max_chars"):
                range_hint = "，允许范围为 1～80"
            elif loc.endswith("max_duration_ms"):
                range_hint = "，允许范围为 500～30000"
            elif loc.endswith("bucket_ms"):
                range_hint = "，允许范围为 1000～60000"
            else:
                range_hint = ""
            messages.append(f"字段 {loc} 无效{range_hint}")
        elif etype in ("too_short", "too_long"):
            if loc.endswith("calibrations"):
                messages.append("校准点数量无效，允许范围为 1～20 个")
            elif loc.endswith("entities"):
                messages.append("实体数量无效，允许范围为 2～20 个")
            elif loc.endswith("aliases"):
                messages.append("每个实体的别名数量无效，允许范围为 1～5 个")
            else:
                messages.append(f"字段 {loc} 无效")
        elif etype == "extra_forbidden":
            messages.append(f"存在不允许的字段：{loc}")
        else:
            messages.append(f"字段 {loc} 无效：{err['msg']}")
    detail = "；".join(messages) if messages else "请求参数无效"
    return JSONResponse(status_code=422, content={"detail": detail})


@app.get("/health", response_model=HealthResponse, tags=["健康检查"])
async def health() -> HealthResponse:
    """健康检查。"""
    return HealthResponse(status="ok", backend=alignment.backend_name())


@app.post("/align", response_model=AlignResponse, tags=["校时对齐"])
async def align(req: AlignRequest) -> AlignResponse:
    """为人工修订文本生成逐字时间轴。"""
    validate_request(req, MAX_RECOGNIZED_CHARS, MAX_REVISED_LENGTH)
    result = build_timeline(req, alignment.backend_name())
    return AlignResponse(**result)


@app.post("/retime", response_model=AlignResponse, tags=["校时对齐"])
async def retime(req: RetimeRequest) -> AlignResponse:
    """按校准点对逐字时间轴做分段线性重映射。"""
    validate_request(req, MAX_RECOGNIZED_CHARS, MAX_REVISED_LENGTH)
    validate_calibrations(req)
    result = build_timeline(req, alignment.backend_name())
    result["chars"] = apply_calibrations(req, result["chars"])
    return AlignResponse(**result)


@app.post(
    "/locate-quote",
    response_model=list[QuoteHit],
    tags=["校时对齐"],
)
async def locate_quote(req: LocateQuoteRequest) -> list[QuoteHit]:
    """沿修订文本时间轴定位与引语近似匹配的互不重叠片段。"""
    validate_request(req, MAX_RECOGNIZED_CHARS, MAX_REVISED_LENGTH)
    if len(req.quote) > MAX_QUOTE_LENGTH:
        raise InvalidTimelineError(
            f"引语长度 {len(req.quote)} 超过上限 {MAX_QUOTE_LENGTH}，"
            "可通过环境变量 MAX_QUOTE_LENGTH 调整")

    timeline = build_timeline(req, alignment.backend_name())
    hits = find_quote_hits(req.quote, req.revised_text, req.max_edits)
    return [
        QuoteHit(
            start_index=start,
            end_index=end,
            text=req.revised_text[start:end],
            edits=edits,
            start=timeline["chars"][start].start,
            end=timeline["chars"][end - 1].end,
        )
        for start, end, edits in hits
    ]


@app.post(
    "/revision-diff",
    response_model=list[RevisionBlock],
    tags=["校时对齐"],
)
async def revision_diff(req: AlignRequest) -> list[RevisionBlock]:
    """输出人工修订相对识别稿的有序改动块。"""
    validate_request(req, MAX_RECOGNIZED_CHARS, MAX_REVISED_LENGTH)
    timeline = build_timeline(req, alignment.backend_name())
    return build_revision_diff(req.recognized, timeline["chars"])


@app.post(
    "/speaker-turns",
    response_model=list[SpeakerBlock],
    tags=["校时对齐"],
)
async def speaker_turns(req: SpeakerTurnsRequest) -> list[SpeakerBlock]:
    """把修订文本逐字归属到说话人轮次，并合并相邻同归属字符为块。"""
    validate_request(req, MAX_RECOGNIZED_CHARS, MAX_REVISED_LENGTH)
    validate_speaker_turns(req)
    timeline = build_timeline(req, alignment.backend_name())
    return build_speaker_blocks(
        req.revised_text, timeline["chars"], req.speaker_turns)


@app.post(
    "/captions",
    response_model=list[CaptionSegment],
    tags=["字幕切分"],
)
async def captions(req: CaptionsRequest) -> list[CaptionSegment]:
    """沿修订文本时间轴切分字幕段：段数最少，再最大化断点得分。"""
    validate_request(req, MAX_RECOGNIZED_CHARS, MAX_REVISED_LENGTH)
    timeline = build_timeline(req, alignment.backend_name())
    return build_captions(
        req.revised_text, timeline["chars"], req.max_chars, req.max_duration_ms)


@app.post(
    "/entity-links",
    response_model=EntityLinksResponse,
    tags=["实体共提及"],
)
async def entity_links(req: EntityLinksRequest) -> EntityLinksResponse:
    """按别名精确命中与首字时间桶，生成采访实体的共提及关系。"""
    validate_request(req, MAX_RECOGNIZED_CHARS, MAX_REVISED_LENGTH)
    validate_entity_links(req)
    timeline = build_timeline(req, alignment.backend_name())
    return build_entity_links(
        req.revised_text, timeline["chars"], req.entities, req.bucket_ms)
