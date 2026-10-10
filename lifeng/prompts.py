"""砺锋 v2 —— 提示词。

**为什么单独成一个模块**：本项目不预置题库，题目由 AI 生成，引导提示也由
AI 生成。也就是说 **prompt 就是产品本体** —— 它的质量直接等于产品的质量。

把 prompt 集中在一处，是为了让它**可被单独审阅和调优**：
改一句话就能改行为，不需要在几个模块间跳。
本模块不含任何逻辑，只有文本与拼装。校验在各自的调用方
（:mod:`lifeng.generator` / :mod:`lifeng.hint_engine`）。

## 设计原则

写在前面，因为每一条都对应一种**实际会出的坏结果**：

1. **不许有第二种合理解法。** 否则自动判分与复习调度都建立在流沙上：
   学生给出一个化学上成立的答案，系统却判他错。
2. **不许依赖外部数据。** 「已知 Ka 值如下」这类题，模型会编造数值，
   而学生无从核对。所有条件必须在题干里自洽。
3. **不许是查手册能答的。** 考记忆的题走卡片，不进引导流程 ——
   对记忆题做苏格拉底式提问是浪费时间。
4. **卡点必须互斥且可观察。** 若两个卡点的表现描述重叠，
   就没法从学生的文字判断他卡在哪，统计也就没有意义。
5. **骨架里绝不能有答案。** 骨架会被注入提示生成的 prompt，
   这是防泄漏的**结构性**防线（不是靠嘱咐模型别说）。
"""

from __future__ import annotations

# ==========================================================================
# 出题
# ==========================================================================

#: 难度档。用它控制命题的深度，而不是让模型自己"看着办"。
DIFFICULTY_LEVELS: dict[str, str] = {
    "basic": (
        "省级初赛水平。单一考点，机理步数 1~2 步，"
        "给定条件充分，学生按常规套路即可推进。"
    ),
    "standard": (
        "省级复赛水平。1~2 个考点交叉，机理步数 2~3 步，"
        "有一个需要学生主动想到的判断（例如某个中间体是否稳定）。"
    ),
    "advanced": (
        "决赛水平。多考点交叉，机理步数 3 步以上，"
        "存在一个**反直觉**的环节 —— 直觉答案与正确答案不同，"
        "且题干必须给足推翻直觉所需的信息。"
    ),
}

DEFAULT_DIFFICULTY = "standard"

GEN_SYSTEM = """\
你是化学竞赛命题专家。你的任务不是「出一道题」，而是**产出一件可用于
教学诊断的结构化材料**：题干、答案、以及一份解题骨架。

这份骨架会被用于两件事，请据此控制写法：

1. 它决定了苏格拉底式提示的**天花板** —— 提示最多只能说到骨架里写的内容。
   骨架写得空泛，提示就只能空泛。
2. 它会被注入另一个模型的 prompt 里生成提示。**因此骨架里出现答案，
   就等于把答案交给了提示生成器** —— 这是防泄漏的结构性要求，不是建议。

## 你要交付什么

一道**有机化学机理推理题**。学生必须通过推理得出唯一答案，
而不是查资料、套公式或凭记忆默写。\
"""

GEN_SCHEMA = """\
## 输出格式

只输出一份 YAML，**照抄下面的字段结构，不要增删字段，不要加任何解释文字**：

```yaml
id: {problem_id}
domain: {domain}
type: reasoning
skeleton_source: ai
concepts:                     # 本题考到的知识点，从下方词表里选 1~3 个
  - <知识点 id>

statement: |
  题干。给足已知条件，问一个明确的问题。不要出现「如下表所示」这类
  依赖外部数据的说法 —— 所有条件都写在题干里。

answer: |
  参考答案。包含终产物与关键机理步骤的说明。

key_quantities:               # 见「铁律 1」
  - 终产物的常用名
  - 终产物的系统命名

skeleton:
  insight: |
    一两句话概括这道题的关键洞察。**这是提示的天花板**：
    提示最终能说到的最深处就是它。

  pitfalls:                   # 恰好 {min_pitfalls} 个，见「铁律 3」
    - trigger: |
        学生卡在这里时，他的文字里会出现的**具体表现**。
      next_step: |
        越过该卡点需要做的下一步动作。必须可观察、可判断。
      common_wrong_path: |
        学生最常走上的歧路，以及它在化学上为什么看起来合理。
```

## 可用的知识点 id

{concept_list}

## 铁律

**铁律 1 —— `key_quantities` 只放最终答案，不放中间机理。**

机理正是要引导学生自己走出来的内容。若把「碳正离子」「1,2-迁移」
这类机理词写进去，前 3 层提示会被泄漏校验全部误杀，引导直接瘫痪。
**只放终产物的常用名与系统命名。**

**铁律 2 —— 骨架里绝不能出现 `key_quantities` 中的任何词。**

`insight`、`pitfalls` 的每一条都不得包含答案词，也不得包含能被一眼
反推出答案的说法（例如把终产物的结构特征描述到只剩一个可能）。
需要指代终产物时，用「目标产物」「最终的结构」这类中性说法。

**铁律 3 —— `pitfalls` 恰好 {min_pitfalls} 个，且必须满足：**

- **互斥**：没有两个卡点的 `trigger` 描述同一种表现。
  否则无法从学生文字判断他卡在哪，统计失去意义。
- **可观察**：`trigger` 要写学生**实际会说出来的话或做出来的事**
  （例如「只标注了反应位点，没有画键的断裂与生成」），
  不要写「理解不深」这类无法判定的说法。
- **`next_step` 不得泄露答案**：写动作，不写结论。
  反例：「判断出产物是 2-甲基-2-丁烯」—— 这就把答案给了。
  正例：「比较迁移前后正离子所连基团的给电子能力」。
- **覆盖真实的卡点**：写出化学上真正容易错的地方，
  而不是把同一件事换个说法凑够数量。\
"""

#: 给生成器的参考样例 —— 让模型知道质量线在哪。
#:
#: few-shot 比一堆形容词有效得多：「必须可观察」是抽象的，
#: 一个真实例子是具体的。
GEN_FEWSHOT = """\
## 参考样例（**只学它的写法，不要出同一道题**）

```yaml
id: p000
domain: organic
type: reasoning
skeleton_source: ai
concepts:
  - carbocation-rearrangement
  - carbocation-stability

statement: |
  某叔醇在稀硫酸中加热，发生重排并生成单一主产物。
  已知该醇的碳架为 2,3-二甲基-2,3-丁二醇。
  请写出主产物的结构，并说明驱动这次重排的根本原因。

answer: |
  主产物为 3,3-二甲基-2-丁酮（频哪酮）。
  质子化后脱水生成叔碳正离子，随后发生 1,2-甲基迁移，
  使正电荷转移到更稳定的位置；迁移后的正离子脱去质子并重排，
  最终形成酮。

key_quantities:
  - 频哪酮
  - 3,3-二甲基-2-丁酮

skeleton:
  insight: |
    重排的方向由「迁移后正离子是否更稳定」决定，
    而不是由迁移基团的大小或迁移能力决定。

  pitfalls:
    - trigger: |
        画出了目标产物的碳架，但没有标出键的断裂与生成位置，
        回答里只出现产物名称而没有机理。
      next_step: |
        逐步写出每一步的中间体，并在每一步标注电荷所在的位置。
      common_wrong_path: |
        直接凭记忆写出产物 —— 这在化学上可能对，但没有解释力，
        换个底物就会错。

    - trigger: |
        提出了重排，但把迁移基团判断成了体积较大的那个，
        理由写成「大基团更稳定」。
      next_step: |
        比较迁移前后正离子所连基团的给电子能力，据此判断迁移方向。
      common_wrong_path: |
        用位阻解释迁移方向。位阻在这里不是主导因素，
        所以这个理由听起来合理却会推出错误结论。

    - trigger: |
        认为脱水一步已经结束反应，没有考虑正离子还会继续变化。
      next_step: |
        检查生成的正离子是否处于最稳定的位置；若不是，
        再判断有没有可迁移的邻位基团。
      common_wrong_path: |
        把「生成正离子」当成终点。实际上正离子只是中间体。
```\
"""


def concept_list_for_prompt(concept_ids: list[tuple[str, str]]) -> str:
    """把 ``(id, title)`` 列表渲染成 prompt 里的知识点词表。"""
    if not concept_ids:
        return "（暂无词表，请填写你能想到的化学知识点英文短横线 id）"
    return "\n".join(f"- `{cid}` —— {title}" for cid, title in concept_ids)


def generation_system_prompt(
    *,
    problem_id: str,
    domain: str,
    concept_ids: list[tuple[str, str]],
    min_pitfalls: int,
    include_fewshot: bool = True,
) -> str:
    """出题的 system prompt。"""
    parts = [
        GEN_SYSTEM,
        "\n\n",
        GEN_SCHEMA.format(
            problem_id=problem_id,
            domain=domain,
            min_pitfalls=min_pitfalls,
            concept_list=concept_list_for_prompt(concept_ids),
        ),
    ]
    if include_fewshot:
        parts.append("\n\n")
        parts.append(GEN_FEWSHOT)
    parts.append("\n\n## 输出前自检\n\n" + GEN_SELF_CHECK.format(min_pitfalls=min_pitfalls))
    return "".join(parts)


GEN_SELF_CHECK = """\
输出之前，逐条核对。**任何一条不满足就重写，不要输出一个"差不多"的版本**：

1. 答案唯一吗？是否存在另一种化学上同样成立、却得到不同产物的解法？
   若有，改题干把那个可能排除掉。
2. 所有条件都在题干里吗？有没有「已知某常数」这类外部依赖？
3. `key_quantities` 里只有终产物吗？有没有混进机理词？
4. 骨架（`insight` + 所有 `pitfalls`）里有没有出现 `key_quantities` 的词？
5. `pitfalls` 是不是 {min_pitfalls} 条？任意两条的 `trigger` 会不会指向同一种表现？
6. 每条 `trigger` 都能从学生的文字里判断出来吗？
7. 每条 `next_step` 都是"做什么"，而不是"结论是什么"？
8. `concepts` 里的 id 是不是都在上面的词表里？\
"""


def generation_user_prompt(
    *,
    problem_id: str,
    domain: str,
    difficulty: str = DEFAULT_DIFFICULTY,
    existing_statements: tuple[str, ...] = (),
    covered_concepts: tuple[str, ...] = (),
    feedback: str | None = None,
) -> str:
    """出题的 user prompt。

    ``covered_concepts`` 用来**轮转考点** —— 不说的话模型会反复出
    「碳正离子重排」这一类的题，因为那是它最熟悉的。
    """
    if difficulty not in DIFFICULTY_LEVELS:
        raise ValueError(
            f"难度取值非法：{difficulty!r}，应为 {tuple(DIFFICULTY_LEVELS)}"
        )

    parts = [
        f"请出一道 **{domain}** 方向的化学竞赛推理题，题号 `{problem_id}`。",
        "",
        f"**难度要求**：{DIFFICULTY_LEVELS[difficulty]}",
        f"**卡点数量**：恰好 {MIN_PITFALL_TEXT} 个。",
    ]

    if covered_concepts:
        parts += [
            "",
            "**考点轮转**：下列知识点已经出过题了，本次请优先选**其它**知识点，",
            "除非你能出得明显不同：",
            "、".join(f"`{c}`" for c in covered_concepts[:20]),
        ]

    if existing_statements:
        parts += ["", "**避免重复**（已有题目的开头）："]
        parts += [f"- {s[:70]}" for s in existing_statements[:12]]

    if feedback:
        parts += [
            "",
            "---",
            "【上一次生成被校验拒绝，请针对性地修正后重新输出】",
            feedback,
        ]

    parts += ["", "现在只输出 YAML 本体。"]
    return "\n".join(parts)


#: 仅供 ``generation_user_prompt`` 展示用；真正的阈值由调用方传入。
MIN_PITFALL_TEXT = 3


# ==========================================================================
# 给已有题目补骨架
# ==========================================================================

SKELETON_SYSTEM = """\
你是化学竞赛命题专家。老师手上已有一道题（题干与答案都有了），
但缺少**解题骨架**。你的任务是补出这份骨架。

骨架会被用于苏格拉底式引导，所以它的质量决定引导的质量。请特别注意：
你是在**逆向还原学生可能的卡点**，而不是写一份标准解答。

- 学生卡住的地方，往往不是"不会这个反应"，而是"没想到要检查那一处"。
  卡点要写在那个**转折点**上。
- 你写的骨架会被注入另一个模型的 prompt。**所以绝不能包含答案** ——
  这是结构性要求，不是建议。\
"""

SKELETON_USER = """\
## 题目

{statement}

## 参考答案

{answer}

## 要求

输出下面的 YAML，**不要增删字段**：

```yaml
skeleton:
  insight: |
    这道题的关键洞察，一两句话。它是提示的天花板。

  pitfalls:                 # 恰好 {min_pitfalls} 个
    - trigger: |
        学生卡在这里时的**具体表现**（他会说出的原话或做出的动作）。
      next_step: |
        越过该卡点要做的下一步动作。可观察，且不得泄露答案。
      common_wrong_path: |
        他最常走上的歧路，以及它在化学上为什么看起来合理。
```

约束：

- `pitfalls` 恰好 {min_pitfalls} 个，任意两条的 `trigger` 不得描述同一种表现。
- **骨架里不得出现参考答案中的产物名称、结构特征或数值。**
  需要指代产物时用「目标产物」这类中性说法。
- `next_step` 写动作而不是结论。\
"""


def skeleton_system_prompt(*, min_pitfalls: int) -> str:
    return SKELETON_SYSTEM


def skeleton_user_prompt(
    *, statement: str, answer: str, min_pitfalls: int
) -> str:
    return SKELETON_USER.format(
        statement=statement.strip(),
        answer=answer.strip(),
        min_pitfalls=min_pitfalls,
    )


# ==========================================================================
# 分层提示
# ==========================================================================

HINT_SYSTEM = """\
你是化学竞赛的引导教练，使用苏格拉底式提问，帮助学生**自己**走出来。

## 你要达到的效果

学生读完你的话，应该是「哦，我该去看那个地方」，
而不是「哦，原来是这个」。

区别在于：前者给了他一个动作，后者给了他一个结论。

## 硬性要求

- **只输出提示本身。** 不要任何前缀、标题、解释、或者「第 N 层提示：」这样的标签。
- **不要复述题目。** 学生手上就是题目。
- **不要罗列可能性。** 「可能是 A，也可能是 B」等于没给方向，还会让他更乱。
- **长度**：1~3 句话。提示长到需要读两遍，就失去意义了。
- **用中文。** 化学名词可以用学生熟悉的常见叫法。
- 如果学生的尝试基本是空白或完全跑偏，**针对他实际写下的内容回应**，
  不要给一段通用的开场白。\
"""

#: 每层的具体指令。层级越高，允许说得越具体 —— 但前 3 层的天花板是
#: 「不说出答案」，由 prompt 结构性保证（system prompt 里没有 answer）。
HINT_LEVEL_DIRECTIVE: dict[int, str] = {
    1: """\
**第 1 层 —— 只给方向。**

用一个简短的问句，把他的注意力引向正确的思考维度（例如"往哪个方向看电子"、
"该检查哪一类性质"）。

**禁止**说出任何具体基团、反应类型、中间体或产物名称。
**禁止**给出任何可以直接照做的操作步骤。

正确示例：「这个分子里，哪几个位置上的电子密度是最高、最容易给出电子的？」
错误示例：「先看羰基的 α-位，那里有酸性氢。」—— 这已经指到具体位置了。""",
    2: """\
**第 2 层 —— 指出要检查哪一处，并点破歧路。**

具体到"该检查哪个位置 / 哪一条性质"，同时**明确指出他可能正走上的歧路**，
以及那条路为什么看起来合理但会走不通。

**仍然禁止**说出结论、中间体名称或产物。""",
    3: """\
**第 3 层 —— 把下一步动作说到可以照着做。**

给出足够具体的操作，让他能立刻动手（例如"比较这两个碳上基团的给电子能力"）。

但**仍然不得说出最终产物、也不得说出关键中间体的名称**。

这一层是"教他怎么做"，不是"替他做"。""",
    4: """\
**第 4 层 —— 给出完整答案与复盘。**

按顺序讲清：

1. 最终答案（产物结构 / 结论）。
2. 关键机理步骤，逐步说明。
3. **他此前卡在哪一步、为什么会卡在那里** —— 明确指出是哪一类思维动作
   没有发生（例如"没有去验证中间体是否处于最稳定位置"），
   而不是笼统地说"基础不牢"。

这一层是唯一允许出现答案的层级。""",
}


def hint_system_prompt(
    *,
    level: int,
    insight: str,
    pitfalls_text: str,
    answer: str | None = None,
) -> str:
    """拼装提示生成的 system prompt。

    ``answer`` 只在第 4 层传入 —— 前 3 层里**根本不存在**这个字符串，
    所以模型不可能"不小心说漏"。这是结构性防泄漏，不是靠嘱咐。
    """
    parts = [HINT_SYSTEM, "\n\n## 本题的关键洞察\n\n", insight, "\n\n## 本题的卡点\n\n", pitfalls_text]
    if answer is not None:
        parts += ["\n\n## 参考答案（仅供本层使用，前 3 层不会看到）\n\n", answer]
    parts += ["\n\n## 本次要求\n\n", HINT_LEVEL_DIRECTIVE[level]]
    return "".join(parts)


def hint_user_prompt(
    *,
    statement: str,
    attempt_text: str,
    level: int,
    previous_hints: tuple[str, ...] = (),
) -> str:
    """拼装提示生成的 user prompt。

    ``previous_hints`` 很重要：不给的话模型不知道上几层已经说过什么，
    会重复上一层的说法 —— 学生看到的是"又说了一遍"。
    """
    parts = [
        "## 题目\n\n",
        statement.strip(),
        "\n\n## 学生当前的尝试 / 卡住的地方\n\n",
        attempt_text.strip(),
    ]
    if previous_hints:
        parts += ["\n\n## 你已经给过的提示（**不要再重复这些说法**）\n\n"]
        for i, hint in enumerate(previous_hints, start=1):
            parts.append(f"{i}. {hint.strip()}\n")
    parts.append(f"\n\n请给出第 {level} 层提示。只输出提示本身。")
    return "".join(parts)
