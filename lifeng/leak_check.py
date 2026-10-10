"""砺锋 v2 —— 泄答案校验器（T4）。

C1 的落地：第 1~3 层的提示不得出现 ``key_quantities`` 中的任何词。

本模块是**第二道**防线。第一道是 :mod:`lifeng.hint_engine` 的结构性防泄漏
（层 1~3 的 prompt 里根本不含答案）。这里负责兜住模型从 ``insight``
推断出的间接泄漏，以及措辞层面的意外复现。

**已知局限**：字符串匹配拦不住改述型泄漏（把"频哪酮"说成"那个叔丁基酮"）。
阶段一（n=20）的应对是实验者逐条人工复核全部提示，而不是构建 LLM 判定器。
阶段二若扩大规模，此处是明确的技术债。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from lifeng.models import Problem

#: 掩码字符。用方块而非文字，避免引入新的可读内容。
MASK = "████"

#: 判定为泄漏后，允许的最大重生成次数。
DEFAULT_MAX_ATTEMPTS = 3


class LeakPersistsError(RuntimeError):
    """重试 ``max_attempts`` 次后仍然泄漏。

    调用方（T5）应把它当作**数据信号**呈现，而不是静默吞掉 ——
    持续泄漏说明该题的 prompt 或骨架标注需要人工介入。
    """


# --------------------------------------------------------------------------
# 归一化与检测
# --------------------------------------------------------------------------


def normalize(text: str) -> str:
    """归一化文本以便比较。

    处理三类差异：

    1. **全角/半角** —— ``ＮＦＫＣ`` 把全角字母数字标点折叠为半角
       （中文全角逗号 `，` → `,`）
    2. **大小写** —— ``casefold`` 比 ``lower`` 更彻底（如德语 ß）
    3. **空白** —— 提示里常有换行、缩进、多余空格
    """
    folded = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", folded.casefold())


def find_leaks(hint: str, key_quantities: tuple[str, ...] | list[str]) -> list[str]:
    """返回 ``hint`` 中命中的黑名单词（保留原始写法，便于直接掩码）。"""
    if not hint:
        return []
    normalized_hint = normalize(hint)
    hits: list[str] = []
    for term in key_quantities:
        if not term or not term.strip():
            continue
        if normalize(term) in normalized_hint:
            hits.append(term)
    return hits


def is_leaked(hint: str, key_quantities: tuple[str, ...] | list[str]) -> bool:
    """第 1~3 层输出的判定函数。"""
    return bool(find_leaks(hint, key_quantities))


def mask_terms(hint: str, terms: tuple[str, ...] | list[str]) -> str:
    """把命中词替换为 :data:`MASK`。

    这是保证 C1 成立的**最后手段**，只在重生成仍然失败时使用。
    调用方必须同时记录该次生成为失败（见 :class:`GuardedHint`）。
    """
    masked = hint
    for term in sorted((t for t in terms if t and t.strip()), key=len, reverse=True):
        masked = re.sub(re.escape(term), MASK, masked, flags=re.IGNORECASE)
    return masked


# --------------------------------------------------------------------------
# 骨架标注卫生（T6 的数据质量检查）
# --------------------------------------------------------------------------


def skeleton_leaks_answer(problem: Problem) -> list[str]:
    """检查骨架本身是否写进了答案。

    骨架会被注入 prompt，因此若 ``insight`` 或卡点里出现了
    ``key_quantities``，结构性防泄漏就失效了 —— 模型即使只看 insight
    也能看到答案。

    这是 **T6 标注阶段**的卫生检查：应在标注完成后立即跑一遍，
    而不是等到实验数据出来才发现。
    """
    if problem.skeleton is None:
        return []

    parts = [problem.skeleton.insight]
    for pitfall in problem.skeleton.pitfalls:
        parts.extend([pitfall.trigger, pitfall.next_step, pitfall.common_wrong_path])

    return find_leaks("\n".join(parts), problem.key_quantities)


# --------------------------------------------------------------------------
# 带守卫的生成
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardedHint:
    """一次受守卫的提示生成结果。"""

    text: str
    attempts: int
    leaked_attempts: int
    masked_terms: tuple[str, ...] = field(default=())

    @property
    def degraded(self) -> bool:
        """是否走了掩码兜底 —— 需要实验者关注。"""
        return bool(self.masked_terms)


_RETRY_INSTRUCTION = (
    "\n\n【上一次生成不合格】\n"
    "你上一次的回答中出现了不应透露的关键结论。"
    "请重新作答：保持在同一层级，改换提问角度，不要提及任何产物名称或最终结论。\n"
)


def guard_hint(
    problem: Problem,
    attempt_text: str,
    level: int,
    *,
    client,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> GuardedHint:
    """生成提示并保证不泄漏。

    - 层 4 不受 C1 约束，直接返回一次生成结果
    - 层 1~3：生成 → 校验 → 命中则带指令重生成；``max_attempts`` 次仍失败
      则掩码兜底，并在 :class:`GuardedHint` 中标记为 ``degraded``
    """
    from lifeng.hint_engine import C1_MAX_LEVEL, build_system_prompt, build_user_prompt

    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")

    # 层 4 允许给出答案，无泄漏可言。
    if level > C1_MAX_LEVEL:
        text = client.complete(
            build_system_prompt(problem, level),
            build_user_prompt(problem, attempt_text, level),
        ).strip()
        return GuardedHint(text=text, attempts=1, leaked_attempts=0)

    system = build_system_prompt(problem, level)
    user = build_user_prompt(problem, attempt_text, level)
    leaked_attempts = 0

    for attempt in range(1, max_attempts + 1):
        text = client.complete(system, user).strip()
        leaks = find_leaks(text, problem.key_quantities)
        if not leaks:
            return GuardedHint(
                text=text, attempts=attempt, leaked_attempts=leaked_attempts
            )

        leaked_attempts += 1
        last_text, last_leaks = text, leaks
        user = user + _RETRY_INSTRUCTION

    # 兜底：掩码命中词，保证 C1 成立，同时把失败暴露成数据。
    return GuardedHint(
        text=mask_terms(last_text, last_leaks),
        attempts=max_attempts,
        leaked_attempts=leaked_attempts,
        masked_terms=tuple(last_leaks),
    )
