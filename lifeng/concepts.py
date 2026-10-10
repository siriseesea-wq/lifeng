"""砺锋 v2 —— 知识点图谱。

**来源说明**：数据模型参考「活书 learning-agent」的 ``core/graph.py``
（MIT，© 2026 KK，见 NOTICE.md）。活书那份是 682 行的完整教材图谱
（含章节簇、教材锚点、跨项目引用、JSON 导入导出），本项目只取它的
**核心形状**：

- 知识点 ``Concept``（对应活书的 ``Item``）
- 前置依赖 ``prerequisites`` 与相关概念 ``related``
- 加载时校验（悬空引用、自引用、成环）

教材锚点、章节簇、跨项目引用这些是教材场景才需要的，这里不引入 ——
本项目的题库是题目，不是教材。

**为什么需要它**：掌握度和复习调度都是**按题目**算的。但学生卡住时
真正需要知道的是「我缺的是哪个概念」，以及「这个概念的前置我会不会」。
那是题目粒度回答不了的。

**图形化**：:meth:`ConceptGraph.to_dot` 产出 Graphviz DOT，
交给 Streamlit 内置的 ``st.graphviz_chart`` 渲染 —— 不需要新依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_DOMAINS: tuple[str, ...] = (
    "organic",
    "physical",
    "structural",
    "inorganic",
    "analytical",
)


class ConceptError(ValueError):
    """知识点图不合规。消息须同时指明文件与字段。"""


@dataclass(frozen=True)
class Concept:
    """一个知识点。

    ``prerequisites`` 表示**学习顺序约束**：没掌握前置，学这个会很吃力。
    ``related`` 表示**横向联系**（类比、对比），不构成顺序约束。
    """

    id: str
    title: str
    domain: str = "organic"
    note: str = ""
    prerequisites: tuple[str, ...] = ()
    related: tuple[str, ...] = ()


@dataclass
class ConceptGraph:
    """内存中的知识点图。"""

    concepts: dict[str, Concept] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    # -- 装载 -------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "ConceptGraph":
        path = Path(path)
        if not path.is_file():
            raise ConceptError(f"{path}: 文件不存在")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConceptError(f"{path}: YAML 解析失败 —— {exc}") from exc

        if not isinstance(raw, dict) or not isinstance(raw.get("concepts"), list):
            raise ConceptError(f"{path}: 顶层需要 `concepts:` 列表")

        graph = cls()
        for index, entry in enumerate(raw["concepts"]):
            graph.concepts[entry["id"]] = _parse_concept(entry, path, index)

        graph.errors = graph.validate()
        return graph

    # -- 校验 -------------------------------------------------------------

    def validate(self) -> list[str]:
        """返回问题列表；空列表表示通过。**不抛异常**，便于一次性看全部问题。"""
        problems: list[str] = []
        for cid, concept in self.concepts.items():
            for prereq in concept.prerequisites:
                if prereq == cid:
                    problems.append(f"{cid}: 前置依赖指向自己")
                elif prereq not in self.concepts:
                    problems.append(f"{cid}: 前置依赖不存在 —— {prereq}")
            for rel in concept.related:
                if rel == cid:
                    problems.append(f"{cid}: 相关概念指向自己")
                elif rel not in self.concepts:
                    problems.append(f"{cid}: 相关概念不存在 —— {rel}")

        problems.extend(self._find_cycles())
        return problems

    def _find_cycles(self) -> list[str]:
        """前置依赖成环检测。

        成环意味着「学 A 之前要先学 B，学 B 之前要先学 A」——
        学生永远无法开始。这不是理论问题，是标注时容易犯的错。
        """
        WHITE, GREY, BLACK = 0, 1, 2
        color = {cid: WHITE for cid in self.concepts}
        found: list[str] = []

        def visit(cid: str, path: list[str]) -> None:
            color[cid] = GREY
            for prereq in self.concepts[cid].prerequisites:
                if prereq not in self.concepts:
                    continue
                if color[prereq] == GREY:
                    cycle = " -> ".join(path + [cid, prereq])
                    found.append(f"前置依赖成环：{cycle}")
                elif color[prereq] == WHITE:
                    visit(prereq, path + [cid])
            color[cid] = BLACK

        for cid in self.concepts:
            if color[cid] == WHITE:
                visit(cid, [])
        return found

    # -- 查询 -------------------------------------------------------------

    def get(self, cid: str) -> Concept | None:
        return self.concepts.get(cid)

    def __len__(self) -> int:
        return len(self.concepts)

    def __contains__(self, cid: object) -> bool:
        return cid in self.concepts

    def missing_prerequisites(self, cid: str, known: set[str]) -> list[str]:
        """学过 ``cid`` 之后，哪些前置还没掌握（含间接前置）。"""
        seen: set[str] = set()
        queue = list(self.concepts[cid].prerequisites) if cid in self.concepts else []
        while queue:
            prereq = queue.pop()
            if prereq in seen or prereq not in self.concepts:
                continue
            seen.add(prereq)
            queue.extend(self.concepts[prereq].prerequisites)
        return sorted(seen - known)

    def unlockable(self, known: set[str]) -> list[str]:
        """前置都已掌握、但自己还没掌握的知识点 —— 即「下一步该学什么」。"""
        return sorted(
            cid
            for cid, concept in self.concepts.items()
            if cid not in known and set(concept.prerequisites) <= known
        )

    def neighbours(self, cid: str) -> list[tuple[str, str]]:
        """(邻居 id, 关系类型)，用于画图。"""
        if cid not in self.concepts:
            return []
        concept = self.concepts[cid]
        return [(p, "prereq") for p in concept.prerequisites] + [
            (r, "related") for r in concept.related
        ]

    # -- 图形化 -----------------------------------------------------------

    def to_dot(
        self,
        *,
        emphasise: set[str] | None = None,
        missing: set[str] | None = None,
        title: str = "",
    ) -> str:
        """产出 Graphviz DOT。

        - ``emphasise``：学生已掌握的点（实心绿）
        - ``missing``：学生薄弱或缺失的点（红框）
        - 其余为未触及（灰）
        """
        emphasise = emphasise or set()
        missing = missing or set()

        lines = ["digraph lifeng {"]
        lines.append('  rankdir=LR;')
        lines.append('  bgcolor="transparent";')
        lines.append(
            '  node [shape=box, style="rounded,filled", fontname="Helvetica",'
            ' fontsize=11, fillcolor="#ECEFF1", color="#90A4AE"];'
        )
        lines.append('  edge [color="#B0BEC5", arrowsize=0.6];')

        for cid, concept in sorted(self.concepts.items()):
            label = _escape(concept.title or cid)
            if cid in missing:
                attrs = 'fillcolor="#FFEBEE", color="#C62828", penwidth=2'
            elif cid in emphasise:
                attrs = 'fillcolor="#E8F5E9", color="#2E7D32"'
            else:
                attrs = ""
            lines.append(f'  "{cid}" [label="{label}"{", " + attrs if attrs else ""}];')

        for cid, concept in sorted(self.concepts.items()):
            for prereq in concept.prerequisites:
                if prereq in self.concepts:
                    lines.append(f'  "{prereq}" -> "{cid}";')
            for rel in concept.related:
                if rel in self.concepts:
                    lines.append(f'  "{cid}" -> "{rel}" [style=dashed, dir=none];')

        lines.append("}")
        return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace('"', '\\"').replace("\n", " ")


def _parse_concept(entry: object, path: Path, index: int) -> Concept:
    where = f"concepts[{index}]"
    if not isinstance(entry, dict):
        raise ConceptError(f"{path}: `{where}` 必须是映射")

    cid = entry.get("id")
    if not isinstance(cid, str) or not cid.strip():
        raise ConceptError(f"{path}: `{where}.id` 缺失或为空")

    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ConceptError(f"{path}: `{where}.title` 缺失或为空")

    domain = entry.get("domain", "organic")
    if domain not in VALID_DOMAINS:
        raise ConceptError(
            f"{path}: `{where}.domain` 取值非法：{domain!r}，应为 {VALID_DOMAINS}"
        )

    return Concept(
        id=cid.strip(),
        title=title.strip(),
        domain=domain,
        note=str(entry.get("note", "") or "").strip(),
        prerequisites=_str_tuple(entry.get("prerequisites"), path, where, "prerequisites"),
        related=_str_tuple(entry.get("related"), path, where, "related"),
    )


def _str_tuple(value: object, path: Path, where: str, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConceptError(f"{path}: `{where}.{key}` 必须是列表")
    return tuple(str(v).strip() for v in value if str(v).strip())


# --------------------------------------------------------------------------
# CLI：python -m lifeng.concepts concepts.yaml
# --------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    from lifeng.console import make_output_safe

    make_output_safe()

    path = Path(argv[1]) if len(argv) > 1 else Path("concepts.yaml")
    try:
        graph = ConceptGraph.load(path)
    except ConceptError as exc:
        print(f"[FAIL] {exc}")
        return 1

    if graph.errors:
        for problem in graph.errors:
            print(f"[FAIL] {problem}")
        return 1

    roots = [c.id for c in graph.concepts.values() if not c.prerequisites]
    print(f"[OK] {len(graph)} 个知识点")
    print(f"     无前置的起点: {', '.join(sorted(roots))}")
    print("     前置关系无成环、无悬空引用")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
