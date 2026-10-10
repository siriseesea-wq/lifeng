"""砺锋 v2 —— 资料库检索（轻量 RAG）。

**覆盖引用项目的功能**：DeepTutor 与「活书 learning-agent」都有
文档入库 + 检索（RAG）。本模块提供同样的能力，但**刻意不引入向量库**：

- 题库规模是几十道，资料是几篇笔记 —— 这个量级下向量检索的
  收益抵不过它的代价（chromadb 会显著撑大已经 300MB+ 的打包体积）
- 用 BM25，纯标准库，可离线、可解释

**为什么不用向量检索**：向量模型要下载、要联网、要额外几百 MB；
而 BM25 在「几十篇文档里找相关段落」这件事上并不明显更差。
真到了几千篇的规模再换，那时换才有意义。

**为什么检索结果不喂给提示模型**：提示生成那条链路有 C1 硬约束
（前 3 层不得泄答案）与掩码兜底。往里塞未经审查的教材文本，
等于绕过那套校验。所以这里的用法是**把段落显示给学生看**，
由学生自己用 —— 既不碰防泄漏链路，又确实是 RAG 的有用部分。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

#: 支持的资料格式。刻意只收纯文本：PDF 要额外依赖，且解析质量不稳定，
#: 让老师自己粘成 .md 比装一个不靠谱的解析器更可靠。
SUPPORTED_SUFFIXES = (".md", ".txt", ".markdown")

#: 一个段落的字数上限，超过就切开 —— 太长的段落检索不精确、读起来也累。
MAX_CHUNK_CHARS = 900

#: BM25 参数（标准默认值）。
BM25_K1 = 1.5
BM25_B = 0.75

#: 一个段落至少命中多少个**不同**查询词，才算相关。
#:
#: 这个常数是量出来的，不是拍的。实测（16 段示例资料、10 道题）：
#:
#: ==================  ==============  ==========
#: 判据                真实匹配         噪声查询
#: ==================  ==============  ==========
#: 命中率（覆盖率）      10.2% ~ 24.1%   22.2%   ← 分不开
#: 信息量占比            7.0% ~ 14.6%    16.4%   ← 分不开
#: BM25 原始分数         14.2 ~ 27.1      5.4    ← 能分
#: **命中词数**          **6 ~ 13**       **2**  ← **余量最大**
#: ==================  ==============  ==========
#:
#: 取 3 是因为真实匹配最少 6、噪声最多 2，两侧都有余量。
MIN_MATCHED_TERMS = 3

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Material:
    """资料库里的一个段落。"""

    source: str      # 相对路径
    heading: str     # 所属标题
    text: str

    @property
    def label(self) -> str:
        return f"{self.source} · {self.heading}" if self.heading else self.source


def tokenize(text: str) -> list[str]:
    """中文按二元组切，英文数字按词。

    中文没有空格，按字切会让「碳正离子」贡献 4 个几乎无区分度的单字。
    二元组（碳正/正离/离子）保留了局部顺序，是纯标准库下最划算的做法。
    """
    lowered = text.lower()
    tokens = _WORD.findall(lowered)

    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def _chunk_markdown(text: str, source: str) -> list[Material]:
    """按标题切段；没有标题就整篇当一段。段落过长再按长度切。"""
    chunks: list[Material] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        body = "\n".join(buffer).strip()
        buffer.clear()
        if not body:
            return
        if len(body) <= MAX_CHUNK_CHARS:
            chunks.append(Material(source=source, heading=heading, text=body))
            return
        for i in range(0, len(body), MAX_CHUNK_CHARS):
            chunks.append(
                Material(source=source, heading=heading, text=body[i : i + MAX_CHUNK_CHARS])
            )

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip()
        else:
            buffer.append(line)
    flush()
    return chunks


def load_materials(directory: str | Path) -> list[Material]:
    """装入目录下所有支持的文本资料。目录不存在时返回空列表。

    刻意不抛异常：没有资料库时这个功能应当**静默失效**，
    而不是让整个应用起不来。
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    materials: list[Material] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            rel = str(path.relative_to(directory))
        except ValueError:
            rel = path.name
        materials.extend(_chunk_markdown(text, rel))
    return materials


def _bm25_detail(
    materials: list[Material], query: str
) -> tuple[list[float], list[int]]:
    """返回 (每个段落的 BM25 分数, 每个段落命中的**不同**查询词数)。

    命中词数单独返回，是因为**分数本身不足以判断相关性**：
    查询越长 BM25 和越大，固定阈值无从设起。而「只是碰巧共用一个词」
    这种情况，靠命中词数才拦得住。

    实测的例子：查询「量子色动力学胶子禁闭」曾返回「共轭二烯的加成」——
    只因为查询里的「动力」撞上了资料里的「动力学产物」。
    """
    query_tokens = tokenize(query)
    distinct_query = set(query_tokens)
    if not materials or not distinct_query:
        return [0.0] * len(materials), [0] * len(materials)

    docs = [tokenize(m.text + " " + m.heading) for m in materials]
    lengths = [len(d) for d in docs]
    avg_length = sum(lengths) / len(lengths) if lengths else 0.0

    df: Counter[str] = Counter()
    for doc in docs:
        for token in set(doc):
            df[token] += 1

    total = len(materials)
    scores: list[float] = []
    matched_counts: list[int] = []
    for doc, length in zip(docs, lengths):
        counts = Counter(doc)
        score = 0.0
        matched = 0
        for token in distinct_query:
            tf = counts.get(token, 0)
            if not tf:
                continue
            matched += 1
            idf = math.log(1 + (total - df[token] + 0.5) / (df[token] + 0.5))
            denom = tf + BM25_K1 * (
                1 - BM25_B + BM25_B * (length / avg_length if avg_length else 1)
            )
            score += idf * (tf * (BM25_K1 + 1)) / denom
        scores.append(score)
        matched_counts.append(matched)
    return scores, matched_counts


def bm25_scores(materials: list[Material], query: str) -> list[float]:
    """对每个段落算 BM25 分数。查询为空时全为 0。"""
    return _bm25_detail(materials, query)[0]


def retrieve(
    materials: list[Material], query: str, *, limit: int = 3, min_score: float = 0.5
) -> list[tuple[Material, float]]:
    """返回最相关的若干段落，分数降序。

    两道闸门，缺一不可：

    1. ``min_score`` —— 绝对分数下限（兜底）
    2. **至少命中 :data:`MIN_MATCHED_TERMS` 个不同的查询词** —— 主判据

    第 2 条是实测逼出来的，也是这几条判据里余量最大的：
    只靠阈值或覆盖率时，「量子色动力学胶子禁闭」会因为「动力」「力学」
    两个词都撞上资料里的「动力学产物」而被判为相关 —— 而那两个词
    其实来自同一个词，只算**一次**巧合。
    """
    if limit < 1:
        raise ValueError(f"limit 至少为 1，收到 {limit}")

    scores, matched_counts = _bm25_detail(materials, query)
    distinct_query = set(tokenize(query))
    # 查询本身就短时，不能要求命中比它词数还多
    required = min(MIN_MATCHED_TERMS, len(distinct_query)) if distinct_query else 1

    ranked = sorted(
        (
            (m, s)
            for m, s, matched in zip(materials, scores, matched_counts)
            if s >= min_score and matched >= required
        ),
        key=lambda pair: -pair[1],
    )
    return ranked[:limit]


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))
