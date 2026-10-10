"""砺锋 v2 —— 领域模型与骨架加载（T1）。

本模块冻结 skeleton YAML schema 与内存模型的映射关系。
T2（存储）、T3（提示引擎）、T4（泄漏校验）、T5（界面）均依赖本模块，
因此它是本计划中第一个也是必须最先冻结的接口。

设计依据见 ``docs/skeleton-format.md``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

Domain = Literal["organic", "physical", "structural", "inorganic", "analytical"]
ProblemType = Literal["reasoning", "memory"]

VALID_DOMAINS: tuple[str, ...] = (
    "organic",
    "physical",
    "structural",
    "inorganic",
    "analytical",
)
VALID_TYPES: tuple[str, ...] = ("reasoning", "memory")

#: 计划 T6 的验收要求：每题至少 3 个卡点。
MIN_PITFALLS = 3


class SkeletonError(ValueError):
    """骨架文件不符合 schema。

    消息必须同时指明**文件**与**字段**，否则 T6 标注 20 道题时无法定位问题。
    """


@dataclass(frozen=True)
class Pitfall:
    """一个卡点。

    ``next_step`` 是推进判定的唯一判据（见计划 §3：n=20 时由实验者人工判定）。
    """

    trigger: str
    next_step: str
    common_wrong_path: str


@dataclass(frozen=True)
class Skeleton:
    insight: str
    pitfalls: tuple[Pitfall, ...]


@dataclass(frozen=True)
class Problem:
    id: str
    domain: Domain
    type: ProblemType
    statement: str
    answer: str
    key_quantities: tuple[str, ...]
    skeleton: Skeleton | None

    @property
    def is_reasoning(self) -> bool:
        return self.type == "reasoning"

    @property
    def hintable(self) -> bool:
        """C2：只有**推理型且已标注骨架**的题才进入引导流程。

        记忆型题（元素化学、人名反应、试剂特性）走卡片检索，不进提示阶梯。
        """
        return self.is_reasoning and self.skeleton is not None


# --------------------------------------------------------------------------
# 校验辅助
# --------------------------------------------------------------------------


def _require_mapping(raw: object, path: Path, where: str) -> dict:
    if not isinstance(raw, dict):
        raise SkeletonError(
            f"{path}: `{where}` 必须是映射，实际是 {type(raw).__name__}"
        )
    return raw


def _require_str(raw: dict, key: str, path: Path, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkeletonError(f"{path}: `{where}.{key}` 缺失或为空")
    return value.strip()


def _parse_pitfall(raw: object, path: Path, index: int) -> Pitfall:
    where = f"pitfalls[{index}]"
    mapping = _require_mapping(raw, path, where)
    return Pitfall(
        trigger=_require_str(mapping, "trigger", path, where),
        next_step=_require_str(mapping, "next_step", path, where),
        common_wrong_path=_require_str(mapping, "common_wrong_path", path, where),
    )


def _parse_skeleton(raw: object, path: Path) -> Skeleton:
    mapping = _require_mapping(raw, path, "skeleton")
    insight = _require_str(mapping, "insight", path, "skeleton")

    raw_pitfalls = mapping.get("pitfalls")
    if not isinstance(raw_pitfalls, list):
        raise SkeletonError(f"{path}: `skeleton.pitfalls` 必须是列表")
    if len(raw_pitfalls) < MIN_PITFALLS:
        raise SkeletonError(
            f"{path}: `skeleton.pitfalls` 至少需要 {MIN_PITFALLS} 个，"
            f"实际 {len(raw_pitfalls)} 个"
        )

    pitfalls = tuple(
        _parse_pitfall(item, path, i) for i, item in enumerate(raw_pitfalls)
    )
    return Skeleton(insight=insight, pitfalls=pitfalls)


def _parse_key_quantities(raw: dict, path: Path) -> tuple[str, ...]:
    """解析 C1 的黑名单：只应包含**最终答案**，不含中间机理。

    中间机理正是要引导学生自己走出来的内容；若列入黑名单，
    前 3 层提示将无法推进。取舍理由见 docs/skeleton-format.md。
    """
    value = raw.get("key_quantities")
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SkeletonError(f"{path}: `key_quantities` 必须是列表")
    items = [str(v).strip() for v in value if str(v).strip()]
    return tuple(items)


# --------------------------------------------------------------------------
# 装载
# --------------------------------------------------------------------------


def load_problem(path: str | Path) -> Problem:
    """从单个 YAML 文件装载题目并做完整 schema 校验。"""
    path = Path(path)
    if not path.is_file():
        raise SkeletonError(f"{path}: 文件不存在")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SkeletonError(f"{path}: YAML 解析失败 —— {exc}") from exc

    mapping = _require_mapping(raw, path, "<root>")

    problem_id = _require_str(mapping, "id", path, "<root>")
    domain = _require_str(mapping, "domain", path, "<root>")
    ptype = _require_str(mapping, "type", path, "<root>")

    if domain not in VALID_DOMAINS:
        raise SkeletonError(
            f"{path}: `domain` 取值非法：{domain!r}，应为 {VALID_DOMAINS}"
        )
    if ptype not in VALID_TYPES:
        raise SkeletonError(f"{path}: `type` 取值非法：{ptype!r}，应为 {VALID_TYPES}")

    statement = _require_str(mapping, "statement", path, "<root>")
    answer = _require_str(mapping, "answer", path, "<root>")
    key_quantities = _parse_key_quantities(mapping, path)

    raw_skeleton = mapping.get("skeleton")
    skeleton: Skeleton | None = None

    if ptype == "reasoning":
        if raw_skeleton is None:
            raise SkeletonError(f"{path}: 推理型题目必须提供 `skeleton`")
        if not key_quantities:
            raise SkeletonError(
                f"{path}: 推理型题目必须提供非空 `key_quantities`（C1 泄漏校验依据）"
            )
        skeleton = _parse_skeleton(raw_skeleton, path)
    elif raw_skeleton is not None:
        # 记忆型题也可以挂骨架（便于将来扩展），但校验同样严格。
        skeleton = _parse_skeleton(raw_skeleton, path)

    return Problem(
        id=problem_id,
        domain=domain,  # type: ignore[arg-type]
        type=ptype,  # type: ignore[arg-type]
        statement=statement,
        answer=answer,
        key_quantities=key_quantities,
        skeleton=skeleton,
    )


def load_problems(directory: str | Path) -> list[Problem]:
    """装载目录下全部 ``*.yaml``，按 id 排序。

    任一文件不合规即抛 ``SkeletonError``（快速失败，避免标注错误被静默吞掉）。
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise SkeletonError(f"{directory}: 不是目录")

    problems = [load_problem(p) for p in sorted(directory.glob("*.yaml"))]

    seen: set[str] = set()
    for problem in problems:
        if problem.id in seen:
            raise SkeletonError(f"{directory}: 题目 id 重复 —— {problem.id!r}")
        seen.add(problem.id)

    return problems


# --------------------------------------------------------------------------
# CLI：python -m lifeng.models problems/
# --------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path("problems")
    try:
        problems = load_problems(target)
    except SkeletonError as exc:
        print(f"[FAIL] {exc}")
        return 1

    for problem in problems:
        pitfalls = len(problem.skeleton.pitfalls) if problem.skeleton else 0
        mode = "引导" if problem.hintable else "卡片"
        print(
            f"[OK] {problem.id:<6} {problem.domain:<10} {problem.type:<9} "
            f"卡点={pitfalls} 黑名单={len(problem.key_quantities)} → {mode}"
        )
    print(f"\n共 {len(problems)} 题，全部通过 schema 校验。")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
