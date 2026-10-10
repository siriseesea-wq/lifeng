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

_LEVEL_DIRECTIVE: dict[int, str] = {
    1: (
        "第 1 层 —— 只问方向。用一个简短的问题，把学生的注意力引向正确的思考维度。"
        "禁止说出任何具体基团、步骤、中间体或产物名称。"
    ),
    2: (
        "第 2 层 —— 给具体提示。明确指出学生应当检查哪个位置或哪一条性质，"
        "并点出他可能正走上的歧路。仍然禁止说出结论或产物。"
    ),
    3: (
        "第 3 层 —— 给更具体的提示。把下一步动作说到可以直接执行的程度，"
        "但**仍然不得说出最终产物或答案**。"
    ),
    4: (
        "第 4 层 —— 给出完整答案与复盘。说明最终答案与机理，"
        "并指出学生此前卡在哪一步、为什么会卡在那里。"
    ),
}

_SYSTEM_HEADER = """你是化学竞赛的引导教练，使用苏格拉底式提问帮助学生自己走出来。

铁律：
- 学生需要的是「被引导着想到」，不是「被告知答案」。
- 不要复述题目，不要写长篇讲解，不要罗列多个可能性。
- 只输出提示本身，不要任何前缀、标题或解释。
"""


def _render_pitfalls(skeleton: Skeleton, level: int) -> str:
    """按层级渐进披露卡点信息（结构性防泄漏的关键）。"""
    lines: list[str] = []
    for i, pitfall in enumerate(skeleton.pitfalls, start=1):
        lines.append(f"卡点 {i}")
        lines.append(f"  学生的典型表现：{pitfall.trigger}")
        if level >= 2:
            lines.append(f"  他可能正走上的歧路：{pitfall.common_wrong_path}")
        if level >= 3:
            lines.append(f"  越过该卡点所需的动作：{pitfall.next_step}")
    return "\n".join(lines)


def build_system_prompt(problem: Problem, level: int) -> str:
    """构造 system prompt。

    第 1~3 层**绝不包含 ``answer``**；第 4 层必须包含，否则无法给出答案。
    """
    if level not in VALID_LEVELS:
        raise HintError(f"层级必须是 {VALID_LEVELS} 之一，收到 {level!r}")
    if problem.skeleton is None:
        raise HintError(f"题目 {problem.id} 没有骨架，无法生成提示")

    parts = [_SYSTEM_HEADER, f"\n【本题的关键洞察】\n{problem.skeleton.insight}\n"]
    parts.append(f"\n【本题的卡点】\n{_render_pitfalls(problem.skeleton, level)}\n")
    parts.append(f"\n【本次要求】\n{_LEVEL_DIRECTIVE[level]}\n")

    if level == 4:
        parts.append(f"\n【参考答案（仅供本层使用）】\n{problem.answer}\n")

    return "".join(parts)


def build_user_prompt(problem: Problem, attempt_text: str, level: int) -> str:
    """构造 user prompt。同样不含答案（第 4 层由 system prompt 提供）。"""
    if level not in VALID_LEVELS:
        raise HintError(f"层级必须是 {VALID_LEVELS} 之一，收到 {level!r}")

    return (
        f"题目：\n{problem.statement}\n\n"
        f"学生当前的尝试或卡住的地方：\n{attempt_text}\n\n"
        f"请给出第 {level} 层提示。"
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
