"""资料库检索测试。

这一层最容易「看起来能用其实是噪声」：任何查询都返回 top-k，
不设阈值、用错查询串，表面都正常。所以测试要钉住三件事：

1. 相关段落排第一，且领先幅度足够大
2. 无关查询返回空（而不是硬凑 top-k）
3. 查询串必须用知识点**中文标题**，不能用英文 id
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lifeng.concepts import ConceptGraph
from lifeng.materials import (
    Material,
    bm25_scores,
    load_materials,
    retrieve,
    tokenize,
)
from lifeng.models import load_problems
from lifeng.views import build_material_query

MATERIALS = Path("materials")
CONCEPTS = Path("concepts.yaml")


@pytest.fixture
def materials():
    return load_materials(MATERIALS)


@pytest.fixture
def problems():
    return load_problems("problems")


@pytest.fixture
def graph():
    return ConceptGraph.load(CONCEPTS)


# --------------------------------------------------------------------------
# 分词
# --------------------------------------------------------------------------


class TestTokenize:
    def test_chinese_uses_bigrams(self):
        tokens = tokenize("碳正离子")
        assert "碳正" in tokens and "正离" in tokens and "离子" in tokens
        # 不该切成一堆单字：单字几乎没有区分度
        assert "碳" not in tokens

    def test_single_character_run_is_kept(self):
        assert tokenize("碳") == ["碳"]

    def test_english_and_digits_are_words(self):
        tokens = tokenize("Diels-Alder [4+2]")
        assert "diels" in tokens and "alder" in tokens and "4" in tokens

    def test_case_is_folded(self):
        assert tokenize("SN2") == tokenize("sn2")


# --------------------------------------------------------------------------
# 装载
# --------------------------------------------------------------------------


class TestLoading:
    def test_missing_directory_returns_empty(self, tmp_path):
        """没有资料库时必须静默失效，而不是让应用起不来。"""
        assert load_materials(tmp_path / "nope") == []

    def test_empty_directory(self, tmp_path):
        assert load_materials(tmp_path) == []

    def test_ignores_unsupported_suffixes(self, tmp_path):
        (tmp_path / "a.pdf").write_text("x", encoding="utf-8")
        (tmp_path / "b.md").write_text("# T\n正文", encoding="utf-8")
        loaded = load_materials(tmp_path)
        assert len(loaded) == 1

    def test_splits_by_heading(self, tmp_path):
        (tmp_path / "n.md").write_text(
            "# 一\n甲\n\n# 二\n乙\n", encoding="utf-8"
        )
        headings = [m.heading for m in load_materials(tmp_path)]
        assert headings == ["一", "二"]

    def test_chunks_overlong_sections(self, tmp_path):
        (tmp_path / "n.md").write_text("# 长\n" + "字" * 2500, encoding="utf-8")
        parts = load_materials(tmp_path)
        assert len(parts) >= 3
        assert all(len(p.text) <= 900 for p in parts)

    def test_bad_encoding_is_skipped(self, tmp_path):
        (tmp_path / "bad.md").write_bytes(b"\xff\xfe\x00\x00 not utf8")
        (tmp_path / "ok.md").write_text("# T\n正文", encoding="utf-8")
        assert len(load_materials(tmp_path)) == 1

    def test_real_library_loads(self, materials):
        assert len(materials) >= 5
        assert all(m.text.strip() for m in materials)


# --------------------------------------------------------------------------
# 检索
# --------------------------------------------------------------------------


class TestRetrieval:
    def test_relevant_section_ranks_first(self, materials):
        hits = retrieve(materials, "休克尔规则 4n+2", limit=1)
        assert hits
        assert "芳香" in hits[0][0].heading or "休克尔" in hits[0][0].text

    def test_irrelevant_query_returns_nothing(self, materials):
        """必须返回空。硬凑 top-k 会让人以为检索到了，其实是噪声。"""
        assert retrieve(materials, "量子色动力学胶子禁闭") == []

    def test_empty_query_returns_nothing(self, materials):
        assert retrieve(materials, "") == []

    def test_empty_library_returns_nothing(self):
        assert retrieve([], "任何查询") == []

    def test_scores_are_ordered(self, materials):
        hits = retrieve(materials, "碳正离子 重排 迁移", limit=5)
        scores = [s for _, s in hits]
        assert scores == sorted(scores, reverse=True)

    def test_limit_respected(self, materials):
        hits = retrieve(materials, "碳 反应 加成 取代", limit=2)
        assert len(hits) <= 2

    def test_rejects_bad_limit(self, materials):
        with pytest.raises(ValueError):
            retrieve(materials, "x", limit=0)

    def test_leading_margin_is_large(self, materials):
        """正确的段落不该只是「险胜」—— 领先幅度太小说明查询串有问题。"""
        hits = retrieve(materials, "迁移方向由什么决定 碳正离子", limit=2)
        assert len(hits) >= 1
        if len(hits) == 2:
            assert hits[0][1] > hits[1][1] * 2


class TestBm25:
    def test_returns_one_score_per_material(self, materials):
        assert len(bm25_scores(materials, "碳")) == len(materials)

    def test_no_query_tokens_gives_zeros(self, materials):
        assert set(bm25_scores(materials, "")) == {0.0}


# --------------------------------------------------------------------------
# 查询串构造（回归：用 id 会检索错）
# --------------------------------------------------------------------------


class TestQueryConstruction:
    def test_uses_chinese_titles_not_ids(self, problems, graph):
        """id 是英文 slug，资料是中文 —— 用 id 会灌噪声。"""
        problem = next(p for p in problems if p.id == "p003")
        query = build_material_query(problem, concepts_path=CONCEPTS)

        title = graph.get("aromaticity").title
        assert title in query
        assert "aromaticity" not in query

    def test_includes_statement(self, problems):
        problem = problems[0]
        assert problem.statement[:50] in build_material_query(problem)

    def test_includes_attempt_text(self, problems):
        query = build_material_query(problems[0], attempt_text="我卡在迁移方向")
        assert "我卡在迁移方向" in query

    def test_degrades_without_graph(self, problems):
        """图谱不可用时退回 id，至少不比空查询差。"""
        problem = next(p for p in problems if p.concepts)
        query = build_material_query(problem, concepts_path=Path("nope.yaml"))
        assert problem.concepts[0] in query


class TestEndToEndRetrieval:
    """每道题都必须能检索到相关段落 —— 这是功能是否真的可用的判据。"""

    @pytest.mark.parametrize(
        "problem_id,keyword",
        [
            ("p001", "重排"),
            ("p002", "共轭二烯"),
            ("p003", "芳香"),
            ("p005", "自由基"),
            ("p006", "周环"),
            ("p007", "重排"),
            ("p008", "坎尼扎罗"),
            ("p009", "硼氢化"),
            ("p010", "软硬"),
        ],
    )
    def test_top_hit_matches_topic(self, materials, problems, problem_id, keyword):
        problem = next(p for p in problems if p.id == problem_id)
        query = build_material_query(problem, concepts_path=CONCEPTS)
        hits = retrieve(materials, query, limit=1)
        assert hits, f"{problem_id} 什么都没检索到"
        material, _ = hits[0]
        haystack = material.heading + material.text
        assert keyword in haystack, (
            f"{problem_id} 的 top-1 是「{material.heading}」，不含关键词「{keyword}」"
        )
