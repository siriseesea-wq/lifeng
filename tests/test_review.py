"""复习调度与掌握度模型测试。

两个模块都从「活书 learning-agent」（MIT）移植，因此测试的重点是
**移植后的行为契约**，而不是原实现的内部细节：

- outcome 由提示层级推导（不是自评）
- 答错必须回炉，不能把弱项推远
- 掌握度有单一属主：review 不得自己另算一套
"""

from __future__ import annotations

from datetime import date

import pytest

from lifeng.mastery import (
    BLACKBOX_DIFFICULTY,
    MASTERY_MAX,
    MASTERY_MIN,
    WHITEBOX_DIFFICULTY,
    probability_correct,
    update_mastery,
)
from lifeng.review import (
    DEFAULT_INTERVALS,
    PENALTY_INTERVAL,
    compute_next_review,
    initial_schedule,
    mastery_label,
    next_interval,
    outcome_from_levels,
)

TODAY = date(2026, 1, 1)


# --------------------------------------------------------------------------
# outcome 推导
# --------------------------------------------------------------------------


class TestOutcomeFromLevels:
    @pytest.mark.parametrize("level", [0, 1, 2])
    def test_directional_hints_score_full(self, level: int) -> None:
        """第 1~2 层是方向性提示，突破说明是真的会。"""
        assert outcome_from_levels(solved=True, max_level_used=level) == 1.0

    def test_level3_is_partial(self) -> None:
        assert outcome_from_levels(solved=True, max_level_used=3) == 0.5

    def test_level4_scores_zero(self) -> None:
        """用了第 4 层（给答案）等于被告知，不算掌握。"""
        assert outcome_from_levels(solved=True, max_level_used=4) == 0.0

    def test_unsolved_scores_zero(self) -> None:
        assert outcome_from_levels(solved=False, max_level_used=1) == 0.0

    def test_unsolved_with_high_level_scores_zero(self) -> None:
        assert outcome_from_levels(solved=False, max_level_used=4) == 0.0


# --------------------------------------------------------------------------
# 间隔
# --------------------------------------------------------------------------


class TestNextInterval:
    def test_incorrect_returns_penalty_interval(self) -> None:
        """答错必须回炉 —— 不能把弱项推到很远的将来。"""
        assert next_interval(0.0, previous_interval=60) == PENALTY_INTERVAL

    def test_partial_steps_back(self) -> None:
        """部分对应当退一档，而不是前进。"""
        forward = next_interval(1.0, previous_interval=7)
        backward = next_interval(0.5, previous_interval=7)
        assert backward < forward

    def test_correct_advances_one_step(self) -> None:
        """答对前进一档 —— 从 3 天应到 7 天，而不是跳级。"""
        assert next_interval(1.0, previous_interval=1) == 3
        assert next_interval(1.0, previous_interval=3) == 7

    def test_capped_at_last_interval(self) -> None:
        assert next_interval(1.0, previous_interval=120) == DEFAULT_INTERVALS[-1]

    def test_ladder_restarts_after_failure(self) -> None:
        """回归：原实现用「复习次数」定位档位，导致回炉后答对一次就跳级。

        正确行为是档位随上次间隔一起复位，重新从底部爬。
        """
        # 已经爬到 30 天档位
        assert next_interval(1.0, previous_interval=30) == 60

        # 答错 → 回炉
        assert next_interval(0.0, previous_interval=30) == PENALTY_INTERVAL

        # 回炉后答对 → 应从底部重新开始（3 天），而不是跳回 60 天
        assert next_interval(1.0, previous_interval=PENALTY_INTERVAL) == 3


# --------------------------------------------------------------------------
# 调度
# --------------------------------------------------------------------------


class TestComputeNextReview:
    def test_correct_raises_mastery(self) -> None:
        result = compute_next_review(0.20, 1.0, today=TODAY)
        assert result.new_mastery > 0.20
        assert result.mastery_delta > 0

    def test_incorrect_lowers_mastery(self) -> None:
        result = compute_next_review(0.80, 0.0, today=TODAY)
        assert result.new_mastery < 0.80
        assert result.mastery_delta < 0

    def test_mastery_respects_bounds(self) -> None:
        high = compute_next_review(MASTERY_MAX, 1.0, today=TODAY)
        low = compute_next_review(MASTERY_MIN, 0.0, today=TODAY)
        assert high.new_mastery <= MASTERY_MAX
        assert low.new_mastery >= MASTERY_MIN

    def test_next_review_is_after_today(self) -> None:
        result = compute_next_review(0.2, 1.0, today=TODAY)
        assert result.next_review > TODAY

    def test_review_count_increments(self) -> None:
        assert compute_next_review(0.2, 1.0, review_count=4, today=TODAY).review_count == 5

    @pytest.mark.parametrize("bad", [-0.1, 1.1])
    def test_rejects_bad_outcome(self, bad: float) -> None:
        with pytest.raises(ValueError):
            compute_next_review(0.3, bad, today=TODAY)

    def test_mastery_matches_single_owner(self) -> None:
        """掌握度必须由 mastery 模块算，review 不得自己另算一套。"""
        expected = update_mastery(0.30, 1.0).new_mastery
        actual = compute_next_review(0.30, 1.0, today=TODAY).new_mastery
        assert actual == pytest.approx(expected)

    def test_initial_schedule_is_tomorrow(self) -> None:
        result = initial_schedule(today=TODAY)
        assert result.interval_days == 1
        assert (result.next_review - TODAY).days == 1


class TestMasteryLabel:
    def test_labels_are_reachable(self) -> None:
        """阈值必须落在模型真实能达到的量程内，否则学生永远只看到「生」。"""
        mastery = 0.15
        labels = set()
        for i in range(12):
            mastery = compute_next_review(mastery, 1.0, review_count=i, today=TODAY).new_mastery
            labels.add(mastery_label(mastery))
        assert "还行" in labels or "稳" in labels, (
            f"连续 12 次全对仍只得到 {labels} —— 阈值与模型量程不匹配"
        )

    def test_ordering(self) -> None:
        assert mastery_label(0.05) == "生"
        assert mastery_label(0.60) == "稳"


# --------------------------------------------------------------------------
# 掌握度模型
# --------------------------------------------------------------------------


class TestProbabilityCorrect:
    def test_within_bounds(self) -> None:
        for mastery in (0.0, 0.25, 0.5, 0.75, 1.0):
            p = probability_correct(mastery)
            assert 0.0 <= p <= 1.0

    def test_monotonic_in_mastery(self) -> None:
        assert probability_correct(0.2) < probability_correct(0.8)

    def test_whitebox_is_harder_than_blackbox(self) -> None:
        """同样掌握度下，要求"真懂"的题答对概率更低。"""
        assert WHITEBOX_DIFFICULTY > BLACKBOX_DIFFICULTY
        assert probability_correct(0.5, mode="whitebox") < probability_correct(0.5, mode="blackbox")

    def test_rejects_bad_mode(self) -> None:
        with pytest.raises(ValueError):
            probability_correct(0.5, mode="greybox")


class TestUpdateMastery:
    def test_correct_increases(self) -> None:
        assert update_mastery(0.3, 1.0).delta > 0

    def test_incorrect_decreases(self) -> None:
        assert update_mastery(0.6, 0.0).delta < 0

    def test_high_confidence_error_moves_more(self) -> None:
        """高掌握度却答错 → 修正更大（surprise 效应）。"""
        high = abs(update_mastery(0.85, 0.0).delta)
        low = abs(update_mastery(0.30, 0.0).delta)
        assert high > low

    def test_low_mastery_correct_is_damped(self) -> None:
        """低掌握度答对 → 奖励打折（可能是蒙的）。"""
        cautious = update_mastery(0.15, 1.0).delta
        normal = update_mastery(0.55, 1.0).delta
        assert cautious < normal

    def test_blackbox_moves_less(self) -> None:
        wb = abs(update_mastery(0.5, 0.0, mode="whitebox").delta)
        bb = abs(update_mastery(0.5, 0.0, mode="blackbox").delta)
        assert bb < wb

    @pytest.mark.parametrize("bad", [-0.01, 1.01])
    def test_rejects_bad_outcome(self, bad: float) -> None:
        with pytest.raises(ValueError):
            update_mastery(0.5, bad)

    def test_rejects_bad_mastery(self) -> None:
        with pytest.raises(ValueError):
            update_mastery(1.5, 1.0)

    def test_reports_prediction(self) -> None:
        result = update_mastery(0.5, 1.0)
        assert 0.0 <= result.p_correct <= 1.0
        assert result.old_mastery == 0.5
