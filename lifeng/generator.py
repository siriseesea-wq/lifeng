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

from lifeng import prompts
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


def build_system_prompt(
    *,
    problem_id: str = "p001",
    domain: str = "organic",
    concept_ids: list[tuple[str, str]] | None = None,
    include_fewshot: bool = True,
) -> str:
    """出题的 system prompt。

    实际文本在 :mod:`lifeng.prompts` —— 那里是**唯一**需要审阅与调优的地方。
    """
    return prompts.generation_system_prompt(
        problem_id=problem_id,
        domain=domain,
        concept_ids=concept_ids or [],
        min_pitfalls=MIN_PITFALLS,
        include_fewshot=include_fewshot,
    )


def build_user_prompt(
    *,
    problem_id: str,
    domain: str,
    difficulty: str = prompts.DEFAULT_DIFFICULTY,
    existing_statements: tuple[str, ...] = (),
    covered_concepts: tuple[str, ...] = (),
    feedback: str | None = None,
) -> str:
    """出题的 user prompt。"""
    return prompts.generation_user_prompt(
        problem_id=problem_id,
        domain=domain,
        difficulty=difficulty,
        existing_statements=existing_statements,
        covered_concepts=covered_concepts,
        feedback=feedback,
    )


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


def validate_candidate(
    problem: Problem, *, known_concepts: set[str] | None = None
) -> list[str]:
    """返回不合格的原因列表；空列表表示通过。

    ``known_concepts`` 给出时，会检查题目标注的知识点是否都在图谱里 ——
    写错的 id 不会让程序崩，只会让图谱上少一条连线、
    画像少一个维度，属于**静默失效**，必须显式拦截。
    """
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

    if known_concepts is not None and problem.concepts:
        unknown = sorted(set(problem.concepts) - known_concepts)
        if unknown:
            problems.append(
                f"`concepts` 里有图谱中不存在的 id：{unknown}。"
                "请从给定的词表里选，或改用其它知识点"
            )

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
    difficulty: str = prompts.DEFAULT_DIFFICULTY,
    existing_statements: tuple[str, ...] = (),
    covered_concepts: tuple[str, ...] = (),
    concept_ids: list[tuple[str, str]] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Problem:
    """生成一道题并通过全部校验；失败时带反馈重试。"""
    if domain not in VALID_DOMAINS:
        raise ValueError(f"domain 取值非法：{domain!r}，应为 {VALID_DOMAINS}")
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")

    concept_ids = concept_ids or []
    system = build_system_prompt(
        problem_id=problem_id, domain=domain, concept_ids=concept_ids
    )
    known_concepts = {cid for cid, _ in concept_ids}
    feedback: str | None = None
    last_error = "（无）"

    for _ in range(max_attempts):
        user = build_user_prompt(
            problem_id=problem_id,
            domain=domain,
            difficulty=difficulty,
            existing_statements=existing_statements,
            covered_concepts=covered_concepts,
            feedback=feedback,
        )
        text = client.complete(system, user)

        try:
            problem = parse_candidate(text, source=problem_id)
        except SkeletonError as exc:
            last_error = str(exc)
            feedback = f"YAML 不合规：{last_error}"
            continue

        issues = validate_candidate(problem, known_concepts=known_concepts)
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
        "concepts": list(problem.concepts),
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
    difficulty: str = prompts.DEFAULT_DIFFICULTY,
    concepts_path: str | Path | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> list[GenerationResult]:
    """连出 ``count`` 道题并写盘。单题失败不中断整批。"""
    directory = Path(directory)
    results: list[GenerationResult] = []
    statements: list[str] = []
    covered: list[str] = []

    # 知识点词表：给出后，模型才会用**我们图谱里的** id 而不是自己编。
    concept_ids: list[tuple[str, str]] = []
    if concepts_path is not None:
        from lifeng.concepts import ConceptError, ConceptGraph

        try:
            graph = ConceptGraph.load(concepts_path)
            concept_ids = [(c.id, c.title) for c in graph.concepts.values()]
        except ConceptError:
            concept_ids = []

    # 收录已有题干与已覆盖知识点：前者避免重复出题，后者用于**考点轮转**
    if directory.is_dir():
        from lifeng.models import load_problems

        try:
            existing = load_problems(directory)
            statements.extend(p.statement for p in existing)
            covered.extend(c for p in existing for c in p.concepts)
        except SkeletonError:
            pass  # 题库里已有不合格文件时不阻断生成

    for _ in range(count):
        problem_id = next_problem_id(directory)
        problem = generate_problem(
            client,
            problem_id=problem_id,
            domain=domain,
            difficulty=difficulty,
            existing_statements=tuple(statements),
            covered_concepts=tuple(covered),
            concept_ids=concept_ids,
            max_attempts=max_attempts,
        )
        path = save_problem(problem, directory)
        statements.append(problem.statement)
        covered.extend(problem.concepts)
        results.append(GenerationResult(problem=problem, path=path, attempts=1))

    return results


# --------------------------------------------------------------------------
# CLI：python -m lifeng.generator --count 5
# --------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """CLI 入口。打包后由 ``launcher`` 的 ``generate`` 子命令调用。"""
    import argparse

    from lifeng.console import make_output_safe
    from lifeng.hint_engine import DeepSeekClient, HintError

    make_output_safe()

    parser = argparse.ArgumentParser(description="用 LLM 生成题目与骨架")
    parser.add_argument("--count", type=int, default=5, help="生成几道")
    parser.add_argument("--domain", default="organic", choices=VALID_DOMAINS)
    parser.add_argument(
        "--difficulty",
        default=prompts.DEFAULT_DIFFICULTY,
        choices=list(prompts.DIFFICULTY_LEVELS),
        help="命题深度：basic 初赛 / standard 复赛 / advanced 决赛",
    )
    parser.add_argument(
        "--concepts",
        default="concepts.yaml",
        help="知识点图谱路径；给了它模型才会用图谱里的 id 而不是自己编",
    )
    parser.add_argument("--dir", default=str(DEFAULT_PROBLEMS_DIR))
    args = parser.parse_args(argv[1:])

    try:
        client = DeepSeekClient()
    except HintError as exc:
        print(f"[FAIL] {exc}")
        return 1

    concepts = Path(args.concepts)
    if not concepts.is_file():
        print(f"[警告] 找不到知识点图谱 {concepts}，生成的题将没有 concepts 标注，"
              "图谱与画像功能用不上它们")

    print(f"[生成] {args.count} 道 {args.domain} 推理题（{args.difficulty}）→ {args.dir}")
    for i in range(args.count):
        try:
            results = generate_batch(
                client,
                1,
                directory=args.dir,
                domain=args.domain,
                difficulty=args.difficulty,
                concepts_path=concepts if concepts.is_file() else None,
            )
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

    raise SystemExit(main(sys.argv))
