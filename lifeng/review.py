"""砺锋 v2 —— 间隔复习调度（SM-2）。

**来源说明**：本模块由「活书 learning-agent」的 ``core/scheduler.py``
（MIT 许可）移植并改写，适配本项目的领域模型。间隔表与推进规则保留不变，
改动在两处：

1. **复习单元**：原实现是「知识点」，本实现是**题目** ——
   v1 的题库就是题目，且题目内部已用 ``skeleton.pitfalls`` 表达了卡点结构。
2. **掌握度由谁更新**：原实现自己算一个加减增量，本实现改由
   :mod:`lifeng.mastery` 统一更新。**两处各算一套会互相打架**，
   掌握度只能有单一属主。

**为什么需要它**：学生做错或卡住的题若不安排重做，就会停在"当时会了"。
间隔复习把这个动作自动化。

**判分来源**：outcome 由「学生需要第几层提示才突破」推导，
而不是靠自评 —— 自评会系统性偏高。见 :func:`outcome_from_levels`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from lifeng.mastery import INITIAL_MASTERY as _M_INITIAL
from lifeng.mastery import update_mastery

#: 间隔表（天），指数增长。答对一次前进一档。
DEFAULT_INTERVALS: tuple[int, ...] = (1, 3, 7, 14, 30, 60, 120)

#: 新题目的初始掌握度（与 mastery 模块保持一致）。
INITIAL_MASTERY = _M_INITIAL

#: 首次学习后的间隔（天）。
DEFAULT_FIRST_INTERVAL = 1

#: 答错后的回炉间隔（天）—— 不让弱项被推远。
PENALTY_INTERVAL = 1

#: 一次复习会话最多推几题 —— 防止一次堆几十道把人压垮。
MAX_REVIEW_ITEMS_PER_SESSION = 8


@dataclass(frozen=True)
class ScheduleResult:
    next_review: date
    interval_days: int
    new_mastery: float
    mastery_delta: float
    review_count: int


def outcome_from_levels(*, solved: bool, max_level_used: int) -> float:
    """把「用了第几层提示」翻译成 SM-2 需要的 outcome。

    ====================  =========  ==========================
    情况                    outcome    含义
    ====================  =========  ==========================
    未解出                            0.0
    用了第 4 层（给答案）               0.0   被告知，不算掌握
    在第 3 层突破                      0.5   提示已相当具体
    在第 1~2 层突破                     1.0   方向性提示就够
    完全无提示解出                      1.0
    ====================  =========  ==========================

    用「提示层级」而非「对错」判分：本项目里学生几乎总能改对，
    区别只在于**需要多少帮助**。那才是掌握度的信号。
    """
    if not solved:
        return 0.0
    if max_level_used >= 4:
        return 0.0
    if max_level_used >= 3:
        return 0.5
    return 1.0


def next_interval(outcome: float, *, previous_interval: int = 0) -> int:
    """决定下次间隔（天）。

    **档位由「上次间隔」推导，而不是由「复习次数」。**

    这是对原实现的一处修正。活书原版用 ``review_count + 1`` 定位档位，
    后果是：学生答错回炉（间隔复位到 1 天）后，只要再答对一次，
    档位仍按累计次数往前冲 —— 累计 6 次的话直接跳到 120 天，
    等于把回炉白做了。

    改用上次间隔推导后，回炉会把档位一起复位，重新从底部爬。
    这也让本函数不需要读复习次数，成为纯粹的状态机。
    """
    if outcome < 0.4:
        # 答错：回炉到最短间隔
        return PENALTY_INTERVAL
    if outcome < 0.8:
        # 部分对：退一档，而不是前进
        return DEFAULT_INTERVALS[max(0, _interval_index(previous_interval) - 1)]

    # 答对：前进一档（以当前所处档位为基准）
    return DEFAULT_INTERVALS[
        min(_interval_index(previous_interval) + 1, len(DEFAULT_INTERVALS) - 1)
    ]


def compute_next_review(
    current_mastery: float,
    outcome: float,
    *,
    mode: str = "whitebox",
    review_count: int = 0,
    previous_interval: int = 0,
    today: date | None = None,
) -> ScheduleResult:
    """计算新掌握度与下次复习时间。

    掌握度交给 :func:`lifeng.mastery.update_mastery`，
    间隔由 :func:`next_interval`；本函数只做编排。

    Raises:
        ValueError: outcome 或 mastery 越界。
    """
    if not 0.0 <= outcome <= 1.0:
        raise ValueError(f"outcome 必须在 [0, 1] 范围内，实际 {outcome}")

    update = update_mastery(current_mastery, outcome, mode=mode)
    interval_days = next_interval(outcome, previous_interval=previous_interval)

    # 掌握度很高时允许跳一档 —— 避免明明已经很熟还按原节奏复习
    if outcome >= 0.8 and update.new_mastery > 0.8:
        idx = min(
            _interval_index(interval_days) + 1, len(DEFAULT_INTERVALS) - 1
        )
        interval_days = DEFAULT_INTERVALS[idx]

    reference = today or datetime.now().date()
    return ScheduleResult(
        next_review=reference + timedelta(days=interval_days),
        interval_days=interval_days,
        new_mastery=update.new_mastery,
        mastery_delta=update.delta,
        review_count=review_count + 1,
    )


def _interval_index(days: int) -> int:
    """间隔天数在间隔表中的档位。"""
    if days <= 0:
        return 0
    for i, d in enumerate(DEFAULT_INTERVALS):
        if days <= d:
            return i
    return len(DEFAULT_INTERVALS) - 1


def initial_schedule(
    mastery: float = INITIAL_MASTERY, *, today: date | None = None
) -> ScheduleResult:
    """新题目的初始复习计划：明天第一次复习。"""
    reference = today or datetime.now().date()
    return ScheduleResult(
        next_review=reference + timedelta(days=DEFAULT_FIRST_INTERVAL),
        interval_days=DEFAULT_FIRST_INTERVAL,
        new_mastery=mastery,
        mastery_delta=0.0,
        review_count=0,
    )


def mastery_label(mastery: float) -> str:
    """把掌握度翻译成给学生看的说法。

    ⚠️ **阈值是暂定的，不是标定过的分级。**

    实测本模型在连续答对时的增长很慢（每次约 +0.04），
    从初始 0.15 到 0.40 需要约 6 次全对复习。因此阈值按这个量程取，
    而不是按 [0,1] 的直觉取。真实数据积累后应重新校准。

    另：界面上**优先展示「下次复习间隔」**（具体、可行动），
    掌握度标签只是辅助 —— 掌握度本质是调度用的相对量，不是测量值。
    """
    if mastery >= 0.40:
        return "稳"
    if mastery >= 0.25:
        return "还行"
    return "生"


# --------------------------------------------------------------------------
# 与存储层的编排
#
# 存储层保持「笨」（纯读写 review_state），调度编排放在这里 ——
# 反过来会让存储层依赖领域逻辑，层次颠倒。
# --------------------------------------------------------------------------


def apply_outcome(
    store,
    *,
    student_code: str,
    problem_id: str,
    outcome: float,
    mode: str = "whitebox",
    today: date | None = None,
) -> ScheduleResult:
    """读出该生该题的复习状态 → 计算 → 写回。"""
    row = store.get_review_state(student_code, problem_id)
    if row is None:
        current, count, previous_interval = INITIAL_MASTERY, 0, 0
    else:
        current = float(row["mastery"])
        count = int(row["review_count"])
        previous_interval = int(row["interval_days"])

    result = compute_next_review(
        current,
        outcome,
        mode=mode,
        review_count=count,
        previous_interval=previous_interval,
        today=today,
    )
    store.upsert_review_state(
        student_code,
        problem_id,
        mastery=result.new_mastery,
        next_review=result.next_review.isoformat(),
        interval_days=result.interval_days,
        review_count=result.review_count,
    )
    return result


def due_for_student(
    store, student_code: str, *, today: date | None = None
) -> list:
    """今天该复习的题目（最弱的在前，最多 MAX_REVIEW_ITEMS_PER_SESSION 道）。"""
    reference = (today or datetime.now().date()).isoformat()
    return store.due_reviews(
        student_code, on_date=reference, limit=MAX_REVIEW_ITEMS_PER_SESSION
    )
