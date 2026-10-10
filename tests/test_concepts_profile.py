"""知识点图与学生画像测试。

这两个模块分别覆盖两个引用项目的功能（活书的知识图谱、
DeepTutor 的综合记忆 + OI.SKILL 的错误模式诊断），
因此测试要钉住的是**判断口径**，不是实现细节。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lifeng.concepts import Concept, ConceptError, ConceptGraph
from lifeng.models import load_problems
from lifeng.review import MASTERED_THRESHOLD, WEAK_THRESHOLD
from lifeng.profile import (
    MASTERED_THRESHOLD,
    WEAK_THRESHOLD,
    build_profile,
    concept_titles,
)
from lifeng.review import apply_outcome
from lifeng.store import Store

TODAY = date(2026, 3, 1)
CONCEPTS = Path("concepts.yaml")


def _graph(tmp_path: Path, body: str) -> ConceptGraph:
    path = tmp_path / "g.yaml"
    path.write_text(body, encoding="utf-8")
    return ConceptGraph.load(path)


@pytest.fixture
def problems():
    return load_problems("problems")


@pytest.fixture
def graph():
    return ConceptGraph.load(CONCEPTS)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "p.db") as s:
        yield s


# --------------------------------------------------------------------------
# 知识点图
# --------------------------------------------------------------------------


class TestLoading:
    def test_project_graph_is_valid(self, graph):
        assert graph.errors == []
        assert len(graph) >= 10

    def test_has_roots(self, graph):
        roots = [c.id for c in graph.concepts.values() if not c.prerequisites]
        assert roots, "至少要有一个无前置的起点，否则无从学起"

    def test_every_problem_concept_exists(self, graph, problems):
        """题目标注的知识点必须真的存在 —— 打错一个字就会静默失效。

        这类错误不会崩，只会让图谱上少一条连线、检索少一个关键词，
        所以必须显式校验。
        """
        missing = sorted({c for p in problems for c in p.concepts if c not in graph})
        assert missing == [], f"题目标注了不存在的知识点：{missing}"

    def test_related_are_symmetric_or_absent(self, graph):
        """related 是横向联系，单向声明即可，但目标必须存在（validate 已查）。"""
        assert graph.validate() == []


class TestValidation:
    def test_detects_cycle(self, tmp_path):
        g = _graph(
            tmp_path,
            """
concepts:
  - {id: a, title: A, prerequisites: [b]}
  - {id: b, title: B, prerequisites: [a]}
""",
        )
        assert any("成环" in e for e in g.errors)

    def test_detects_dangling_prerequisite(self, tmp_path):
        g = _graph(
            tmp_path, "concepts:\n  - {id: a, title: A, prerequisites: [nope]}\n"
        )
        assert any("不存在" in e for e in g.errors)

    def test_detects_dangling_related(self, tmp_path):
        g = _graph(tmp_path, "concepts:\n  - {id: a, title: A, related: [nope]}\n")
        assert any("不存在" in e for e in g.errors)

    def test_detects_self_reference(self, tmp_path):
        g = _graph(tmp_path, "concepts:\n  - {id: a, title: A, prerequisites: [a]}\n")
        assert any("自己" in e for e in g.errors)

    def test_missing_title_is_rejected(self, tmp_path):
        with pytest.raises(ConceptError):
            _graph(tmp_path, "concepts:\n  - {id: a}\n")

    def test_missing_id_is_rejected(self, tmp_path):
        with pytest.raises(ConceptError):
            _graph(tmp_path, "concepts:\n  - {title: A}\n")

    def test_bad_domain_is_rejected(self, tmp_path):
        with pytest.raises(ConceptError):
            _graph(tmp_path, "concepts:\n  - {id: a, title: A, domain: alchemy}\n")

    def test_non_mapping_entry_is_rejected(self, tmp_path):
        with pytest.raises(ConceptError):
            _graph(tmp_path, "concepts:\n  - just-a-string\n")

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConceptError):
            ConceptGraph.load(tmp_path / "nope.yaml")


class TestQueries:
    def test_missing_prerequisites_is_transitive(self, tmp_path):
        g = _graph(
            tmp_path,
            """
concepts:
  - {id: a, title: A}
  - {id: b, title: B, prerequisites: [a]}
  - {id: c, title: C, prerequisites: [b]}
""",
        )
        # c 的直接前置是 b，间接前置还有 a
        assert g.missing_prerequisites("c", set()) == ["a", "b"]
        assert g.missing_prerequisites("c", {"b"}) == ["a"]
        assert g.missing_prerequisites("c", {"a", "b"}) == []

    def test_unlockable_requires_all_prerequisites(self, tmp_path):
        g = _graph(
            tmp_path,
            """
concepts:
  - {id: a, title: A}
  - {id: b, title: B, prerequisites: [a]}
""",
        )
        assert g.unlockable(set()) == ["a"]
        assert g.unlockable({"a"}) == ["b"]
        assert g.unlockable({"a", "b"}) == []

    def test_neighbours_tags_relation_kind(self, tmp_path):
        g = _graph(
            tmp_path,
            """
concepts:
  - {id: a, title: A}
  - {id: b, title: B, prerequisites: [a], related: [c]}
  - {id: c, title: C}
""",
        )
        kinds = dict(g.neighbours("b"))
        assert kinds["a"] == "prereq"
        assert kinds["c"] == "related"

    def test_lookup_helpers(self, graph):
        cid = next(iter(graph.concepts))
        assert graph.get(cid) is not None
        assert cid in graph
        assert graph.get("nope") is None


class TestDot:
    def test_lists_every_node(self, graph):
        dot = graph.to_dot()
        for cid in graph.concepts:
            assert f'"{cid}"' in dot

    def test_emits_prerequisite_edges(self, graph):
        dot = graph.to_dot()
        assert "->" in dot
        # 前置依赖必须画出来，否则图看不出学习顺序
        for cid, concept in graph.concepts.items():
            for prereq in concept.prerequisites:
                assert f'"{prereq}" -> "{cid}"' in dot

    def test_highlights_missing_and_known(self, graph):
        """薄弱与已稳必须画出**明显不同**的样式。

        这里断言语义差异（不同的填充色），而不是写死色值 ——
        配色属于设计令牌，会跟着 theme.py 调整，不该把测试钉在十六进制上。
        """
        cid = next(iter(graph.concepts))
        plain = graph.to_dot()
        missing = graph.to_dot(missing={cid})
        known = graph.to_dot(emphasise={cid})

        def node_line(dot: str) -> str:
            return next(l for l in dot.splitlines() if l.startswith(f'  "{cid}" ['))

        assert node_line(plain) != node_line(missing), "薄弱的样式没有变化"
        assert node_line(plain) != node_line(known), "已稳的样式没有变化"
        assert node_line(missing) != node_line(known), "薄弱与已稳画成了同一个样子"

    def test_escapes_quotes(self):
        g = ConceptGraph(concepts={"a": Concept(id="a", title='有"引号"')})
        assert '\\"' in g.to_dot()


# --------------------------------------------------------------------------
# 学生画像
# --------------------------------------------------------------------------


def _complete(store, problem_id, *, student="S1", outcome=1.0, times=1):
    for _ in range(times):
        apply_outcome(
            store, student_code=student, problem_id=problem_id, outcome=outcome, today=TODAY
        )


class TestProfileEmpty:
    def test_new_student_has_no_standings(self, store, problems, graph):
        profile = build_profile(store, problems, graph, "NEW")
        assert profile.is_empty
        assert "先做几道题" in profile.headline


class TestConceptMastery:
    def test_concept_mastery_averages_its_problems(self, store, problems, graph):
        # p001 与 p007 都考 carbocation-rearrangement，且都属于 S1
        _complete(store, "p001", outcome=0.0)
        _complete(store, "p007", outcome=1.0)

        p1 = build_profile(store, problems, graph, "S1")
        standing = next(s for s in p1.standings if s.concept_id == "carbocation-rearrangement")
        assert standing.problem_ids == ("p001", "p007")
        assert 0.0 < standing.mastery < 1.0

    def test_other_students_records_are_excluded(self, store, problems, graph):
        """概念掌握度只能由**该学生本人**的题聚合出来。"""
        _complete(store, "p001", outcome=0.0)
        _complete(store, "p007", outcome=1.0, student="S2")

        p1 = build_profile(store, problems, graph, "S1")
        standing = next(s for s in p1.standings if s.concept_id == "carbocation-rearrangement")
        assert standing.problem_ids == ("p001",)

    def test_weak_and_mastered_split(self, store, problems, graph):
        _complete(store, "p001", outcome=0.0)
        profile = build_profile(store, problems, graph, "S1")
        assert "carbocation-rearrangement" in profile.weak
        assert "carbocation-rearrangement" not in profile.mastered

    def test_repeated_success_reaches_mastered(self, store, problems, graph):
        _complete(store, "p001", outcome=1.0, times=8)
        _complete(store, "p007", outcome=1.0, times=8)
        profile = build_profile(store, problems, graph, "S1")
        assert "carbocation-rearrangement" in profile.mastered

    def test_thresholds_are_ordered(self):
        assert WEAK_THRESHOLD < MASTERED_THRESHOLD


class TestGaps:
    def test_missing_prerequisites_reported(self, store, problems, graph):
        _complete(store, "p001", outcome=0.0)
        profile = build_profile(store, problems, graph, "S1")
        # 碳正离子重排的前置链上有电子效应、空间效应、碳正离子稳定性
        assert profile.missing_prerequisites

    def test_next_concepts_are_actionable(self, store, problems, graph):
        """next_concepts 的每一项，其前置都必须已掌握 —— 否则不叫「可以学」。"""
        _complete(store, "p001", outcome=0.0)
        profile = build_profile(store, problems, graph, "S1")
        known = set(profile.mastered)
        for cid in profile.next_concepts:
            concept = graph.get(cid)
            assert set(concept.prerequisites) <= known, f"{cid} 的前置还没齐"

    def test_uncovered_concepts_reported(self, store, problems, graph):
        """图谱里有、题库里没题的知识点必须报出来 —— 那是题库的空白。"""
        profile = build_profile(store, problems, graph, "S1")
        covered = {c for p in problems for c in p.concepts}
        assert set(profile.uncovered_concepts) == set(graph.concepts) - covered


class TestDiagnosis:
    def test_hint_dependency_is_mean_level(self, store, problems, graph):
        sid = store.create_session("p001", "S1", "hint")
        for level in (1, 3):
            aid = store.log_attempt(sid, "尝试", level_requested=0)
            store.attach_hint(aid, level, "提示" * 20)
        profile = build_profile(store, problems, graph, "S1")
        assert profile.hint_dependency == 2.0

    def test_hint_dependency_zero_without_hints(self, store, problems, graph):
        sid = store.create_session("p001", "S1", "baseline")
        store.log_attempt(sid, "尝试", level_requested=0)
        profile = build_profile(store, problems, graph, "S1")
        assert profile.hint_dependency == 0.0

    def test_stall_patterns_counted_desc(self, store, problems, graph):
        sid = store.create_session("p001", "S1", "hint")
        for index in (2, 2, 0):
            aid = store.log_attempt(sid, "尝试", level_requested=0)
            store.set_pitfall_index(aid, index)
        profile = build_profile(store, problems, graph, "S1")
        assert profile.stall_patterns[0] == (2, 2)


class TestHeadline:
    def test_mentions_untouched(self, store, problems, graph):
        """只说「碰过的都不算生」会让人误以为全都掌握了。"""
        _complete(store, "p001", outcome=1.0, times=8)
        profile = build_profile(store, problems, graph, "S1")
        assert "还没碰过" in profile.headline

    def test_names_next_step_when_weak(self, store, problems, graph):
        _complete(store, "p001", outcome=0.0)
        profile = build_profile(store, problems, graph, "S1")
        assert "建议先补" in profile.headline


class TestConceptTitles:
    def test_maps_ids_to_titles(self, graph):
        cid = next(iter(graph.concepts))
        assert concept_titles(graph, (cid,)) == [graph.get(cid).title]

    def test_unknown_id_passes_through(self, graph):
        assert concept_titles(graph, ("nope",)) == ["nope"]


class TestProfileCountsAreConsistent:
    """画像上的分类必须互斥且不重不漏 —— 数字自相矛盾比没有数字更糟。

    回归：早先 ``untouched`` 被定义成「没掌握」，于是一个都没稳时
    全部知识点都算「未触及」，与「薄弱」相加超过总数。
    """

    def test_categories_do_not_overlap_or_exceed(self, store, problems, graph):
        _complete(store, "p001", outcome=0.0)
        profile = build_profile(store, problems, graph, "S1")

        mastered = set(profile.mastered)
        weak = set(profile.weak)
        untouched = set(profile.untouched)

        assert not (mastered & weak), "已稳与薄弱重叠"
        assert not (mastered & untouched), "已稳与未触及重叠"
        assert not (weak & untouched), "薄弱与未触及重叠"
        assert len(mastered | weak | untouched) <= len(graph), "分类之和超过知识点总数"

    def test_untouched_means_never_attempted(self, store, problems, graph):
        """做过题的（哪怕很弱）不算「未触及」。"""
        _complete(store, "p003", outcome=0.0)
        profile = build_profile(store, problems, graph, "S1")

        assert "aromaticity" in profile.weak
        assert "aromaticity" not in profile.untouched
