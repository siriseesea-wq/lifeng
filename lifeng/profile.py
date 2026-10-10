"""砺锋 v2 —— 学生画像与卡点诊断。

**覆盖两个引用项目的功能**：

- DeepTutor 的**长期记忆 / 综合判断**：把一次次作答与掌握度，
  归结成「这个学生哪里强、哪里弱、缺哪块前置」的一句话判断。
- OI.SKILL 的**错误模式诊断**：不只统计对错，而是统计
  **他总卡在哪一类地方**。

## 三层记忆在这里的落点

======================  ================================================
层                      落在哪
======================  ================================================
L1 短期（当前会话）       Streamlit ``session_state``
L2 长期事实（跨会话）     ``store.db`` 的 attempts / review_state
**L3 综合判断**          **本模块** —— 从 L2 推导出的画像
======================  ================================================

本模块**不写数据**，只读 L2 并推导。写成纯函数是为了可测：
画像的口径是产品判断，必须有测试钉住，不能每次改一点。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lifeng.concepts import ConceptGraph
from lifeng.models import Problem
from lifeng.review import (
    MASTERED_THRESHOLD,
    WEAK_THRESHOLD,
    mastery_label,
)

# 阈值从 review 导入，不在这里重复定义 —— 单一属主。
# 兼容旧引用：本模块仍导出这两个名字。



@dataclass(frozen=True)
class ConceptStanding:
    """一个知识点的掌握状况（由它下面的题目聚合而来）。"""

    concept_id: str
    title: str
    mastery: float
    problem_ids: tuple[str, ...]

    @property
    def label(self) -> str:
        return mastery_label(self.mastery)


@dataclass(frozen=True)
class StudentProfile:
    student_code: str
    standings: tuple[ConceptStanding, ...] = ()
    mastered: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()
    untouched: tuple[str, ...] = ()
    #: 薄弱知识点在**前置链上**未掌握的全部知识点（传递闭包）。
    #: 注意这不是「要先补的清单」—— 其中有些本身还没到能学的时候。
    #: 真正可执行的清单是 :attr:`next_concepts`。
    missing_prerequisites: tuple[str, ...] = ()
    #: 前置已满足、可以直接开始学的知识点 —— **这才是有序的下一步**。
    next_concepts: tuple[str, ...] = ()
    #: 平均「用了第几层提示」；0 表示全靠自己
    hint_dependency: float = 0.0
    #: 卡点分布：(卡点序号, 次数)，按次数降序
    stall_patterns: tuple[tuple[int, int], ...] = ()
    #: 图谱里有、但题库里还没有题的知识点
    uncovered_concepts: tuple[str, ...] = ()
    headline: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.standings


def _concept_mastery(
    concept_id: str,
    problems: list[Problem],
    mastery_by_problem: dict[str, float],
) -> tuple[float, tuple[str, ...]]:
    """概念掌握度 = 它下面各题掌握度的平均。

    为什么取平均而不是取最低：取最低会让一个知识点永远「弱」，
    只要它有一道难题没做出来。平均更能反映「大体上会不会」。
    """
    ids = tuple(
        p.id for p in problems if concept_id in p.concepts and p.id in mastery_by_problem
    )
    if not ids:
        return 0.0, ()
    return sum(mastery_by_problem[i] for i in ids) / len(ids), ids


def build_profile(
    store,
    problems: list[Problem],
    graph: ConceptGraph,
    student_code: str,
) -> StudentProfile:
    """从 L2 数据推导 L3 判断。只读，不写。"""
    overview = store.student_overview(student_code)
    mastery_by_problem = {str(r["problem_id"]): float(r["mastery"]) for r in overview}

    # ── 概念层面的掌握状况 ──────────────────────────────────────────
    standings: list[ConceptStanding] = []
    for cid, concept in sorted(graph.concepts.items()):
        mastery, ids = _concept_mastery(cid, problems, mastery_by_problem)
        if ids:
            standings.append(
                ConceptStanding(
                    concept_id=cid,
                    title=concept.title,
                    mastery=mastery,
                    problem_ids=ids,
                )
            )

    mastered = tuple(s.concept_id for s in standings if s.mastery >= MASTERED_THRESHOLD)
    weak = tuple(s.concept_id for s in standings if s.mastery < WEAK_THRESHOLD)

    # 「未触及」= 没有对应题目的知识点，**不是**「没掌握」。
    # 早先写成了 set(graph) - known（known 只含已稳），结果一个都没稳时
    # 全部 19 个都算「未触及」，和「薄弱 10」加起来超过总数 —— 自相矛盾。
    # 语义应当是：做过题才有掌握度，没做过题的才算未触及。
    touched = {s.concept_id for s in standings}
    untouched = tuple(sorted(set(graph.concepts) - touched))

    # ── 前置缺口：薄弱点的根在哪 ────────────────────────────────────
    known = set(mastered)
    gaps: set[str] = set()
    for cid in weak:
        gaps.update(graph.missing_prerequisites(cid, known))
    missing = tuple(sorted(gaps - known))

    # ── 下一步该学什么 ──────────────────────────────────────────────
    next_concepts = tuple(graph.unlockable(known))

    # ── 图谱有、题库无的知识点 ──────────────────────────────────────
    covered = {c for p in problems for c in p.concepts}
    uncovered = tuple(sorted(set(graph.concepts) - covered))

    # ── 提示依赖与卡点分布（错误模式诊断）──────────────────────────
    attempts = [a for a in store.iter_attempts() if a["student_code"] == student_code]
    hinted = [int(a["level_requested"]) for a in attempts if int(a["level_requested"]) >= 1]
    hint_dependency = sum(hinted) / len(hinted) if hinted else 0.0

    stall_counts: dict[int, int] = {}
    for attempt in attempts:
        index = attempt["pitfall_index"]
        if index is None:
            continue
        stall_counts[int(index)] = stall_counts.get(int(index), 0) + 1
    stall_patterns = tuple(
        sorted(stall_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )

    headline = _headline(
        len(standings),
        weak,
        graph,
        next_concepts,
        hint_dependency,
        untouched_count=len(set(graph.concepts)) - len(standings),
    )

    return StudentProfile(
        student_code=student_code,
        standings=tuple(standings),
        mastered=mastered,
        weak=weak,
        untouched=untouched,
        missing_prerequisites=missing,
        next_concepts=next_concepts,
        hint_dependency=hint_dependency,
        stall_patterns=stall_patterns,
        uncovered_concepts=uncovered,
        headline=headline,
    )


def _headline(
    tracked: int,
    weak: tuple[str, ...],
    graph: ConceptGraph,
    next_concepts: tuple[str, ...],
    hint: float,
    *,
    untouched_count: int = 0,
) -> str:
    """一句话画像。刻意给「下一步做什么」，而不是只报数字。

    只报「你有 6 个前置没补」等于没帮上忙 —— 学生依然不知道该动哪一块。
    所以这里直接点名：前置已满足的那几个里，挑最靠前的说。

    ``untouched_count`` 必须报出来：``tracked`` 只统计**碰过的**概念，
    不说清会让你以为「都没问题」，而实际上可能一大半还没开始。
    """
    if tracked == 0:
        return "还没有可用的掌握度数据 —— 先做几道题。"

    parts: list[str] = []
    if not weak:
        parts.append("碰过的知识点都不算生")
    else:
        parts.append(f"{len(weak)} 个知识点还生")
        titles = concept_titles(graph, next_concepts[:3])
        if titles:
            parts.append(f"建议先补：{'、'.join(titles)}")
        else:
            parts.append("但目前没有前置已满足的项可补")

    if untouched_count:
        parts.append(f"另有 {untouched_count} 个知识点还没碰过")

    if hint > 2.5:
        parts.append(f"提示依赖偏高（平均用到第 {hint:.1f} 层）")
    elif hint and hint <= 1.5:
        parts.append("提示用得少，方向性提问就够")

    return "；".join(parts) + "。"


def concept_titles(graph: ConceptGraph, ids: tuple[str, ...]) -> list[str]:
    """把 id 列表转成标题列表，供界面显示。未知 id 原样返回。"""
    out: list[str] = []
    for cid in ids:
        concept = graph.get(cid)
        out.append(concept.title if concept else cid)
    return out
