"""每日计划测试。

这一页的职责是**替学生做决定**，所以测试重点是决策规则本身：
优先级对不对、会不会超量、说不说得清理由。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from lifeng.models import load_problems
from lifeng.planner import DAILY_TARGET, build_daily_plan, plan_summary
from lifeng.review import PENALTY_INTERVAL, apply_outcome
from lifeng.store import Store

TODAY = date(2026, 3, 1)


@pytest.fixture
def problems():
    return load_problems("problems")


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "p.db") as s:
        yield s


def _finish(store, problem_id, *, student="S1", outcome=1.0, on=TODAY):
    return apply_outcome(
        store,
        student_code=student,
        problem_id=problem_id,
        outcome=outcome,
        today=on,
    )


class TestPriority:
    def test_fresh_student_gets_new_problems(self, store, problems):
        plan = build_daily_plan(store, problems, "S1", today=TODAY)
        assert len(plan.items) == DAILY_TARGET
        assert all(i.kind == "new" for i in plan.items)

    def test_due_reviews_come_first(self, store, problems):
        """答错 → 明天到期 → 明天应先复习它，而不是继续推新题。"""
        _finish(store, problems[0].id, outcome=0.0)

        plan = build_daily_plan(store, problems, "S1", today=TODAY + timedelta(days=1))

        assert plan.items[0].problem_id == problems[0].id
        assert plan.items[0].kind == "review"
        assert plan.review_count >= 1

    def test_not_due_yet_is_not_reviewed(self, store, problems):
        """无提示解出 → 间隔 3 天 → 明天不该复习。"""
        _finish(store, problems[0].id, outcome=1.0)

        plan = build_daily_plan(store, problems, "S1", today=TODAY + timedelta(days=1))

        assert all(i.kind != "review" for i in plan.items)

    def test_falls_back_to_weakest_when_no_new(self, store, problems):
        """全部做过且都没到期 → 回去补最弱的那个。"""
        for p in problems:
            _finish(store, p.id, outcome=1.0)
        # 把一道压到很弱
        store.upsert_review_state(
            "S1",
            problems[3].id,
            mastery=0.06,
            next_review=(TODAY + timedelta(days=90)).isoformat(),
            interval_days=60,
            review_count=3,
        )

        plan = build_daily_plan(store, problems, "S1", today=TODAY)

        assert plan.items, "应至少推一道补弱"
        assert plan.items[0].problem_id == problems[3].id
        assert plan.items[0].kind == "weakest"


class TestShape:
    def test_respects_target(self, store, problems):
        plan = build_daily_plan(store, problems, "S1", today=TODAY, target=2)
        assert len(plan.items) == 2

    def test_never_exceeds_available_problems(self, store):
        small = load_problems("problems")[:2]
        plan = build_daily_plan(store, small, "S1", today=TODAY, target=10)
        assert len(plan.items) == 2

    def test_no_duplicates(self, store, problems):
        _finish(store, problems[0].id, outcome=0.0)
        plan = build_daily_plan(store, problems, "S1", today=TODAY + timedelta(days=1))
        ids = [i.problem_id for i in plan.items]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("bad", [0, -1])
    def test_rejects_bad_target(self, store, problems, bad):
        with pytest.raises(ValueError):
            build_daily_plan(store, problems, "S1", today=TODAY, target=bad)

    def test_empty_problem_bank(self, store):
        plan = build_daily_plan(store, [], "S1", today=TODAY)
        assert plan.is_empty
        assert "没有任务" in plan.headline


class TestExplainability:
    """每条任务都要说清为什么 —— 不讲理由的强制安排只会让人抵触。"""

    def test_every_item_has_reason(self, store, problems):
        _finish(store, problems[0].id, outcome=0.0)
        plan = build_daily_plan(store, problems, "S1", today=TODAY + timedelta(days=1))
        for item in plan.items:
            assert item.reason.strip(), f"{item.problem_id} 没有说明理由"

    def test_headline_matches_composition(self, store, problems):
        plan = build_daily_plan(store, problems, "S1", today=TODAY)
        assert plan.new_count == len(plan.items)
        assert "新题" in plan.headline

    def test_review_headline_when_only_reviews(self, store, problems):
        for p in problems[:DAILY_TARGET]:
            _finish(store, p.id, outcome=0.0)
        plan = build_daily_plan(store, problems, "S1", today=TODAY + timedelta(days=1))
        assert plan.review_count == DAILY_TARGET
        assert "复习" in plan.headline

    def test_summary_is_compact(self, store, problems):
        plan = build_daily_plan(store, problems, "S1", today=TODAY)
        summary = plan_summary(plan)
        assert summary.count("(") == len(plan.items)


class TestIsolation:
    def test_students_do_not_affect_each_other(self, store, problems):
        for p in problems:
            _finish(store, p.id, student="S1", outcome=1.0)

        plan_s2 = build_daily_plan(store, problems, "S2", today=TODAY)

        assert plan_s2.new_count == len(plan_s2.items), "S2 是新学生，应全是新题"
