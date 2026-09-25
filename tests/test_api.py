"""端到端 API 与时间轴语义测试（native 与 python 两种内核均会覆盖）。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.alignment import align_chars  # noqa: E402
from app.main import app  # noqa: E402
from app.quote import find_quote_hits  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def rc(items):
    return [{"char": c, "start": a, "end": b} for c, a, b in items]


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 对齐内核：重复字符结合上下文选择匹配位置，同分优先较早原位置
# ---------------------------------------------------------------------------
def test_duplicate_chars_prefer_earlier_source():
    # aaa -> aa：保留前两个（较早原位置）
    assert align_chars("aaa", "aa") == [0, 1]
    # aa -> aaa：新增字符落在末尾
    assert align_chars("aa", "aaa") == [0, 1, -1]


def test_substitution_is_anchored():
    # 替换纠错优先锚定到识别字符时间槽，而不是全部走删除+新增
    mapping = align_chars("识别错误文字", "识别正确文字")
    assert mapping[2] == 2  # “正” 替换 “错”，锚定下标 2
    assert mapping[3] == 3  # “确” 替换 “误”


def test_insertion_and_deletion():
    mapping = align_chars("你好吗世界", "你好世界")
    assert mapping == [0, 1, 3, 4]  # “吗” 被删除


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["backend"] in ("native", "python")


# ---------------------------------------------------------------------------
# 基本对齐与新增字符空档均分
# ---------------------------------------------------------------------------
def test_basic_align_with_interpolation(client):
    payload = {
        "recognized": rc([
            ("你", 0, 100), ("好", 200, 300), ("世", 400, 500), ("界", 600, 700),
        ]),
        "total_duration": 1000,
        "revised_text": "你好呀世界",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    chars = body["chars"]
    assert [c["char"] for c in chars] == list("你好呀世界")
    assert [c["source"] for c in chars] == [
        "matched", "matched", "computed", "matched", "matched"]

    ni = chars[2]
    # “呀”位于“好”结束(300)与“世”开始(400)之间
    assert ni["start"] == 300 and ni["end"] == 400
    # matched 锚点时间原样保留
    assert (chars[0]["start"], chars[0]["end"]) == (0, 100)
    assert body["inserted_count"] == 1
    assert body["deleted_count"] == 0
    assert body["matched_count"] == 4


def test_multiple_insertions_evenly_split(client):
    # 锚点 a 与 c 之间没有任何识别字符，x、y 为两个纯新增字符，空档 [100,600]
    payload = {
        "recognized": rc([("a", 0, 100), ("c", 600, 700)]),
        "total_duration": 1000,
        "revised_text": "axyc",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    x, y = r.json()["chars"][1], r.json()["chars"][2]
    assert x["source"] == "computed" and y["source"] == "computed"
    # [100,600] 分两段：100..350, 350..600
    assert (x["start"], x["end"]) == (100, 350)
    assert (y["start"], y["end"]) == (350, 600)


def test_substitution_tie_anchors_into_gap(client):
    # a b c -> a x y c：x 与 b 替换代价相同且优先锚定，y 落在 b 与 c 的空档
    payload = {
        "recognized": rc([("a", 0, 100), ("b", 200, 300), ("c", 600, 700)]),
        "total_duration": 1000,
        "revised_text": "axyc",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    x, y = r.json()["chars"][1], r.json()["chars"][2]
    assert x["source"] == "matched" and x["recognized_char"] == "b"
    assert (x["start"], x["end"]) == (200, 300)
    assert y["source"] == "computed"
    assert (y["start"], y["end"]) == (300, 600)


def test_leading_and_trailing_insertions_use_recording_bounds(client):
    payload = {
        "recognized": rc([("b", 400, 500)]),
        "total_duration": 1000,
        "revised_text": "abc",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    a, b, c = r.json()["chars"]
    # 前缀“a”在录音起点 0 到 b 开始 400 之间
    assert (a["start"], a["end"]) == (0, 400)
    # 后缀“c”在 b 结束 500 到录音总时长 1000 之间
    assert (c["start"], c["end"]) == (500, 1000)
    assert b["source"] == "matched"


def test_deleted_chars_not_output(client):
    payload = {
        "recognized": rc([("a", 0, 100), ("x", 200, 300), ("b", 400, 500)]),
        "total_duration": 600,
        "revised_text": "ab",
    }
    r = client.post("/align", json=payload)
    body = r.json()
    assert [c["char"] for c in body["chars"]] == ["a", "b"]
    assert body["deleted_count"] == 1


def test_substitution_marked_matched_with_recognized_char(client):
    payload = {
        "recognized": rc([("错", 0, 100)]),
        "total_duration": 200,
        "revised_text": "对",
    }
    r = client.post("/align", json=payload)
    body = r.json()
    ch = body["chars"][0]
    assert ch["char"] == "对"
    assert ch["source"] == "matched"
    assert ch["recognized_char"] == "错"
    assert (ch["start"], ch["end"]) == (0, 100)


def test_all_inserted_spans_whole_recording(client):
    payload = {"recognized": [], "total_duration": 300, "revised_text": "三"}
    r = client.post("/align", json=payload)
    ch = r.json()["chars"][0]
    assert ch["source"] == "computed"
    assert (ch["start"], ch["end"]) == (0, 300)


def test_empty_revised_text(client):
    payload = {
        "recognized": rc([("a", 0, 100)]),
        "total_duration": 200,
        "revised_text": "",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["chars"] == []
    assert body["deleted_count"] == 1


def test_timestamps_ordered_and_in_range(client):
    payload = {
        "recognized": rc([("a", 0, 10), ("b", 20, 30), ("c", 40, 50)]),
        "total_duration": 100,
        "revised_text": "xaxbxcx",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    prev_end = 0
    for c in r.json()["chars"]:
        assert 0 <= c["start"] <= c["end"] <= 100
        assert c["start"] >= prev_end
        prev_end = c["end"]


def test_zero_duration(client):
    payload = {"recognized": rc([("a", 0, 0)]), "total_duration": 0,
               "revised_text": "a"}
    r = client.post("/align", json=payload)
    assert r.status_code == 200
    ch = r.json()["chars"][0]
    assert (ch["start"], ch["end"]) == (0, 0)


def test_duplicate_resolved_by_context_to_earlier_source(client):
    # 识别串里出现两个“对”。修订文本在“错”前补“对”、“错”后保留“对”：
    # 第一个“对”必须结合上下文匹配到更早的识别位置 0，而不是误配位置 2。
    payload = {
        "recognized": rc([
            ("对", 0, 100), ("错", 200, 300), ("对", 400, 500),
        ]),
        "total_duration": 600,
        "revised_text": "对错对",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    chars = r.json()["chars"]
    assert [c["matched_index"] for c in chars] == [0, 1, 2]


def test_duplicate_identical_target_keeps_earlier_slots(client):
    # aaa（三个相同识别字） -> aa：删除多余重复字符时，保留最早的两个时间槽
    payload = {
        "recognized": rc([("a", 0, 10), ("a", 20, 30), ("a", 40, 50)]),
        "total_duration": 60,
        "revised_text": "aa",
    }
    r = client.post("/align", json=payload)
    chars = r.json()["chars"]
    assert [c["matched_index"] for c in chars] == [0, 1]
    assert (chars[0]["start"], chars[1]["start"]) == (0, 20)
    assert r.json()["deleted_count"] == 1


# ---------------------------------------------------------------------------
# 输入校验：时间无效时给出明确错误
# ---------------------------------------------------------------------------
def _post(client, payload):
    return client.post("/align", json=payload)


def test_negative_total_duration(client):
    r = _post(client, {"recognized": [], "total_duration": -1,
                       "revised_text": "a"})
    assert r.status_code == 422
    assert "非负" in r.json()["detail"]


def test_end_before_start(client):
    r = _post(client, {
        "recognized": rc([("a", 300, 100)]),
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_out_of_recording_range(client):
    r = _post(client, {
        "recognized": rc([("a", 100, 600)]),
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "超出录音总时长" in r.json()["detail"]


def test_not_sorted(client):
    r = _post(client, {
        "recognized": rc([("b", 300, 400), ("a", 0, 100)]),
        "total_duration": 500, "revised_text": "ab"})
    assert r.status_code == 422
    assert "排序" in r.json()["detail"]


def test_overlapping_intervals(client):
    r = _post(client, {
        "recognized": rc([("a", 0, 200), ("b", 100, 300)]),
        "total_duration": 500, "revised_text": "ab"})
    assert r.status_code == 422
    assert "重叠" in r.json()["detail"]


def test_negative_timestamp(client):
    r = _post(client, {
        "recognized": rc([("a", -5, 100)]),
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "负数" in r.json()["detail"]


def test_missing_field(client):
    r = client.post("/align", json={"recognized": [], "total_duration": 100})
    assert r.status_code == 422
    assert "revised_text" in r.json()["detail"]


def test_wrong_type_timestamp(client):
    r = client.post("/align", json={
        "recognized": [{"char": "a", "start": "0", "end": 100}],
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


def test_multi_char_recognized_item(client):
    r = _post(client, {
        "recognized": [{"char": "ab", "start": 0, "end": 100}],
        "total_duration": 500, "revised_text": "ab"})
    assert r.status_code == 422
    assert "1 个字符" in r.json()["detail"]


def test_float_duration_rejected(client):
    r = _post(client, {"recognized": [], "total_duration": 1.5,
                       "revised_text": "a"})
    assert r.status_code == 422


def test_extra_field_rejected(client):
    r = _post(client, {"recognized": [], "total_duration": 5,
                       "revised_text": "a", "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_zero_duration_multiple_inserts(client):
    r = _post(client, {"recognized": [], "total_duration": 0,
                       "revised_text": "甲乙"})
    assert r.status_code == 200
    chars = r.json()["chars"]
    assert [(c["start"], c["end"]) for c in chars] == [(0, 0), (0, 0)]


# ---------------------------------------------------------------------------
# 引语近似定位
# ---------------------------------------------------------------------------
def test_quote_finder_exact_repeats():
    assert find_quote_hits("abc", "xabcxabc", 1) == [
        (1, 4, 0), (5, 8, 0)]


def test_quote_finder_uses_insertion():
    assert find_quote_hits("你好世界", "你好呀世界", 1) == [(0, 5, 1)]


def test_quote_finder_overlapping_choice_prefers_more_hits():
    # 长度 2 的引语与任一单字符距离均为 1，因此四个互不重叠单字符优先于两个较长命中。
    assert find_quote_hits("ab", "aabb", 1) == [
        (0, 1, 1), (1, 2, 1), (2, 3, 1), (3, 4, 1)]


def test_quote_finder_tie_uses_lower_edit_cost():
    # 同一文本内较短片段可消耗 1 次删除命中；零编辑完整片段优先。
    assert find_quote_hits("abc", "abc", 1) == [(0, 3, 0)]


def test_locate_quote_returns_timeline_ranges(client):
    payload = {
        "recognized": rc([
            ("你", 0, 100), ("好", 200, 300), ("世", 500, 600),
            ("界", 700, 800),
        ]),
        "total_duration": 1000,
        "revised_text": "你好呀世界",
        "quote": "你好世界",
        "max_edits": 1,
    }
    r = client.post("/locate-quote", json=payload)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "start_index": 0,
        "end_index": 5,
        "text": "你好呀世界",
        "edits": 1,
        "start": 0,
        "end": 800,
    }]


def test_locate_quote_empty_array_when_no_hit(client):
    payload = {
        "recognized": rc([("a", 0, 100)]),
        "total_duration": 200,
        "revised_text": "a",
        "quote": "zzz",
        "max_edits": 2,
    }
    r = client.post("/locate-quote", json=payload)
    assert r.status_code == 200
    assert r.json() == []


def test_locate_quote_reuses_alignment_validation(client):
    payload = {
        "recognized": rc([("a", 300, 100)]),
        "total_duration": 500,
        "revised_text": "a",
        "quote": "a",
        "max_edits": 0,
    }
    r = client.post("/locate-quote", json=payload)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_locate_quote_empty_quote_rejected(client):
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "quote": "", "max_edits": 0})
    assert r.status_code == 422
    assert "非空" in r.json()["detail"]


def test_locate_quote_max_edits_range(client):
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "quote": "a", "max_edits": 3})
    assert r.status_code == 422
    assert "0～2" in r.json()["detail"]


def test_locate_quote_extra_field_rejected(client):
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "quote": "a", "max_edits": 0, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_locate_quote_too_long_rejected(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_QUOTE_LENGTH", 2)
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "abc",
        "quote": "abc", "max_edits": 0})
    assert r.status_code == 422
    assert "引语长度" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 字幕切分
# ---------------------------------------------------------------------------
def _captions(client, items, revised, max_chars=80, max_duration_ms=30000,
              total=None):
    payload = {
        "recognized": rc(items),
        "total_duration": total if total is not None else 40000,
        "revised_text": revised,
        "max_chars": max_chars,
        "max_duration_ms": max_duration_ms,
    }
    return client.post("/captions", json=payload)


def test_captions_empty_text_returns_empty_array(client):
    r = _captions(client, [], "", total=0)
    assert r.status_code == 200
    assert r.json() == []


def test_captions_prefer_break_after_sentence_punctuation(client):
    # 两种两段切分：[你好][。世界] 与 [你好。][世界]；断点左字为“。”加 2000 分
    r = _captions(client, [
        ("你", 0, 100), ("好", 200, 300), ("。", 400, 500),
        ("世", 600, 700), ("界", 800, 900),
    ], "你好。世界", max_chars=3, total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"start_index": 0, "end_index": 3, "text": "你好。",
         "start": 0, "end": 500},
        {"start_index": 3, "end_index": 5, "text": "世界",
         "start": 600, "end": 900},
    ]


def test_captions_punctuation_bonus_applies_to_all_listed_marks(client):
    for mark in "。！？；":
        r = _captions(client, [
            ("甲", 0, 100), (mark, 200, 300), ("乙", 400, 500),
        ], f"甲{mark}乙", max_chars=2, total=600)
        assert r.status_code == 200, r.text
        segs = r.json()
        assert [s["text"] for s in segs] == [f"甲{mark}", "乙"]


def test_captions_maximize_pause_score_without_punctuation(client):
    # 候选断点停顿分别为 100 / 300 / 100 毫秒，选停顿最大者
    r = _captions(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 600, 700), ("d", 800, 900),
    ], "abcd", max_chars=3, total=1000)
    assert [s["text"] for s in r.json()] == ["ab", "cd"]


def test_captions_pause_clamped_then_lexicographic(client):
    # 断点 1 与断点 3 的停顿分别为 1900、3200 毫秒，均截到 1000 而同分，
    # 取断点下标序列字典序最小者
    r = _captions(client, [
        ("a", 0, 100), ("b", 2000, 2100), ("c", 2200, 2300),
        ("d", 5500, 5600),
    ], "abcd", max_chars=3, total=6000)
    segs = r.json()
    assert [s["text"] for s in segs] == ["a", "bcd"]
    assert (segs[0]["start_index"], segs[0]["end_index"]) == (0, 1)
    assert (segs[1]["start_index"], segs[1]["end_index"]) == (1, 4)


def test_captions_fewest_segments_beats_breakpoint_score(client):
    # 一段总分 0 也优于两段（断点在“。”后可得 2000+ 分）
    r = _captions(client, [
        ("a", 0, 100), ("。", 200, 300), ("b", 400, 500),
    ], "a。b", max_chars=3, total=600)
    assert r.json() == [
        {"start_index": 0, "end_index": 3, "text": "a。b",
         "start": 0, "end": 500},
    ]


def test_captions_respect_max_chars(client):
    # 停顿全部相同，同分时取字典序最小断点序列：前段尽量短
    r = _captions(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 400, 500),
        ("d", 600, 700), ("e", 800, 900),
    ], "abcde", max_chars=2, total=1000)
    segs = r.json()
    assert [s["text"] for s in segs] == ["a", "bc", "de"]
    assert all(s["end_index"] - s["start_index"] <= 2 for s in segs)


def test_captions_respect_max_duration(client):
    # 每段跨度不得超过 1000 毫秒：abc 相连 1400 毫秒超限，只能 ab / cd
    r = _captions(client, [
        ("a", 0, 400), ("b", 500, 900), ("c", 1000, 1400),
        ("d", 1500, 1900),
    ], "abcd", max_duration_ms=1000, total=2000)
    segs = r.json()
    assert [s["text"] for s in segs] == ["ab", "cd"]
    assert (segs[0]["start"], segs[0]["end"]) == (0, 900)
    assert (segs[1]["start"], segs[1]["end"]) == (1000, 1900)


def test_captions_single_char_span_exceeds_limit(client):
    r = _captions(client, [("啊", 0, 600)], "啊",
                  max_duration_ms=500, total=1000)
    assert r.status_code == 422
    assert "超过单段时长上限" in r.json()["detail"]


def test_captions_uses_computed_timeline_for_inserted_chars(client):
    # 新增字符“呀”在空档 [300,400] 内推算，切分以其推算时间为准
    r = _captions(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 400, 500),
    ], "你好呀世", max_chars=2, total=600)
    segs = r.json()
    assert [s["text"] for s in segs] == ["你好", "呀世"]
    assert (segs[1]["start"], segs[1]["end"]) == (300, 500)


def test_captions_reuses_alignment_validation(client):
    r = _captions(client, [("a", 300, 100)], "a", total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_captions_max_chars_range(client):
    for bad in (0, 81):
        r = _captions(client, [], "", max_chars=bad, total=0)
        assert r.status_code == 422
        assert "1～80" in r.json()["detail"]


def test_captions_max_duration_range(client):
    for bad in (499, 30001):
        r = _captions(client, [], "", max_duration_ms=bad, total=0)
        assert r.status_code == 422
        assert "500～30000" in r.json()["detail"]


def test_captions_extra_field_rejected(client):
    r = client.post("/captions", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "max_chars": 10, "max_duration_ms": 1000, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 修订差异
# ---------------------------------------------------------------------------
def _revision_diff(client, items, revised, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
    }
    return client.post("/revision-diff", json=payload)


def test_revision_diff_no_changes_returns_empty_array(client):
    r = _revision_diff(client, [("你", 0, 100), ("好", 200, 300)], "你好",
                       total=400)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_revision_diff_insert_block_uses_computed_timeline(client):
    # “呀”为新增字符，在“好”结束(300)与“世”开始(400)之间推算
    r = _revision_diff(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 400, 500), ("界", 600, 700),
    ], "你好呀世界")
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "type": "insert",
        "recognized_start": 2,
        "recognized_end": 2,
        "revised_start": 2,
        "revised_end": 3,
        "recognized_text": "",
        "revised_text": "呀",
        "start": 300,
        "end": 400,
    }]


def test_revision_diff_delete_block(client):
    r = _revision_diff(client, [
        ("你", 0, 100), ("吗", 200, 300), ("好", 400, 500),
    ], "你好", total=600)
    assert r.json() == [{
        "type": "delete",
        "recognized_start": 1,
        "recognized_end": 2,
        "revised_start": 1,
        "revised_end": 1,
        "recognized_text": "吗",
        "revised_text": "",
        "start": 200,
        "end": 300,
    }]


def test_revision_diff_substitution_is_replace(client):
    r = _revision_diff(client, [("错", 0, 100)], "对", total=200)
    assert r.json() == [{
        "type": "replace",
        "recognized_start": 0,
        "recognized_end": 1,
        "revised_start": 0,
        "revised_end": 1,
        "recognized_text": "错",
        "revised_text": "对",
        "start": 0,
        "end": 100,
    }]


def test_revision_diff_mixed_delete_and_insert_is_replace(client):
    # “bc”被删、“x”新增，落在同一空档（无未改动匹配字符分隔）→ 合并为 replace
    r = _revision_diff(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 400, 500),
    ], "ax", total=600)
    assert r.json() == [{
        "type": "replace",
        "recognized_start": 1,
        "recognized_end": 3,
        "revised_start": 1,
        "revised_end": 2,
        "recognized_text": "bc",
        "revised_text": "x",
        "start": 200,
        "end": 500,
    }]


def test_revision_diff_ordered_blocks_of_all_types(client):
    # 依次：删除“吗”、插入“呀”（推算 [500,600]）、替换“玩→耍”
    r = _revision_diff(client, [
        ("吗", 0, 100), ("你", 200, 300), ("好", 400, 500),
        ("呢", 600, 700), ("玩", 800, 900),
    ], "你好呀呢耍")
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"type": "delete", "recognized_start": 0, "recognized_end": 1,
         "revised_start": 0, "revised_end": 0,
         "recognized_text": "吗", "revised_text": "", "start": 0, "end": 100},
        {"type": "insert", "recognized_start": 3, "recognized_end": 3,
         "revised_start": 2, "revised_end": 3,
         "recognized_text": "", "revised_text": "呀",
         "start": 500, "end": 600},
        {"type": "replace", "recognized_start": 4, "recognized_end": 5,
         "revised_start": 4, "revised_end": 5,
         "recognized_text": "玩", "revised_text": "耍",
         "start": 800, "end": 900},
    ]


def test_revision_diff_block_time_spans_both_sides(client):
    # 替换“x→y”占 [200,300]，新增“z”推算到 [300,900]，
    # 块时间取两侧最早起点 200、最晚终点 900
    r = _revision_diff(client, [
        ("a", 0, 100), ("x", 200, 300), ("b", 900, 1000),
    ], "ayzb", total=1000)
    assert r.json() == [{
        "type": "replace",
        "recognized_start": 1,
        "recognized_end": 2,
        "revised_start": 1,
        "revised_end": 3,
        "recognized_text": "x",
        "revised_text": "yz",
        "start": 200,
        "end": 900,
    }]


def test_revision_diff_empty_revised_text_is_single_delete(client):
    r = _revision_diff(client, [("a", 0, 100), ("b", 200, 300)], "", total=400)
    assert r.json() == [{
        "type": "delete",
        "recognized_start": 0,
        "recognized_end": 2,
        "revised_start": 0,
        "revised_end": 0,
        "recognized_text": "ab",
        "revised_text": "",
        "start": 0,
        "end": 300,
    }]


def test_revision_diff_empty_recognized_is_single_insert(client):
    r = _revision_diff(client, [], "甲乙", total=300)
    assert r.json() == [{
        "type": "insert",
        "recognized_start": 0,
        "recognized_end": 0,
        "revised_start": 0,
        "revised_end": 2,
        "recognized_text": "",
        "revised_text": "甲乙",
        "start": 0,
        "end": 300,
    }]


def test_revision_diff_reuses_alignment_validation(client):
    r = _revision_diff(client, [("a", 300, 100)], "a", total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_revision_diff_extra_field_rejected(client):
    r = client.post("/revision-diff", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 校准重映射 POST /retime
# ---------------------------------------------------------------------------
def _retime(client, items, revised, calib, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
        "calibrations": calib,
    }
    return client.post("/retime", json=payload)


def _pt(a, b):
    return {"at_ms": a, "corrected_ms": b}


def test_retime_piecewise_remap_with_inserted_char(client):
    # 旧轴：你[0,100] 好[200,300]，呀 推算 [300,500]，世[500,600] 界[700,800]
    # 校准点 500 -> 750：前段 [0,500] -> [0,750]（×1.5），
    # 后段 [500,1000] -> [750,1000]（×0.5）。
    r = _retime(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 500, 600), ("界", 700, 800),
    ], "你好呀世界", [_pt(500, 750)])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_duration"] == 1000
    assert body["inserted_count"] == 1
    assert body["deleted_count"] == 0
    assert body["matched_count"] == 4
    assert body["backend"] in ("native", "python")
    assert [(c["char"], c["source"], c["matched_index"]) for c in body["chars"]] == [
        ("你", "matched", 0), ("好", "matched", 1), ("呀", "computed", None),
        ("世", "matched", 2), ("界", "matched", 3)]
    assert [(c["start"], c["end"]) for c in body["chars"]] == [
        (0, 150), (300, 450), (450, 750), (750, 800), (850, 900)]


def test_retime_floor_interpolation(client):
    # 段 [0,100] -> [0,30]：t=50 -> 15，t=100 精确命中 30
    r = _retime(client, [("a", 0, 50), ("b", 50, 100)], "ab", [_pt(100, 30)])
    assert r.status_code == 200, r.text
    assert [(c["start"], c["end"]) for c in r.json()["chars"]] == [
        (0, 15), (15, 30)]


def test_retime_calibration_points_hit_exactly_across_segments(client):
    # 多个校准点；识别字符正好落在校准位置上时必须精确命中，不得有取整误差。
    # 结 0,200,400,700,1000 -> 新 0,500,700,800,1000（两列均严格递增）。
    r = _retime(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 400, 500), ("d", 700, 900),
    ], "abcd", [_pt(200, 500), _pt(400, 700), _pt(700, 800)])
    assert r.status_code == 200, r.text
    chars = r.json()["chars"]
    # a.end=100 在段 [0,200]->[0,500]：500*100//200 = 250
    assert (chars[0]["start"], chars[0]["end"]) == (0, 250)
    # b.start 旧 200 命中校准点 -> 500；b.end=300 在 [200,400]->[500,700]：600
    assert (chars[1]["start"], chars[1]["end"]) == (500, 600)
    # c.start 旧 400 命中校准点 -> 700；c.end=500 在 [400,700]->[700,800]：733
    assert (chars[2]["start"], chars[2]["end"]) == (700, 733)
    # d.start 旧 700 命中校准点 -> 800；d.end=900 在 [700,1000]->[800,1000]：933
    assert (chars[3]["start"], chars[3]["end"]) == (800, 933)


def test_retime_endpoints_fixed_zero_and_total(client):
    r = _retime(client, [
        ("a", 0, 100), ("b", 200, 400),
    ], "ab", [_pt(200, 300)], total=500)
    assert r.status_code == 200, r.text
    chars = r.json()["chars"]
    # 旧 0 固定映射 0
    assert chars[0]["start"] == 0
    # b.end=400 位于末段 [200,500] -> [300,500]：300 + 200*200//300 = 433
    assert chars[1]["end"] == 433
    # 追加一个旧时间恰为 total_duration 的字，新时间必须仍是 total
    r2 = _retime(client, [
        ("a", 0, 100), ("b", 200, 500),
    ], "ab", [_pt(200, 300)], total=500)
    assert r2.status_code == 200, r2.text
    assert r2.json()["chars"][1]["end"] == 500


def test_retime_no_regression_and_in_range(client):
    # 结 0,25,45,100 -> 新 0,20,70,100：前段压缩、中段拉伸、末段压缩
    r = _retime(client, [
        ("a", 0, 10), ("b", 20, 30), ("c", 40, 50),
    ], "xaxbxcx", [_pt(25, 20), _pt(45, 70)], total=100)
    assert r.status_code == 200, r.text
    prev_end = 0
    for c in r.json()["chars"]:
        assert 0 <= c["start"] <= c["end"] <= 100
        assert c["start"] >= prev_end
        prev_end = c["end"]


def test_retime_empty_revised_text(client):
    r = _retime(client, [("a", 0, 100)], "", [_pt(50, 80)], total=200)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chars"] == []
    assert body["deleted_count"] == 1


def test_retime_preserves_recognized_char_on_substitution(client):
    r = _retime(client, [("错", 0, 100)], "对", [_pt(50, 80)], total=200)
    assert r.status_code == 200, r.text
    ch = r.json()["chars"][0]
    assert ch["char"] == "对" and ch["recognized_char"] == "错"
    assert ch["source"] == "matched" and ch["matched_index"] == 0
    # end=100 在段 [50,200]->[80,200]：80 + 120*50//150 = 120
    assert (ch["start"], ch["end"]) == (0, 120)


def test_retime_missing_calibrations_rejected(client):
    r = client.post("/retime", json={
        "recognized": [], "total_duration": 100, "revised_text": "a"})
    assert r.status_code == 422
    assert "calibrations" in r.json()["detail"]


def test_retime_empty_calibrations_rejected(client):
    r = _retime(client, [], "a", [])
    assert r.status_code == 422
    assert "1～20" in r.json()["detail"]


def test_retime_too_many_calibrations_rejected(client):
    calib = [_pt(i, i) for i in range(1, 22)]
    r = _retime(client, [], "a", calib, total=1000)
    assert r.status_code == 422
    assert "1～20" in r.json()["detail"]


def test_retime_twenty_calibrations_accepted(client):
    # 恰好 20 个：at_ms=5,10,...,100；corrected_ms=10,20,...,200，均严格递增
    calib = [_pt(5 * i, 10 * i) for i in range(1, 21)]
    r = _retime(client, [], "a", calib, total=1000)
    assert r.status_code == 200, r.text
    ch = r.json()["chars"][0]
    # 全新增字符“a”：旧轴 [0,1000]，起点 0 -> 0；终点 1000 -> 1000
    assert (ch["start"], ch["end"]) == (0, 1000)


def test_retime_at_ms_not_strictly_increasing(client):
    r = _retime(client, [], "a", [_pt(400, 100), _pt(400, 900)])
    assert r.status_code == 422
    assert "at_ms" in r.json()["detail"]
    assert "严格递增" in r.json()["detail"]


def test_retime_corrected_ms_not_strictly_increasing(client):
    r = _retime(client, [], "a", [_pt(100, 500), _pt(400, 500)])
    assert r.status_code == 422
    assert "corrected_ms" in r.json()["detail"]
    assert "严格递增" in r.json()["detail"]


def test_retime_at_ms_out_of_open_interval(client):
    for bad_at in (0, 1000, -1, 1001):
        r = _retime(client, [], "a", [_pt(bad_at, 500)])
        assert r.status_code == 422, (bad_at, r.text)
        assert "(0, 1000)" in r.json()["detail"]


def test_retime_corrected_ms_out_of_open_interval(client):
    for bad_new in (0, 1000):
        r = _retime(client, [], "a", [_pt(500, bad_new)])
        assert r.status_code == 422
        assert "(0, 1000)" in r.json()["detail"]


def test_retime_zero_duration_has_no_interior_points(client):
    # total_duration=0 时开区间 (0,0) 为空，任何校准点都非法
    r = client.post("/retime", json={
        "recognized": [], "total_duration": 0, "revised_text": "甲",
        "calibrations": [_pt(0, 0)]})
    assert r.status_code == 422
    assert "(0, 0)" in r.json()["detail"]


def test_retime_reuses_alignment_validation(client):
    r = _retime(client, [("a", 300, 100)], "a", [_pt(200, 400)], total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_retime_extra_field_rejected(client):
    r = client.post("/retime", json={
        "recognized": [], "total_duration": 100, "revised_text": "a",
        "calibrations": [_pt(50, 60)], "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_retime_calibration_point_wrong_type(client):
    r = client.post("/retime", json={
        "recognized": [], "total_duration": 100, "revised_text": "a",
        "calibrations": [{"at_ms": "50", "corrected_ms": 60}]})
    assert r.status_code == 422
    assert "整数毫秒" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 说话人轮次归属 POST /speaker-turns
# ---------------------------------------------------------------------------
def _speaker_turns(client, items, revised, turns, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
        "speaker_turns": turns,
    }
    return client.post("/speaker-turns", json=payload)


def _turn(speaker, start, end):
    return {"speaker_id": speaker, "start": start, "end": end}


def test_speaker_turns_assign_by_max_overlap_and_merge(client):
    # 你[0,100] 好[200,300] 呀(computed)[300,400] 世[500,600] 界[700,800]
    # 轮次 A[0,250)、B[250,1000)：好与 A 重叠 50、与 B 重叠 50 → 并列取较早 A
    r = _speaker_turns(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 500, 600), ("界", 700, 800),
    ], "你好呀世界", [_turn("A", 0, 250), _turn("B", 250, 1000)])
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"speaker_id": "A", "start_index": 0, "end_index": 2,
         "text": "你好", "start": 0, "end": 300},
        {"speaker_id": "B", "start_index": 2, "end_index": 5,
         "text": "呀世界", "start": 300, "end": 800},
    ]


def test_speaker_turns_boundary_goes_to_right_turn(client):
    # 字符正好覆盖到边界 300：a[0,300) 与左右两轮次各重叠 300，并列取较早；
    # 而零时长字符落在边界 300 上时归右侧轮次。
    r = _speaker_turns(client, [
        ("a", 0, 300), ("b", 300, 300), ("c", 300, 600),
    ], "abc", [_turn("A", 0, 300), _turn("B", 300, 1000)], total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"speaker_id": "A", "start_index": 0, "end_index": 1,
         "text": "a", "start": 0, "end": 300},
        {"speaker_id": "B", "start_index": 1, "end_index": 3,
         "text": "bc", "start": 300, "end": 600},
    ]


def test_speaker_turns_no_overlap_is_null_block(client):
    # 轮次只覆盖 [0,200)；好 [200,300) 与之端点相接无重叠，后续字符也无重叠
    r = _speaker_turns(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 400, 500),
    ], "abc", [_turn("A", 0, 200)], total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"speaker_id": "A", "start_index": 0, "end_index": 1,
         "text": "a", "start": 0, "end": 100},
        {"speaker_id": None, "start_index": 1, "end_index": 3,
         "text": "bc", "start": 200, "end": 500},
    ]


def test_speaker_turns_zero_duration_char_inside_turn(client):
    # 零时长字符“。”[300,300] 位于轮次内部
    r = _speaker_turns(client, [
        ("你", 0, 100), ("好", 200, 300), ("。", 300, 300), ("呀", 400, 500),
    ], "你好。呀", [_turn("A", 0, 400), _turn("B", 400, 1000)])
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"speaker_id": "A", "start_index": 0, "end_index": 3,
         "text": "你好。", "start": 0, "end": 300},
        {"speaker_id": "B", "start_index": 3, "end_index": 4,
         "text": "呀", "start": 400, "end": 500},
    ]


def test_speaker_turns_zero_duration_char_in_gap_is_null(client):
    # 零时长字符在两轮次的间隙 (400,500) 内 → null，与相邻同归属字符合并
    r = _speaker_turns(client, [
        ("a", 0, 100), ("b", 450, 450), ("c", 600, 700),
    ], "abc", [_turn("A", 0, 400), _turn("B", 500, 1000)], total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"speaker_id": "A", "start_index": 0, "end_index": 1,
         "text": "a", "start": 0, "end": 100},
        {"speaker_id": None, "start_index": 1, "end_index": 2,
         "text": "b", "start": 450, "end": 450},
        {"speaker_id": "B", "start_index": 2, "end_index": 3,
         "text": "c", "start": 600, "end": 700},
    ]


def test_speaker_turns_choose_larger_overlap_not_earlier(client):
    # 字符 [100,500) 与 A 重叠 100、与 B 重叠 300 → 归 B（非并列不取较早）
    r = _speaker_turns(client, [("x", 100, 500)], "x",
                       [_turn("A", 0, 200), _turn("B", 200, 1000)],
                       total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "speaker_id": "B", "start_index": 0, "end_index": 1,
        "text": "x", "start": 100, "end": 500}]


def test_speaker_turns_same_speaker_adjacent_turns_merge(client):
    # 同一 speaker_id 的两个相接轮次，相邻归属字符合并为一块
    r = _speaker_turns(client, [
        ("a", 0, 100), ("b", 200, 300),
    ], "ab",
        [_turn("A", 0, 200), _turn("A", 200, 1000)], total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "speaker_id": "A", "start_index": 0, "end_index": 2,
        "text": "ab", "start": 0, "end": 300}]


def test_speaker_turns_empty_turns_all_null(client):
    r = _speaker_turns(client, [("a", 0, 100)], "a", [], total=200)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "speaker_id": None, "start_index": 0, "end_index": 1,
        "text": "a", "start": 0, "end": 100}]


def test_speaker_turns_empty_revised_text(client):
    r = _speaker_turns(client, [("a", 0, 100)], "", [_turn("A", 0, 200)],
                       total=200)
    assert r.status_code == 200
    assert r.json() == []


def test_speaker_turns_zero_duration_turns_touching_allowed(client):
    # 零时长轮次 [200,200) 与前后轮次端点相接，互不重叠 → 合法
    r = _speaker_turns(client, [
        ("a", 0, 100), ("b", 200, 300),
    ], "ab",
        [_turn("A", 0, 200), _turn("C", 200, 200), _turn("B", 200, 1000)],
        total=1000)
    assert r.status_code == 200, r.text
    assert [blk["speaker_id"] for blk in r.json()] == ["A", "B"]


def test_speaker_turns_reuses_alignment_validation(client):
    r = _speaker_turns(client, [("a", 300, 100)], "a",
                       [_turn("A", 0, 500)], total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_speaker_turns_negative_timestamp_rejected(client):
    r = _speaker_turns(client, [], "a", [_turn("A", -1, 100)])
    assert r.status_code == 422
    assert "说话人轮次" in r.json()["detail"]
    assert "负数" in r.json()["detail"]


def test_speaker_turns_end_before_start_rejected(client):
    r = _speaker_turns(client, [], "a", [_turn("A", 300, 100)])
    assert r.status_code == 422
    assert "说话人轮次" in r.json()["detail"]
    assert "不能早于起始时间" in r.json()["detail"]


def test_speaker_turns_out_of_recording_range_rejected(client):
    r = _speaker_turns(client, [], "a", [_turn("A", 0, 1001)],
                       total=1000)
    assert r.status_code == 422
    assert "超出录音总时长" in r.json()["detail"]


def test_speaker_turns_not_sorted_rejected(client):
    r = _speaker_turns(client, [], "a",
                       [_turn("B", 500, 600), _turn("A", 0, 100)])
    assert r.status_code == 422
    assert "按起始时间排序" in r.json()["detail"]


def test_speaker_turns_overlap_rejected(client):
    r = _speaker_turns(client, [], "a",
                       [_turn("A", 0, 300), _turn("B", 200, 500)])
    assert r.status_code == 422
    assert "不得重叠" in r.json()["detail"]


def test_speaker_turns_touching_intervals_allowed(client):
    # [0,200) 与 [200,500) 端点相接（左闭右开）不算重叠
    r = _speaker_turns(client, [("a", 0, 0)], "a",
                       [_turn("A", 0, 200), _turn("B", 200, 500)], total=500)
    assert r.status_code == 200, r.text
    # 零时长字符时点 0 位于 A 轮次起点，归 A（边界归右侧规则下 A 即右侧）
    assert r.json() == [{
        "speaker_id": "A", "start_index": 0, "end_index": 1,
        "text": "a", "start": 0, "end": 0}]


def test_speaker_turns_empty_speaker_id_rejected(client):
    r = client.post("/speaker-turns", json={
        "recognized": [], "total_duration": 100, "revised_text": "a",
        "speaker_turns": [{"speaker_id": "", "start": 0, "end": 50}]})
    assert r.status_code == 422
    assert "非空" in r.json()["detail"]


def test_speaker_turns_missing_field_rejected(client):
    r = client.post("/speaker-turns", json={
        "recognized": [], "total_duration": 100, "revised_text": "a"})
    assert r.status_code == 422
    assert "speaker_turns" in r.json()["detail"]


def test_speaker_turns_extra_field_rejected(client):
    r = client.post("/speaker-turns", json={
        "recognized": [], "total_duration": 100, "revised_text": "a",
        "speaker_turns": [{"speaker_id": "A", "start": 0, "end": 50,
                           "bogus": 1}]})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_speaker_turns_wrong_type_timestamp(client):
    r = client.post("/speaker-turns", json={
        "recognized": [], "total_duration": 100, "revised_text": "a",
        "speaker_turns": [{"speaker_id": "A", "start": "0", "end": 50}]})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]
