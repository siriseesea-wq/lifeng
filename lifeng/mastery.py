"""砺锋 v2 —— 掌握度模型。

**来源说明**：本模块由「活书 learning-agent」的 ``core/mastery.py``
（MIT 许可）移植并改写。

**它解决什么问题**：我原先反对照搬 IRT，理由是「单个学生的作答矩阵
无法估计项目参数」。这个反对对**参数拟合**是成立的 ——
但活书的做法是另一件事：**借用 3PL 的更新形式，参数用合理默认值**。

于是得到的是「掌握度随预测误差自适应移动」的行为，而不是一个声称
被标定过的测量模型。这正是单人场景下能站得住的做法。

**单一属主**：掌握度只由本模块更新。``review.py`` 只负责间隔，
不自行改掌握度 —— 两处各算一套会互相打架。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ── 参数（沿用活书默认值）────────────────────────────────────────────

DEFAULT_DISCRIMINATION = 2.0   # a：区分度，越大曲线越陡
DEFAULT_GUESSING = 0.2         # c：猜测概率
DEFAULT_LEARNING_RATE = 0.10   # α：基础学习率
DEFAULT_SLIP = 0.05            # s：粗心上限

MASTERY_MIN = 0.05
MASTERY_MAX = 0.95             # 不取 1.0：留一点不确定性

#: 新题目的初始掌握度。
INITIAL_MASTERY = 0.15

#: 难度参数 b。
#:
#: 本项目沿用原文档的「白箱/黑箱」之分：
#: - 白箱：要求真懂原理（推理题属此类）
#: - 黑箱：会用即可
WHITEBOX_DIFFICULTY = 0.60
BLACKBOX_DIFFICULTY = 0.40

VALID_MODES: tuple[str, ...] = ("whitebox", "blackbox")


@dataclass(frozen=True)
class MasteryUpdate:
    old_mastery: float
    new_mastery: float
    delta: float
    p_correct: float
    outcome: float


def probability_correct(
    mastery: float,
    *,
    difficulty: float | None = None,
    mode: str = "whitebox",
    discrimination: float = DEFAULT_DISCRIMINATION,
    guessing: float = DEFAULT_GUESSING,
) -> float:
    """3PL IRT 的简化形式：``P = c + (1-c) / (1 + exp(-a(θ-b)))``。

    注意：这里的参数是**默认常量**，不是从数据拟合出来的。
    它的用途是让掌握度按"预测误差"更新，而非给出被标定的测量值。
    """
    if mode not in VALID_MODES:
        raise ValueError(f"mode 取值非法：{mode!r}，应为 {VALID_MODES}")
    if difficulty is None:
        difficulty = BLACKBOX_DIFFICULTY if mode == "blackbox" else WHITEBOX_DIFFICULTY

    exponent = -discrimination * (mastery - difficulty)
    exponent = max(-50.0, min(50.0, exponent))  # 防 exp 溢出

    p = guessing + (1.0 - guessing) / (1.0 + math.exp(exponent))
    return max(guessing, min(p, 1.0 - DEFAULT_SLIP))


def update_mastery(
    mastery: float,
    outcome: float,
    *,
    mode: str = "whitebox",
    difficulty: float | None = None,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    discrimination: float = DEFAULT_DISCRIMINATION,
    guessing: float = DEFAULT_GUESSING,
) -> MasteryUpdate:
    """按预测误差更新掌握度：``θ_new = θ_old + α · (outcome − P) · adj``。

    ``adj`` 的作用（沿用活书）：
    - **高掌握度却答错** → 放大修正（高置信错误值得大改）
    - **低掌握度答对** → 缩到 0.7 倍（可能是蒙的）
    - **部分对** → 缩到 0.5 倍（温和调整）
    - 黑箱模式整体波动乘以 0.7
    """
    if not 0.0 <= outcome <= 1.0:
        raise ValueError(f"outcome 必须在 [0, 1] 范围内，实际 {outcome}")
    if not 0.0 <= mastery <= 1.0:
        raise ValueError(f"mastery 必须在 [0, 1] 范围内，实际 {mastery}")

    p_correct = probability_correct(
        mastery,
        difficulty=difficulty,
        mode=mode,
        discrimination=discrimination,
        guessing=guessing,
    )
    adjustment = _compute_adjustment(mastery, outcome, mode)

    new_mastery = mastery + learning_rate * (outcome - p_correct) * adjustment
    new_mastery = max(MASTERY_MIN, min(MASTERY_MAX, new_mastery))

    return MasteryUpdate(
        old_mastery=mastery,
        new_mastery=new_mastery,
        delta=new_mastery - mastery,
        p_correct=p_correct,
        outcome=outcome,
    )


def _compute_adjustment(mastery: float, outcome: float, mode: str) -> float:
    factor = 1.0
    if outcome < 0.5 and mastery > 0.6:
        factor *= 1.5          # 高置信错误
    elif outcome > 0.5 and mastery < 0.3:
        factor *= 0.7          # 低掌握度答对，谨慎奖励
    elif outcome == 0.5:
        factor *= 0.5          # 部分对
    if mode == "blackbox":
        factor *= 0.7
    return factor
