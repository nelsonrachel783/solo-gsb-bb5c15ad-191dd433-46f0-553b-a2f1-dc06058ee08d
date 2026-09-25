"""实体共现：精确匹配别名、全局选择互不重叠命中、按时间桶统计共现边。

命中选择目标（依次）：
1. 覆盖字符数最多；
2. 命中数最少；
3. 按（起点、终点、实体 ID）排序的命中序列字典序最小。

时间桶：命中首字时间轴 start 整除 bucket_ms 归桶；同一桶内同一实体只计
一次；每对实体共同出现的桶数作为边权，只保留非零边。
"""
from __future__ import annotations

from .errors import InvalidTimelineError
from .schemas import (
    Entity,
    EntityLinkEdge,
    EntityLinksRequest,
    EntityLinksResponse,
    EntityMention,
    RevisedCharTimeline,
)


def validate_entities(req: EntityLinksRequest) -> None:
    """校验实体 ID 唯一、别名全局不重复（数量与格式由模型校验）。"""
    seen_ids: dict[str, int] = {}
    for idx, entity in enumerate(req.entities):
        first = seen_ids.get(entity.entity_id)
        if first is not None:
            raise InvalidTimelineError(
                f"实体 ID 必须唯一：第 {first} 个与第 {idx} 个实体的 "
                f"entity_id 均为 {entity.entity_id!r}")
        seen_ids[entity.entity_id] = idx

    seen_aliases: dict[str, str] = {}
    for entity in req.entities:
        for alias in entity.aliases:
            owner = seen_aliases.get(alias)
            if owner is not None:
                raise InvalidTimelineError(
                    f"别名必须全局不重复：{alias!r} 同时出现在实体 "
                    f"{owner!r} 与 {entity.entity_id!r} 的别名中")
            seen_aliases[alias] = entity.entity_id


def find_entity_mentions(
        text: str, entities: list[Entity]) -> list[tuple[int, int, str]]:
    """在修订文本中逐字精确匹配别名，选出全局最优的互不重叠命中。

    返回 ``(起始下标, 结束下标, 实体 ID)``，结束下标为半开区间；依次最大化
    覆盖字符数、最小化命中数，再按（起点、终点、实体 ID）序列取字典序最小。
    """
    n = len(text)
    # 每个起点对应的候选：(终点, 实体 ID)。别名全局不重复，同一片段至多
    # 匹配一个别名，故 (起点, 终点) 唯一确定候选。
    candidates: list[list[tuple[int, str]]] = [[] for _ in range(n)]
    for entity in entities:
        for alias in entity.aliases:
            pos = text.find(alias)
            while pos >= 0:
                candidates[pos].append((pos + len(alias), entity.entity_id))
                pos = text.find(alias, pos + 1)  # 允许重叠出现，均为候选
    for same_start in candidates:
        same_start.sort()

    # 加权区间调度 DP。命中片段使用半开区间，首尾相接不算重叠。
    covered = [0] * (n + 1)
    hit_count = [0] * (n + 1)
    first_start = [n + 1] * (n + 1)
    first_end = [n + 1] * (n + 1)

    for start in range(n - 1, -1, -1):
        covered[start] = covered[start + 1]
        hit_count[start] = hit_count[start + 1]
        first_start[start] = first_start[start + 1]
        first_end[start] = first_end[start + 1]

        for end, _eid in candidates[start]:
            cand_covered = end - start + covered[end]
            cand_hits = 1 + hit_count[end]
            cand_key = (cand_covered, -cand_hits, -start, -end)
            cur_key = (covered[start], -hit_count[start],
                       -first_start[start], -first_end[start])
            if cand_key > cur_key:
                covered[start] = cand_covered
                hit_count[start] = cand_hits
                first_start[start] = start
                first_end[start] = end

    mentions: list[tuple[int, int, str]] = []
    pos = 0
    while pos < n and hit_count[pos] > 0:
        moved = False
        for end, eid in candidates[pos]:
            if (end - pos + covered[end] == covered[pos]
                    and 1 + hit_count[end] == hit_count[pos]):
                mentions.append((pos, end, eid))
                pos = end
                moved = True
                break
        if not moved:
            pos += 1
    return mentions


def build_entity_links(
        req: EntityLinksRequest,
        chars: list[RevisedCharTimeline]) -> EntityLinksResponse:
    """生成实体命中列表与按时间桶加权的共现边。"""
    mentions = [
        EntityMention(
            entity_id=eid,
            start_index=start,
            end_index=end,
            start=chars[start].start,
            end=chars[end - 1].end,
        )
        for start, end, eid in find_entity_mentions(
            req.revised_text, req.entities)
    ]

    # 按命中首字 start 整除 bucket_ms 归桶；同桶同一实体只计一次。
    bucket_entities: dict[int, set[str]] = {}
    for mention in mentions:
        bucket = mention.start // req.bucket_ms
        bucket_entities.setdefault(bucket, set()).add(mention.entity_id)

    edge_buckets: dict[tuple[str, str], list[int]] = {}
    for bucket in sorted(bucket_entities):
        ids = sorted(bucket_entities[bucket])
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                edge_buckets.setdefault((ids[i], ids[j]), []).append(bucket)

    edges = [
        EntityLinkEdge(entity_a=a, entity_b=b,
                       weight=len(buckets), buckets=buckets)
        for (a, b), buckets in sorted(edge_buckets.items())
    ]
    return EntityLinksResponse(mentions=mentions, edges=edges)
