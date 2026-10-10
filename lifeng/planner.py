"""砺锋 v2 —— 每日计划。

**它解决什么问题**：复习调度、掌握度、题库都在了，但最后还是学生自己
决定"今天做什么"。对卡住的人来说，这个决定本身就是负担 ——
他会挑最熟的那道做，或者干脆不做。

所以这里让**系统来定**：每天推一组任务，并说清每条为什么排进来。

## 排任务的优先级

1. **到期复习** —— 间隔到了，不复习就会忘（SM-2 的结论）
2. **没做过的新题** —— 复习队列不满时，往前推进度
3. **掌握度最低的已做题** —— 没新题了，就回去补最弱的那块

**为什么不按"学生想做什么"排**：那正是我们要绕开的。

## 可解释性

每条任务都带 ``reason``，界面上直接显示。学生可以不认同系统，
但至少知道它在想什么 —— 不讲理由的强制安排只会让人抵触。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from lifeng.models import Problem
from lifeng.review import MAX_REVIEW_ITEMS_PER_SESSION, due_for_student, mastery_label

#: 每天推几道。竞赛生一天做 3 道机理题是可持续的量，
#: 推太多会让人直接放弃。
DAILY_TARGET = 3


@dataclass(frozen=True)
class PlanItem:
    problem_id: str
    kind: str      # review | new | weakest
    reason: str


@dataclass(frozen=True)
class DailyPlan:
    items: tuple[PlanItem, ...]
    review_count: int
    new_count: int
    weakest_count: int
    headline: str

    @property
    def is_empty(self) -> bool:
        return not self.items

    def count_of(self, kind: str) -> int:
        return sum(1 for i in self.items if i.kind == kind)


def build_daily_plan(
    store,
    problems: list[Problem],
    student_code: str,
    *,
    today: date | None = None,
    target: int = DAILY_TARGET,
) -> DailyPlan:
    """排出今天的任务。

    纯编排，不做 IO 之外的事；``store`` 只用来读状态。
    """
    reference = today or date.today()
    if target < 1:
        raise ValueError(f"target 至少为 1，收到 {target}")

    available = {p.id: p for p in problems}
    planned: list[PlanItem] = []
    taken: set[str] = set()

    # 1. 到期复习
    for row in due_for_student(store, student_code, today=reference):
        if len(planned) >= target:
            break
        pid = str(row["problem_id"])
        if pid in taken or pid not in available:
            continue
        mastery = float(row["mastery"])
        planned.append(
            PlanItem(
                problem_id=pid,
                kind="review",
                reason=f"复习到期（{mastery_label(mastery)}，掌握度 {mastery:.2f}）",
            )
        )
        taken.add(pid)

    # 2. 没做过的新题
    if len(planned) < target:
        done = {str(r["problem_id"]) for r in store.student_overview(student_code)}
        for problem in problems:
            if len(planned) >= target:
                break
            if problem.id in done or problem.id in taken:
                continue
            planned.append(
                PlanItem(
                    problem_id=problem.id,
                    kind="new",
                    reason="还没做过",
                )
            )
            taken.add(problem.id)

    # 3. 掌握度最低的已做题
    if len(planned) < target:
        overview = sorted(
            store.student_overview(student_code), key=lambda r: float(r["mastery"])
        )
        for row in overview:
            if len(planned) >= target:
                break
            pid = str(row["problem_id"])
            if pid in taken or pid not in available:
                continue
            mastery = float(row["mastery"])
            planned.append(
                PlanItem(
                    problem_id=pid,
                    kind="weakest",
                    reason=f"最弱的一项（{mastery_label(mastery)}，掌握度 {mastery:.2f}）",
                )
            )
            taken.add(pid)

    review_count = sum(1 for i in planned if i.kind == "review")
    new_count = sum(1 for i in planned if i.kind == "new")
    weakest_count = sum(1 for i in planned if i.kind == "weakest")

    if not planned:
        headline = "今天没有任务 —— 题库空了或都还没标注。"
    elif review_count and not new_count:
        headline = f"今天先清 {review_count} 道到期复习。"
    elif new_count == len(planned):
        headline = f"今天做 {new_count} 道新题。"
    else:
        headline = (
            f"今天 {review_count} 道复习 + {new_count} 道新题"
            + (f" + {weakest_count} 道补弱" if weakest_count else "")
            + "。"
        )

    return DailyPlan(
        items=tuple(planned),
        review_count=review_count,
        new_count=new_count,
        weakest_count=weakest_count,
        headline=headline,
    )


def plan_summary(plan: DailyPlan) -> str:
    """给界面用的单行摘要。"""
    if plan.is_empty:
        return "无任务"
    return " · ".join(f"{i.problem_id}({i.kind})" for i in plan.items)


__all__ = [
    "DAILY_TARGET",
    "DailyPlan",
    "PlanItem",
    "build_daily_plan",
    "plan_summary",
    "MAX_REVIEW_ITEMS_PER_SESSION",
    "mastery_label",
]
