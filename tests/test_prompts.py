"""提示词测试。

**为什么 prompt 值得专门写测试**：本项目不预置题库，题目与提示都由 AI 生成，
所以 prompt 就是产品本体。它没有"逻辑"可测，但有一批**必须始终存在的约束** ——
删掉任何一条，产出的质量就会掉一个台阶，而且不会有任何报错。

这些测试守的就是那些约束。
"""

from __future__ import annotations

import pytest

from lifeng import prompts
from lifeng.concepts import ConceptGraph
from lifeng.hint_engine import build_system_prompt as hint_system
from lifeng.models import load_problem

CONCEPT_IDS = [("carbocation-stability", "碳正离子稳定性")]


def _gen_system(**kwargs) -> str:
    defaults = dict(
        problem_id="p900",
        domain="organic",
        concept_ids=CONCEPT_IDS,
        min_pitfalls=3,
    )
    defaults.update(kwargs)
    return prompts.generation_system_prompt(**defaults)


# --------------------------------------------------------------------------
# 出题：系统提示词
# --------------------------------------------------------------------------


class TestGenerationSystem:
    def test_mentions_the_anti_leakage_reason(self):
        """必须说明「骨架会被注入另一个模型的 prompt」这个**原因**。

        只说"不要写答案"是命令；说明原因，模型才会在边界情况下也守住。
        """
        text = _gen_system()
        assert "注入" in text
        assert "答案" in text

    def test_demands_single_answer(self):
        """答案不唯一会让自动判分与复习调度全部失效。"""
        text = _gen_system()
        assert "唯一" in text

    def test_demands_self_contained_statement(self):
        """依赖外部数据的题，模型会编造数值，学生无从核对。"""
        text = _gen_system()
        assert "外部数据" in text or "已知" in text

    def test_includes_concepts_field(self):
        """没有 concepts，AI 出的题就接不进图谱与画像 —— 静默失效。"""
        text = _gen_system()
        assert "concepts:" in text
        assert "carbocation-stability" in text

    def test_includes_fewshot(self):
        """few-shot 比一堆形容词有效得多。"""
        text = _gen_system()
        assert "参考样例" in text
        assert "p000" in text

    def test_fewshot_can_be_disabled(self):
        assert "参考样例" not in _gen_system(include_fewshot=False)

    def test_has_three_iron_rules(self):
        text = _gen_system()
        assert "铁律 1" in text and "铁律 2" in text and "铁律 3" in text

    def test_pitfall_rules_cover_mutual_exclusion_and_observability(self):
        """卡点互斥、可观察 —— 不满足的话统计「卡在哪」就没有意义。"""
        text = _gen_system()
        assert "互斥" in text
        assert "可观察" in text

    def test_forbids_leaking_in_next_step(self):
        """next_step 必须写动作而不是结论，并给出正反例。"""
        text = _gen_system()
        assert "next_step" in text
        assert "反例" in text and "正例" in text

    def test_has_self_check_checklist(self):
        text = _gen_system()
        assert "自检" in text
        # 自检必须逐条可核对，而不是一句"请仔细检查"
        assert text.count("？") >= 5

    def test_problem_id_and_domain_are_substituted(self):
        text = _gen_system(problem_id="p042", domain="physical")
        assert "p042" in text and "physical" in text
        assert "{problem_id}" not in text, "模板占位符没有被替换"

    def test_no_unsubstituted_placeholders(self):
        assert "{" not in _gen_system()

    def test_empty_concept_list_degrades_gracefully(self):
        text = _gen_system(concept_ids=[])
        assert "暂无语表" in text or "词表" in text


# --------------------------------------------------------------------------
# 出题：用户提示词
# --------------------------------------------------------------------------


class TestGenerationUser:
    def test_difficulty_is_spelled_out(self):
        """只说「难一点」是没有信息量的，必须给出可判定的具体标准。"""
        for level in prompts.DIFFICULTY_LEVELS:
            text = prompts.generation_user_prompt(
                problem_id="p900", domain="organic", difficulty=level
            )
            assert prompts.DIFFICULTY_LEVELS[level][:12] in text

    def test_rejects_unknown_difficulty(self):
        with pytest.raises(ValueError):
            prompts.generation_user_prompt(
                problem_id="p900", domain="organic", difficulty="impossible"
            )

    def test_concept_rotation(self):
        """不轮转的话模型会反复出它最熟的那一类题。"""
        text = prompts.generation_user_prompt(
            problem_id="p900",
            domain="organic",
            covered_concepts=("carbocation-rearrangement",),
        )
        assert "轮转" in text
        assert "carbocation-rearrangement" in text

    def test_existing_statements_are_included(self):
        text = prompts.generation_user_prompt(
            problem_id="p900",
            domain="organic",
            existing_statements=("某化合物在酸中重排。",),
        )
        assert "某化合物在酸中重排" in text

    def test_feedback_is_included_verbatim(self):
        text = prompts.generation_user_prompt(
            problem_id="p900", domain="organic", feedback="卡点只有 2 个"
        )
        assert "卡点只有 2 个" in text

    def test_no_feedback_section_when_absent(self):
        text = prompts.generation_user_prompt(problem_id="p900", domain="organic")
        assert "被校验拒绝" not in text


# --------------------------------------------------------------------------
# 提示生成
# --------------------------------------------------------------------------


class TestHintPrompts:
    def test_levels_have_distinct_directives(self):
        texts = [prompts.HINT_LEVEL_DIRECTIVE[i] for i in (1, 2, 3, 4)]
        assert len(set(texts)) == 4, "各层的指令不能相同"

    def test_level1_forbids_specifics(self):
        """第 1 层只给方向。说出具体位置就等于替学生想了一步。"""
        text = prompts.HINT_LEVEL_DIRECTIVE[1]
        assert "禁止" in text

    def test_only_level4_allows_the_answer(self):
        for level in (1, 2, 3):
            assert "不得" in prompts.HINT_LEVEL_DIRECTIVE[level] or "禁止" in prompts.HINT_LEVEL_DIRECTIVE[level]
        assert "允许" in prompts.HINT_LEVEL_DIRECTIVE[4]

    def test_level4_requires_review_of_the_stuck_point(self):
        """第 4 层不只是报答案，要指出学生**卡在哪一类思维动作**上。"""
        text = prompts.HINT_LEVEL_DIRECTIVE[4]
        assert "卡在哪" in text

    def test_system_prompt_frames_the_goal_correctly(self):
        """要的是「哦我该去看那里」，不是「哦原来是这样」。"""
        text = prompts.HINT_SYSTEM
        assert "自己" in text
        assert "只输出提示本身" in text

    def test_answer_only_in_level4(self):
        problem = load_problem("problems/p001.yaml")
        for level in (1, 2, 3):
            text = hint_system(problem, level)
            assert problem.answer.strip() not in text, f"第 {level} 层泄露了答案"
        assert problem.answer.strip() in hint_system(problem, 4)

    def test_progressive_pitfall_disclosure(self):
        """披露得越少，模型能泄露的就越少。"""
        problem = load_problem("problems/p001.yaml")
        pitfall = problem.skeleton.pitfalls[0]
        assert pitfall.trigger in hint_system(problem, 1)
        assert pitfall.next_step not in hint_system(problem, 1)
        assert pitfall.common_wrong_path not in hint_system(problem, 1)
        assert pitfall.next_step in hint_system(problem, 3)

    def test_previous_hints_are_passed_to_avoid_repetition(self):
        """不给上一层的说法，模型会原样重复，学生看到的是「又说了一遍」。"""
        problem = load_problem("problems/p001.yaml")
        text = hint_system(problem, 2, previous_hints=("先检查电子密度最高的位置",))
        assert "先检查电子密度最高的位置" in text
        assert "不要重复" in text

    def test_user_prompt_carries_attempt_and_statement(self):
        text = prompts.hint_user_prompt(
            statement="题干内容", attempt_text="我卡在这里", level=2
        )
        assert "题干内容" in text and "我卡在这里" in text
        assert "第 2 层" in text

    def test_user_prompt_has_no_answer_parameter(self):
        """user prompt 的签名里根本没有 answer —— 想泄露也传不进去。"""
        import inspect

        params = inspect.signature(prompts.hint_user_prompt).parameters
        assert "answer" not in params


# --------------------------------------------------------------------------
# 骨架补全
# --------------------------------------------------------------------------


class TestSkeletonPrompt:
    def test_carries_the_problem(self):
        text = prompts.skeleton_user_prompt(
            statement="题干甲", answer="答案乙", min_pitfalls=3
        )
        assert "题干甲" in text and "答案乙" in text

    def test_forbids_answer_in_skeleton(self):
        text = prompts.skeleton_user_prompt(
            statement="甲", answer="乙", min_pitfalls=3
        )
        assert "不得出现" in text

    def test_frames_it_as_reverse_engineering_students(self):
        """补骨架不是写标准解答，而是**逆向还原学生可能的卡点**。"""
        assert "逆向" in prompts.SKELETON_SYSTEM


class TestProjectPromptIntegration:
    def test_concept_list_uses_real_graph_titles(self):
        graph = ConceptGraph.load("concepts.yaml")
        ids = [(c.id, c.title) for c in graph.concepts.values()]
        rendered = prompts.concept_list_for_prompt(ids)
        first = graph.get(ids[0][0])
        assert first.title in rendered

    def test_generated_system_prompt_fits_within_a_reasonable_budget(self):
        """prompt 太长会挤占上下文，也会让模型忽略后半部分。"""
        graph = ConceptGraph.load("concepts.yaml")
        ids = [(c.id, c.title) for c in graph.concepts.values()]
        text = prompts.generation_system_prompt(
            problem_id="p900",
            domain="organic",
            concept_ids=ids,
            min_pitfalls=3,
        )
        assert len(text) < 12000, f"system prompt 过长：{len(text)} 字符"
