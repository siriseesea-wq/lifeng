"""砺锋 v2 —— 题目生成器（AI 出题）。

**用途**：让 MVP 不必先人工标注 20 题（原计划 T6 的关键路径）。

**但请注意一个设计张力**：Design Spec 决策 4 是「**人工**标注骨架 + LLM 措辞」，
实验要验证的正是这个组合。由 AI 生成的骨架是**另一个命题**。
因此生成的题会标记 ``skeleton_source: ai``，将来真做实验时才能说清
参与验证的骨架里有多少条不是人写的。

**生成后必须通过三道校验**，否则带反馈重试：

1. schema 校验（:func:`lifeng.models.parse_problem`）
2. 骨架不得含答案关键量（:func:`lifeng.leak_check.skeleton_leaks_answer`）
   —— 骨架会被注入 prompt，一旦写进答案，结构性防泄漏直接失效
3. 必须是 ``reasoning`` 型（C2：记忆型题不进引导流程）

用 ``python -m lifeng.generator --count 5`` 生成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from lifeng.leak_check import skeleton_leaks_answer
from lifeng.models import (
    MIN_PITFALLS,
    VALID_DOMAINS,
    Problem,
    SkeletonError,
    parse_problem,
)

DEFAULT_PROBLEMS_DIR = Path("problems")
DEFAULT_MAX_ATTEMPTS = 3

_SCHEMA_SPEC = """\
你必须输出一份 YAML，字段与含义如下（**照搬这个模板，不要增删字段**）：

```yaml
id: <题号，如 p002>
domain: organic            # 只能是 organic/physical/structural/inorganic/analytical
type: reasoning            # 只能是 reasoning；不要出记忆型题
skeleton_source: ai        # 固定写 ai

statement: |
  题干。要像竞赛真题，给足已知条件，问一个明确的问题。

answer: |
  参考答案与简要机理说明。

key_quantities:            # 【最关键】只放「最终答案」，绝不放中间机理
  - 最终产物名
  - 终产物的系统命名

skeleton:
  insight: |
    一两句话概括这道题的关键洞察。它是提示的天花板。

  pitfalls:                # 至少 3 个
    - trigger: |
        什么表现说明学生卡在这里。
      next_step: |
        越过该卡点的下一步动作。必须**可观察**（能看出学生做没做到），
        不要写「理解……」这类无法判定的说法。
      common_wrong_path: |
        学生最常走错的岔路。
```

## 两条铁律

**铁律 1 —— `key_quantities` 只放最终答案，不放中间机理。**

机理正是要引导学生自己走出来的内容。若把「1,2-迁移」「碳正离子」这类
机理词写进 key_quantities，前 3 层提示将无法推进（校验器会把有效引导
全部误杀）。只放终产物名与它的系统命名。

**铁律 2 —— `insight` 与 `pitfalls` 里绝不能出现 `key_quantities` 中的词。**

骨架会被注入提示生成器的 prompt。骨架里若写了答案，
「模型看不到答案就说不出答案」这条结构性防线就失效了。

## 输出格式

只输出 YAML 本体。不要加解释、不要加 ```yaml 代码围栏之外的内容。
"""


class GenerationError(RuntimeError):
    """多次重试后仍无法生成合格题目。"""


@dataclass(frozen=True)
class GenerationResult:
    problem: Problem
    path: Path
    attempts: int


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------


def build_system_prompt() -> str:
    return (
        "你是化学竞赛命题专家，负责出有机化学机理推理题并标注解题骨架。\n"
        "你输出的骨架会被用作苏格拉底式引导提示的约束，因此必须准确、可观察。\n\n"
        + _SCHEMA_SPEC
    )


def build_user_prompt(
    *,
    problem_id: str,
    domain: str,
    existing_statements: tuple[str, ...] = (),
    feedback: str | None = None,
) -> str:
    parts = [
        f"请出一道**{domain}**方向的化学竞赛推理题，题号 {problem_id}。",
        f"`skeleton.pitfalls` 至少 {MIN_PITFALLS} 个。",
    ]
    if existing_statements:
        parts.append("\n**不要与下列已有题目重复**（题干摘要）：")
        parts.extend(f"- {s[:60]}" for s in existing_statements[:10])
    if feedback:
        parts.append(f"\n【上一次生成不合格，请修正后重新输出】\n{feedback}")
    parts.append("\n现在只输出 YAML。")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 解析与校验
# --------------------------------------------------------------------------


def extract_yaml(text: str) -> str:
    """从模型输出中取出 YAML 本体（容忍 ``` 代码围栏与前后废话）。"""
    fenced = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    # 没有围栏时，从第一个看起来像顶层字段的位置截取
    match = re.search(r"^id\s*:", text, re.MULTILINE)
    if match:
        return text[match.start():].strip()
    return text.strip()


def validate_candidate(problem: Problem) -> list[str]:
    """返回不合格的原因列表；空列表表示通过。"""
    problems: list[str] = []

    if problem.type != "reasoning":
        problems.append(
            f"`type` 必须是 reasoning，收到 {problem.type!r}"
            "（记忆型题不进引导流程，见 C2）"
        )

    leaks = skeleton_leaks_answer(problem)
    if leaks:
        problems.append(
            "骨架（insight/pitfalls）中出现了答案关键量 "
            f"{leaks} —— 这会让结构性防泄漏失效，必须改写骨架或用别的词描述"
        )

    if problem.skeleton and len(problem.skeleton.pitfalls) < MIN_PITFALLS:
        problems.append(f"`pitfalls` 至少需要 {MIN_PITFALLS} 个")

    return problems


def parse_candidate(text: str, source: str = "<generated>") -> Problem:
    """把模型输出解析成 ``Problem``；不合规抛 ``SkeletonError``。"""
    raw = yaml.safe_load(extract_yaml(text))
    if not isinstance(raw, dict):
        raise SkeletonError(f"{source}: 模型输出不是 YAML 映射")
    return parse_problem(raw, source)


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------


def next_problem_id(directory: str | Path) -> str:
    """扫描目录，给出下一个可用题号（p001 之后为 p002）。"""
    directory = Path(directory)
    used: set[int] = set()
    if directory.is_dir():
        for path in directory.glob("p*.yaml"):
            match = re.fullmatch(r"p(\d+)", path.stem)
            if match:
                used.add(int(match.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"p{n:03d}"


def generate_problem(
    client,
    *,
    problem_id: str,
    domain: str = "organic",
    existing_statements: tuple[str, ...] = (),
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Problem:
    """生成一道题并通过全部校验；失败时带反馈重试。"""
    if domain not in VALID_DOMAINS:
        raise ValueError(f"domain 取值非法：{domain!r}，应为 {VALID_DOMAINS}")
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")

    system = build_system_prompt()
    feedback: str | None = None
    last_error = "（无）"

    for _ in range(max_attempts):
        user = build_user_prompt(
            problem_id=problem_id,
            domain=domain,
            existing_statements=existing_statements,
            feedback=feedback,
        )
        text = client.complete(system, user)

        try:
            problem = parse_candidate(text, source=problem_id)
        except SkeletonError as exc:
            last_error = str(exc)
            feedback = f"YAML 不合规：{last_error}"
            continue

        issues = validate_candidate(problem)
        if not issues:
            # 题号以调用方指定的为准。模型只是**被建议**用这个 id，
            # 它可能回显错、编造或重复 —— 而文件命名与去重都依赖这个 id，
            # 放任模型决定会导致编号错乱与覆盖。
            return replace(problem, id=problem_id)

        last_error = "；".join(issues)
        feedback = "存在以下问题：\n" + "\n".join(f"- {i}" for i in issues)

    raise GenerationError(
        f"{problem_id}: {max_attempts} 次尝试后仍未产出合格题目。最后一次问题：{last_error}"
    )


def dump_problem(problem: Problem) -> str:
    """序列化为 YAML 文本（保持字段顺序）。"""
    payload = {
        "id": problem.id,
        "domain": problem.domain,
        "type": problem.type,
        "skeleton_source": problem.skeleton_source,
        "statement": problem.statement,
        "answer": problem.answer,
        "key_quantities": list(problem.key_quantities),
        "skeleton": None,
    }
    if problem.skeleton is not None:
        payload["skeleton"] = {
            "insight": problem.skeleton.insight,
            "pitfalls": [
                {
                    "trigger": p.trigger,
                    "next_step": p.next_step,
                    "common_wrong_path": p.common_wrong_path,
                }
                for p in problem.skeleton.pitfalls
            ],
        }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=100)


def save_problem(problem: Problem, directory: str | Path = DEFAULT_PROBLEMS_DIR) -> Path:
    """写盘前**再校验一次**（防御性：save 不应成为绕过校验的旁路）。"""
    issues = validate_candidate(problem)
    if issues:
        raise GenerationError("拒绝写入不合格题目：" + "；".join(issues))

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{problem.id}.yaml"
    path.write_text(dump_problem(problem), encoding="utf-8")
    return path


def generate_batch(
    client,
    count: int,
    *,
    directory: str | Path = DEFAULT_PROBLEMS_DIR,
    domain: str = "organic",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> list[GenerationResult]:
    """连出 ``count`` 道题并写盘。单题失败不中断整批。"""
    directory = Path(directory)
    results: list[GenerationResult] = []
    statements: list[str] = []

    # 收录已有题干，避免重复出题
    if directory.is_dir():
        from lifeng.models import load_problems

        try:
            statements.extend(p.statement for p in load_problems(directory))
        except SkeletonError:
            pass  # 题库里已有不合格文件时不阻断生成

    for _ in range(count):
        problem_id = next_problem_id(directory)
        problem = generate_problem(
            client,
            problem_id=problem_id,
            domain=domain,
            existing_statements=tuple(statements),
            max_attempts=max_attempts,
        )
        path = save_problem(problem, directory)
        statements.append(problem.statement)
        results.append(GenerationResult(problem=problem, path=path, attempts=1))

    return results


# --------------------------------------------------------------------------
# CLI：python -m lifeng.generator --count 5
# --------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    import argparse

    from lifeng.console import make_output_safe
    from lifeng.hint_engine import DeepSeekClient, HintError

    make_output_safe()

    parser = argparse.ArgumentParser(description="用 LLM 生成题目与骨架")
    parser.add_argument("--count", type=int, default=5, help="生成几道")
    parser.add_argument("--domain", default="organic", choices=VALID_DOMAINS)
    parser.add_argument("--dir", default=str(DEFAULT_PROBLEMS_DIR))
    args = parser.parse_args(argv[1:])

    try:
        client = DeepSeekClient()
    except HintError as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"[生成] {args.count} 道 {args.domain} 推理题 → {args.dir}")
    for i in range(args.count):
        try:
            results = generate_batch(client, 1, directory=args.dir, domain=args.domain)
        except GenerationError as exc:
            print(f"[FAIL] 第 {i + 1} 道：{exc}")
            continue
        for r in results:
            print(f"[OK] {r.problem.id} → {r.path}（卡点 {len(r.problem.skeleton.pitfalls)}）")

    from lifeng.models import load_problems

    try:
        load_problems(args.dir)
        print(f"\n题库校验通过：{args.dir}")
    except SkeletonError as exc:
        print(f"\n[FAIL] 题库校验未通过：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
