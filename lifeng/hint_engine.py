"""砺锋 v2 —— 提示阶梯引擎（T3）。

把骨架转成四层递进的提示。核心约束 **C1：第 1~3 层不得给出答案**。

本模块采用**结构性防泄漏**，而不是靠 prompt 措辞祈祷：

===========  ==========================  ==========================
层级          注入给模型的信息            不给模型的信息
===========  ==========================  ==========================
1 问方向      insight + 卡点的 trigger     common_wrong_path / next_step / answer
2 具体提示    + common_wrong_path          next_step / answer
3 更具体提示  + next_step                  answer
4 答案 + 复盘 + answer                     （无限制，但仅限本层）
===========  ==========================  ==========================

即"模型看不到答案就不会说出答案"。T4 的字符串黑名单是**第二道**防线，
用于兜住模型从 insight 里推断出的间接泄漏。
"""

from __future__ import annotations

import os
from typing import Protocol

from lifeng import prompts
from lifeng.models import Pitfall, Problem, Skeleton

#: 允许的层级。4 表示给出答案与复盘。
VALID_LEVELS: tuple[int, ...] = (1, 2, 3, 4)

#: C1 约束的层级上限：1~3 层不得出现答案。
C1_MAX_LEVEL = 3


class HintError(ValueError):
    """提示生成的前置条件不满足。"""


class LLMClient(Protocol):
    """最小 LLM 接口。便于替换 provider，也便于离线测试 prompt 构造。"""

    def complete(self, system: str, user: str) -> str:  # pragma: no cover - 协议
        ...


# --------------------------------------------------------------------------
# Prompt 构造（纯函数，可离线测试）
# --------------------------------------------------------------------------


def _render_pitfalls(skeleton: Skeleton, level: int) -> str:
    """按层级**渐进披露**卡点信息 —— 这是 C1 结构性防泄漏的关键。

    第 1 层只给「学生卡住时的表现」，让模型知道他在哪卡住，
    但不知道正确的下一步是什么；到第 3 层才把 ``next_step`` 交出去。
    披露得越少，模型能泄露的就越少。
    """
    lines: list[str] = []
    for i, pitfall in enumerate(skeleton.pitfalls, start=1):
        lines.append(f"卡点 {i}")
        lines.append(f"  学生的典型表现：{pitfall.trigger}")
        if level >= 2:
            lines.append(f"  他可能正走上的歧路：{pitfall.common_wrong_path}")
        if level >= 3:
            lines.append(f"  越过该卡点所需的动作：{pitfall.next_step}")
    return "\n".join(lines)


def build_system_prompt(
    problem: Problem, level: int, *, previous_hints: tuple[str, ...] = ()
) -> str:
    """构造 system prompt。

    第 1~3 层**绝不包含** ``answer`` —— 这不是靠嘱咐模型"别说"，
    而是那个字符串根本不在 prompt 里。第 4 层必须包含，否则给不出答案。
    """
    if level not in VALID_LEVELS:
        raise HintError(f"层级必须是 {VALID_LEVELS} 之一，收到 {level!r}")
    if problem.skeleton is None:
        raise HintError(f"题目 {problem.id} 没有骨架，无法生成提示")

    parts = [
        prompts.hint_system_prompt(
            level=level,
            insight=problem.skeleton.insight,
            pitfalls_text=_render_pitfalls(problem.skeleton, level),
            answer=problem.answer if level == 4 else None,
        )
    ]
    if previous_hints:
        # 不给的话模型不知道上几层说过什么，会原样重复 ——
        # 学生看到的是「又说了一遍」，而不是往前推进一步。
        parts.append("\n\n## 你已经给过这些提示（**换一种说法，不要重复**）\n\n")
        for i, hint in enumerate(previous_hints, start=1):
            parts.append(f"{i}. {hint.strip()}\n")
    return "".join(parts)


def build_user_prompt(
    problem: Problem,
    attempt_text: str,
    level: int,
    *,
    previous_hints: tuple[str, ...] = (),
) -> str:
    """构造 user prompt。同样不含答案（第 4 层由 system prompt 提供）。"""
    if level not in VALID_LEVELS:
        raise HintError(f"层级必须是 {VALID_LEVELS} 之一，收到 {level!r}")
    return prompts.hint_user_prompt(
        statement=problem.statement,
        attempt_text=attempt_text,
        level=level,
        previous_hints=previous_hints,
    )


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------


def generate_hint(
    problem: Problem,
    attempt_text: str,
    level: int,
    *,
    client: LLMClient,
) -> str:
    """生成第 ``level`` 层提示。

    ``level`` 为 4 时给答案与复盘；1~3 时结构性禁止答案进入 prompt。
    """
    if not problem.hintable:
        if problem.type == "memory":
            reason = "记忆型题目走卡片检索，不进入提示阶梯（C2）"
        else:
            reason = (
                "推理型题目缺少骨架，请先按 docs/skeleton-format.md 完成标注"
            )
        raise HintError(
            f"题目 {problem.id} 不可引导"
            f"（type={problem.type}，是否有骨架={problem.skeleton is not None}）：{reason}"
        )
    if level not in VALID_LEVELS:
        raise HintError(f"层级必须是 {VALID_LEVELS} 之一，收到 {level!r}")
    if not attempt_text.strip():
        raise HintError("attempt_text 不能为空")

    system = build_system_prompt(problem, level)
    user = build_user_prompt(problem, attempt_text, level)
    return client.complete(system, user).strip()


# --------------------------------------------------------------------------
# LLM 客户端
# --------------------------------------------------------------------------


class DeepSeekClient:
    """DeepSeek API 客户端（OpenAI 兼容端点）。"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        temperature: float = 0.3,
    ) -> None:
        key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise HintError(
                "未提供 DeepSeek API key，且环境变量 DEEPSEEK_API_KEY 未设置。"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise HintError("缺少 openai 依赖，请先 pip install openai") from exc

        self._client = OpenAI(api_key=key, base_url=base_url)
        self.model = model
        self.temperature = temperature

    def complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
        )
        return resp.choices[0].message.content or ""


def current_pitfall(problem: Problem, index: int) -> Pitfall:
    """取第 ``index`` 个卡点（从 0 开始）。供 T5 界面与 T9 判定对照使用。"""
    if problem.skeleton is None:
        raise HintError(f"题目 {problem.id} 没有骨架")
    try:
        return problem.skeleton.pitfalls[index]
    except IndexError as exc:
        raise HintError(
            f"题目 {problem.id} 只有 {len(problem.skeleton.pitfalls)} 个卡点，"
            f"索引 {index} 越界"
        ) from exc
