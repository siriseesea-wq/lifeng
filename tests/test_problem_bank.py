"""题库内容守卫。

题库是**教育内容**，错了会教错人。而且骨架会被注入提示生成的 prompt ——
骨架里一旦写了答案词，「模型看不到答案就说不出答案」这条结构性防线
直接失效。

所以把题库本身纳入 CI：新增题目如果不合规，测试立刻失败。

> 实测价值：p008 首次写入时，schema 校验**放行**了它
> （字段齐全、枚举合法），是**骨架卫生检查**抓出骨架里出现了答案词。
> 两道校验各司其职，都需要。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lifeng.leak_check import skeleton_leaks_answer
from lifeng.models import MIN_PITFALLS, load_problems

PROBLEMS_DIR = Path(__file__).resolve().parent.parent / "problems"

#: 低于这个数量说明题库被误删或被清空。
MIN_EXPECTED_PROBLEMS = 10


@pytest.fixture(scope="module")
def bank():
    return load_problems(PROBLEMS_DIR)


class TestBankShape:
    def test_bank_is_not_empty(self, bank) -> None:
        assert len(bank) >= MIN_EXPECTED_PROBLEMS, (
            f"题库只有 {len(bank)} 题，少于预期的 {MIN_EXPECTED_PROBLEMS}"
        )

    def test_all_loadable(self, bank) -> None:
        """load_problems 本身就会在不合规时抛错；这里只需确认没抛。"""
        assert bank

    def test_ids_unique_and_sorted(self, bank) -> None:
        ids = [p.id for p in bank]
        assert len(ids) == len(set(ids))
        assert ids == sorted(ids), "题号应能按顺序排列（p001, p002, …）"

    def test_every_problem_is_hintable(self, bank) -> None:
        """题库里的题都必须能进引导流程，否则学生抽到它就是死路。"""
        dead = [p.id for p in bank if not p.hintable]
        assert not dead, f"以下题目不可引导（类型或骨架有问题）：{dead}"


class TestSkeletonHygiene:
    @pytest.mark.parametrize("index", range(MIN_EXPECTED_PROBLEMS))
    def test_skeleton_does_not_leak_answer(self, bank, index) -> None:
        """骨架不得含答案关键量 —— 否则结构性防泄漏失效。"""
        if index >= len(bank):
            pytest.skip("题库不足，已在 TestBankShape 中报告")
        problem = bank[index]
        leaks = skeleton_leaks_answer(problem)
        assert not leaks, (
            f"{problem.id} 的骨架中出现了答案关键量 {leaks}。\n"
            "骨架会被注入提示生成的 prompt，等于把答案直接递给模型。"
        )

    def test_key_quantities_non_empty(self, bank) -> None:
        """C1 的校验依据不能为空，否则泄漏根本无从判断。"""
        empty = [p.id for p in bank if not p.key_quantities]
        assert not empty, f"以下题目缺少 key_quantities：{empty}"

    def test_every_problem_has_enough_pitfalls(self, bank) -> None:
        thin = [
            p.id for p in bank
            if p.skeleton is None or len(p.skeleton.pitfalls) < MIN_PITFALLS
        ]
        assert not thin, f"以下题目卡点不足 {MIN_PITFALLS} 个：{thin}"


class TestC2Separation:
    def test_bank_is_reasoning_only(self, bank) -> None:
        """v1 只做有机推理题；混入记忆型说明选题跑偏了（C2）。"""
        wrong = [p.id for p in bank if p.type != "reasoning"]
        assert not wrong, f"以下题目不是推理型：{wrong}"

    def test_bank_is_organic_only(self, bank) -> None:
        """Spec §2.3：v1 科目范围限定为有机化学。"""
        wrong = [p.id for p in bank if p.domain != "organic"]
        assert not wrong, f"以下题目不在 v1 科目范围内：{wrong}"


class TestSourceTracking:
    def test_skeleton_source_is_declared(self, bank) -> None:
        """每道题都要能说清骨架是谁写的 —— 见 Design Spec 决策 4 补充记录。"""
        for problem in bank:
            assert problem.skeleton_source in ("human", "ai")

    def test_ai_generated_are_reported(self, bank) -> None:
        """不设阈值，只要求在报告里可见 —— 便于将来实验时区分。"""
        ai = [p.id for p in bank if p.skeleton_source == "ai"]
        human = [p.id for p in bank if p.skeleton_source == "human"]
        # 两者都是合法状态；这里只断言统计不抛错，实际数值由人查看
        assert len(ai) + len(human) == len(bank)
