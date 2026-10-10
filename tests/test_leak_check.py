"""T4 单元测试 —— 泄答案校验器。

覆盖计划 T4 要求的边界用例：完全命中 / 部分命中 / 不命中 / 中英文标点差异，
外加全半角、大小写、掩码、骨架卫生与守卫重试。
"""

from __future__ import annotations

import pytest

from lifeng.leak_check import (
    MASK,
    GuardedHint,
    find_leaks,
    guard_hint,
    is_leaked,
    mask_terms,
    normalize,
    skeleton_leaks_answer,
)
from lifeng.models import Problem, Pitfall, Skeleton, load_problem

KEY = ("频哪酮", "3,3-二甲基-2-丁酮", "pinacolone")


# --------------------------------------------------------------------------
# normalize
# --------------------------------------------------------------------------


class TestNormalize:
    def test_casefold(self):
        assert normalize("Pinacolone") == normalize("PINACOLONE")

    def test_whitespace_removed(self):
        assert normalize("频哪 酮\n\t") == normalize("频哪酮")

    def test_fullwidth_to_halfwidth(self):
        # NFKC 把全角字母数字折叠为半角
        assert normalize("ｐｉｎａｃｏｌｏｎｅ") == normalize("pinacolone")

    def test_fullwidth_comma_to_halfwidth(self):
        # 中文全角逗号 → 半角逗号，这是最关键的一条
        assert normalize("3，3-二甲基") == normalize("3,3-二甲基")

    def test_fullwidth_parens(self):
        assert normalize("（测试）") == normalize("(测试)")


# --------------------------------------------------------------------------
# find_leaks / is_leaked
# --------------------------------------------------------------------------


class TestFindLeaks:
    def test_full_hit(self):
        assert find_leaks("重排后得到频哪酮。", KEY) == ["频哪酮"]

    def test_partial_hit(self):
        # 黑名单里三个词，只命中一个
        assert find_leaks("产物是 3,3-二甲基-2-丁酮。", KEY) == ["3,3-二甲基-2-丁酮"]

    def test_multiple_hits(self):
        hits = find_leaks("频哪酮即 pinacolone", KEY)
        assert set(hits) == {"频哪酮", "pinacolone"}

    def test_no_hit(self):
        assert find_leaks("看看这个碳上连了什么基团？", KEY) == []

    def test_empty_hint(self):
        assert find_leaks("", KEY) == []

    def test_empty_key_list(self):
        assert find_leaks("频哪酮", ()) == []

    def test_blank_terms_ignored(self):
        # 空词不应把一切都判成泄漏
        assert find_leaks("任意文本", ("", "   ")) == []

    def test_case_insensitive(self):
        assert find_leaks("PINACOLONE", KEY) == ["pinacolone"]

    def test_fullwidth_comma_in_hint(self):
        """中英文标点差异 —— 提示里写成全角逗号也必须命中。"""
        assert is_leaked("产物是 3，3-二甲基-2-丁酮", KEY) is True

    def test_whitespace_inside_term(self):
        """模型可能在词中间插空格或换行。"""
        assert is_leaked("频哪\n酮", KEY) is True

    def test_substring_of_longer_word(self):
        """子串匹配：'pinacolone' 出现在 'pinacolones' 里应命中。"""
        assert is_leaked("pinacolones", KEY) is True

    def test_mid_sentence_hit(self):
        assert is_leaked("所以你会得到频哪酮这个产物", KEY) is True


# --------------------------------------------------------------------------
# mask_terms
# --------------------------------------------------------------------------


class TestMaskTerms:
    def test_basic_mask(self):
        assert mask_terms("得到频哪酮", KEY) == f"得到{MASK}"

    def test_case_insensitive_mask(self):
        assert mask_terms("PINACOLONE", KEY) == MASK

    def test_longest_first(self):
        """先掩码长词，避免短词先替换后留下残片。"""
        result = mask_terms("3,3-二甲基-2-丁酮", KEY)
        assert result == MASK

    def test_no_terms(self):
        assert mask_terms("任意文本", ()) == "任意文本"

    def test_mask_removes_leak(self):
        """掩码后必须不再被判定为泄漏 —— 这是 C1 的最后保证。"""
        dirty = "重排后得到频哪酮，也就是 pinacolone"
        assert is_leaked(dirty, KEY) is True
        assert is_leaked(mask_terms(dirty, KEY), KEY) is False


# --------------------------------------------------------------------------
# 骨架卫生
# --------------------------------------------------------------------------


def _problem_with_skeleton(insight: str) -> Problem:
    return Problem(
        id="t001",
        domain="organic",
        type="reasoning",
        statement="题干",
        answer="答案",
        key_quantities=KEY,
        skeleton=Skeleton(
            insight=insight,
            pitfalls=tuple(
                Pitfall(trigger=f"t{i}", next_step=f"n{i}", common_wrong_path=f"w{i}")
                for i in range(3)
            ),
        ),
    )


class TestSkeletonHygiene:
    def test_clean_skeleton_passes(self):
        assert skeleton_leaks_answer(_problem_with_skeleton("关注电子效应")) == []

    def test_skeleton_containing_answer_is_caught(self):
        """骨架写进答案 → 结构性防泄漏失效，必须检出。"""
        assert skeleton_leaks_answer(_problem_with_skeleton("最终得到频哪酮")) == ["频哪酮"]

    def test_no_skeleton(self):
        p = Problem(
            id="m1", domain="inorganic", type="memory", statement="s",
            answer="a", key_quantities=("a",), skeleton=None,
        )
        assert skeleton_leaks_answer(p) == []

    def test_real_sample_is_clean(self):
        """p001 的骨架必须不含答案 —— 这是 T6 标注卫生的回归线。"""
        p = load_problem("problems/p001.yaml")
        assert skeleton_leaks_answer(p) == []


# --------------------------------------------------------------------------
# guard_hint
# --------------------------------------------------------------------------


class ScriptedClient:
    """按剧本返回，并记录收到的 prompt。"""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.systems: list[str] = []
        self.users: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.systems.append(system)
        self.users.append(user)
        if not self._responses:
            raise AssertionError("剧本已用尽")
        return self._responses.pop(0)


@pytest.fixture
def problem() -> Problem:
    return load_problem("problems/p001.yaml")


class TestGuardHint:
    def test_clean_first_try(self, problem):
        client = ScriptedClient(["先想想这个碳上连了什么？"])
        result = guard_hint(problem, "我卡住了", 1, client=client)
        assert isinstance(result, GuardedHint)
        assert result.attempts == 1
        assert result.leaked_attempts == 0
        assert result.degraded is False

    def test_retry_then_success(self, problem):
        """首次泄漏 → 重生成 → 第二次干净。"""
        client = ScriptedClient(["重排得到频哪酮", "换个角度：这个碳缺电子吗？"])
        result = guard_hint(problem, "我卡住了", 1, client=client)
        assert result.attempts == 2
        assert result.leaked_attempts == 1
        assert result.degraded is False
        assert is_leaked(result.text, problem.key_quantities) is False

    def test_retry_prompt_carries_correction(self, problem):
        client = ScriptedClient(["频哪酮", "干净提示"])
        guard_hint(problem, "卡住", 1, client=client)
        # 第二次的 user prompt 必须带上纠错指令
        assert "不合格" in client.users[1]
        assert client.users[1] != client.users[0]

    def test_persistent_leak_falls_back_to_mask(self, problem):
        """三次都泄漏 → 掩码兜底，且标记为 degraded。"""
        client = ScriptedClient(["频哪酮", "还是频哪酮", "又是频哪酮"])
        result = guard_hint(problem, "卡住", 1, client=client, max_attempts=3)
        assert result.attempts == 3
        assert result.leaked_attempts == 3
        assert result.degraded is True
        assert result.masked_terms == ("频哪酮",)
        # C1 最终仍然成立
        assert is_leaked(result.text, problem.key_quantities) is False
        assert MASK in result.text

    def test_level4_is_exempt(self, problem):
        """层 4 允许给出答案，不做泄漏拦截。"""
        client = ScriptedClient(["答案是频哪酮"])
        result = guard_hint(problem, "卡住", 4, client=client)
        assert result.attempts == 1
        assert result.degraded is False
        assert "频哪酮" in result.text

    def test_invalid_max_attempts(self, problem):
        with pytest.raises(ValueError):
            guard_hint(problem, "x", 1, client=ScriptedClient([]), max_attempts=0)
