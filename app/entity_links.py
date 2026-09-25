"""实体共提及：别名精确命中的全局最优选择、时间桶归集与共现边权计算。

命中选择目标（依次）：
1. 覆盖字符数最多；
2. 命中数最少；
3. 按（起点、终点、实体 ID）排序的命中序列字典序最小。

归桶与边权：每个命中按首字 start 整除 bucket_ms 归入时间桶，同桶同一实体
只计一次；每对实体共同出现的桶数即为边权，只返回边权非零的边。
"""
from __future__ import annotations

from itertools import combinations

from .errors import InvalidTimelineError
from .schemas import (
    EntityLinkEdge,
    EntityLinkHit,
    EntityLinksRequest,
    EntityLinksResponse,
    EntitySpec,
    RevisedCharTimeline,
)


def validate_entity_links(req: EntityLinksRequest) -> None:
    """校验实体定义：实体 ID 唯一、别名全局不重复。

    前置条件：请求已通过 Pydantic 校验（实体 2～20 个、别名 1～5 个且非空）。
    """
    seen_ids: set[str] = set()
    seen_aliases: dict[str, str] = {}  # 别名 -> 首次出现的实体 ID
    for entity in req.entities:
        if entity.entity_id in seen_ids:
            raise InvalidTimelineError(
                f"实体 ID 必须唯一：{entity.entity_id!r} 重复出现")
        seen_ids.add(entity.entity_id)
        for alias in entity.aliases:
            owner = seen_aliases.get(alias)
            if owner is not None:
                if owner == entity.entity_id:
                    raise InvalidTimelineError(
                        f"别名必须全局不重复：实体 {entity.entity_id!r} 的"
                        f"别名 {alias!r} 重复出现")
                raise InvalidTimelineError(
                    f"别名必须全局不重复：{alias!r} 同时出现在实体 "
                    f"{owner!r} 与 {entity.entity_id!r} 中")
            seen_aliases[alias] = entity.entity_id


def find_entity_hits(text: str,
                     entities: list[EntitySpec]) -> list[tuple[int, int, str]]:
    """从全部逐字精确命中中选出互不重叠的最优子集。

    返回 ``(起始下标, 结束下标, 实体 ID)``，结束下标为半开区间，按原文顺序。
    """
    n = len(text)
    if n == 0:
        return []

    # 每个起点对应的候选：(终点, 实体 ID)。别名全局不重复 ⇒ 同一匹配串只
    # 属于一个实体，故同一 (起点, 终点) 至多对应一个实体。
    candidates: list[list[tuple[int, str]]] = [[] for _ in range(n)]
    for entity in entities:
        for alias in entity.aliases:
            if not alias:
                continue
            pos = text.find(alias)
            while pos != -1:
                candidates[pos].append((pos + len(alias), entity.entity_id))
                pos = text.find(alias, pos + 1)

    # 反向 DP：best[i] 为后缀 text[i:] 的最优（覆盖字符数, 命中数, 首个命中）。
    # 命中区间互不重叠，序列按起点排序即有序；两个同分方案的首个命中不同时，
    # 首个命中较小者整个序列字典序必小（后续命中由 best[end] 递归保证最优），
    # 首个命中相同则必为同一候选、后续序列相同，故只需比较首个命中。
    best_cov = [0] * (n + 1)
    best_hits = [0] * (n + 1)
    best_first: list[tuple[int, int, str] | None] = [None] * (n + 1)

    for i in range(n - 1, -1, -1):
        cov = best_cov[i + 1]
        hits = best_hits[i + 1]
        first = best_first[i + 1]
        for end, eid in candidates[i]:
            c_cov = (end - i) + best_cov[end]
            c_hits = 1 + best_hits[end]
            c_first = (i, end, eid)
            if (c_cov > cov
                    or (c_cov == cov and c_hits < hits)
                    or (c_cov == cov and c_hits == hits
                        and (first is None or c_first < first))):
                cov, hits, first = c_cov, c_hits, c_first
        best_cov[i] = cov
        best_hits[i] = hits
        best_first[i] = first

    result: list[tuple[int, int, str]] = []
    i = 0
    while i < n:
        first = best_first[i]
        if first is not None and first[0] == i:
            result.append(first)
            i = first[1]
        else:
            i += 1
    return result


def build_entity_links(text: str, chars: list[RevisedCharTimeline],
                       entities: list[EntitySpec],
                       bucket_ms: int) -> EntityLinksResponse:
    """生成互不重叠的实体命中与按时间桶统计的共提及边。"""
    hits: list[EntityLinkHit] = []
    # 时间桶 -> 该桶内出现过的实体 ID 集合（同桶同一实体只计一次）。
    bucket_entities: dict[int, set[str]] = {}
    for start, end, eid in find_entity_hits(text, entities):
        first_char = chars[start]
        last_char = chars[end - 1]
        hits.append(EntityLinkHit(
            entity_id=eid,
            start_index=start,
            end_index=end,
            start=first_char.start,
            end=last_char.end,
        ))
        bucket = first_char.start // bucket_ms
        bucket_entities.setdefault(bucket, set()).add(eid)

    # 每对实体共同出现的桶 -> 边；桶下标按升序收集。
    pair_buckets: dict[tuple[str, str], list[int]] = {}
    for bucket in sorted(bucket_entities):
        for first_id, second_id in combinations(
                sorted(bucket_entities[bucket]), 2):
            pair_buckets.setdefault((first_id, second_id), []).append(bucket)

    edges = [
        EntityLinkEdge(
            source=first_id,
            target=second_id,
            weight=len(buckets),
            buckets=buckets,
        )
        for (first_id, second_id), buckets in sorted(pair_buckets.items())
    ]
    return EntityLinksResponse(hits=hits, edges=edges)
