"""题目生成器测试。

用的是假客户端，不消耗 API。重点验证**校验真的会拒绝** ——
一个从不拒绝的生成器等于没有生成器。
"""

from __future__ import annotations

import pytest

from lifeng.generator import (
    DEFAULT_MAX_ATTEMPTS,
    GenerationError,
    extract_yaml,
    generate_batch,
    generate_problem,
    next_problem_id,
    parse_candidate,
    save_problem,
    validate_candidate,
)
from lifeng.models import SkeletonError, load_problem, load_problems

VALID_YAML = """\
id: p900
domain: organic
type: reasoning
skeleton_source: ai
statement: |
  某化合物在酸性条件下发生重排，请写出主产物。
answer: |
  主产物为示例酮。
key_quantities:
  - 示例酮
skeleton:
  insight: |
    关键在于比较迁移前后的中间体稳定性。
  pitfalls:
    - trigger: 学生认为不发生骨架变化。
      next_step: 让学生检查相邻碳上的基团。
      common_wrong_path: 认为中间体已足够稳定。
    - trigger: 学生迁移了错误的基团。
      next_step: 比较两种迁移所得中间体的稳定性。
      common_wrong_path: 默认最小的基团最容易迁移。
    - trigger: 学生说不清驱动力。
      next_step: 要求逐步标出中间体。
      common_wrong_path: 用宏观说法替代中间体分析。
"""


class ScriptedClient:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.systems: list[str] = []
        self.users: list[str] = []

    @property
    def remaining(self) -> int:
        return len(self._responses)

    def complete(self, system: str, user: str) -> str:
        self.systems.append(system)
        self.users.append(user)
        if not self._responses:
            raise AssertionError("剧本已用尽")
        return self._responses.pop(0)


# --------------------------------------------------------------------------
# extract_yaml
# --------------------------------------------------------------------------


class TestExtractYaml:
    def test_plain(self) -> None:
        assert extract_yaml("id: p1\ndomain: organic") == "id: p1\ndomain: organic"

    def test_fenced(self) -> None:
        text = "这是题目：\n```yaml\nid: p1\n```\n以上。"
        assert extract_yaml(text).strip() == "id: p1"

    def test_fenced_without_language(self) -> None:
        assert extract_yaml("```\nid: p1\n```").strip() == "id: p1"

    def test_strips_prose_before(self) -> None:
        text = "好的，我来出题。\n\nid: p1\ndomain: organic"
        assert extract_yaml(text).startswith("id: p1")


# --------------------------------------------------------------------------
# validate_candidate
# --------------------------------------------------------------------------


class TestValidateCandidate:
    def test_valid_passes(self) -> None:
        assert validate_candidate(parse_candidate(VALID_YAML)) == []

    def test_rejects_memory_type(self) -> None:
        """C2：记忆型题不进引导流程。"""
        text = VALID_YAML.replace("type: reasoning", "type: memory")
        issues = validate_candidate(parse_candidate(text))
        assert any("reasoning" in i for i in issues)

    def test_rejects_skeleton_containing_answer(self) -> None:
        """骨架写进答案 → 结构性防泄漏失效。这是最要紧的一道校验。"""
        text = VALID_YAML.replace(
            "关键在于比较迁移前后的中间体稳定性。",
            "最终得到示例酮。",
        )
        issues = validate_candidate(parse_candidate(text))
        assert any("示例酮" in i for i in issues), issues


# --------------------------------------------------------------------------
# generate_problem
# --------------------------------------------------------------------------


class TestGenerateProblem:
    def test_first_try_success(self) -> None:
        client = ScriptedClient([VALID_YAML])
        problem = generate_problem(client, problem_id="p900")
        assert problem.id == "p900"
        assert problem.skeleton_source == "ai"
        assert len(problem.skeleton.pitfalls) == 3

    def test_retries_with_feedback(self) -> None:
        """首次泄漏 → 带反馈重试 → 第二次干净。"""
        leaky = VALID_YAML.replace("关键在于比较迁移前后的中间体稳定性。", "得到示例酮。")
        client = ScriptedClient([leaky, VALID_YAML])

        problem = generate_problem(client, problem_id="p900")

        assert problem.id == "p900"
        assert len(client.users) == 2
        # 第二次必须带上具体问题，否则模型无从修正
        assert "示例酮" in client.users[1]
        assert "不合格" in client.users[1]

    def test_gives_up_after_max_attempts(self) -> None:
        leaky = VALID_YAML.replace("关键在于比较迁移前后的中间体稳定性。", "得到示例酮。")
        client = ScriptedClient([leaky, leaky, leaky])
        with pytest.raises(GenerationError) as exc:
            generate_problem(client, problem_id="p900", max_attempts=3)
        assert "示例酮" in str(exc.value)

    def test_recovers_from_bad_yaml(self) -> None:
        client = ScriptedClient(["这不是 YAML {{{", VALID_YAML])
        problem = generate_problem(client, problem_id="p900")
        assert problem.id == "p900"

    def test_rejects_bad_domain(self) -> None:
        with pytest.raises(ValueError):
            generate_problem(ScriptedClient([]), problem_id="p9", domain="alchemy")

    def test_rejects_bad_max_attempts(self) -> None:
        with pytest.raises(ValueError):
            generate_problem(ScriptedClient([]), problem_id="p9", max_attempts=0)

    def test_prompt_carries_schema_and_rules(self) -> None:
        client = ScriptedClient([VALID_YAML])
        generate_problem(client, problem_id="p900")
        system = client.systems[0]
        assert "key_quantities" in system
        assert "只放最终答案" in system or "只放「最终答案」" in system


# --------------------------------------------------------------------------
# 题号与落盘
# --------------------------------------------------------------------------


class TestProblemIdAndSave:
    def test_next_id_starts_at_p001(self, tmp_path) -> None:
        assert next_problem_id(tmp_path) == "p001"

    def test_next_id_skips_used(self, tmp_path) -> None:
        (tmp_path / "p001.yaml").write_text("id: p001\n", encoding="utf-8")
        (tmp_path / "p002.yaml").write_text("id: p002\n", encoding="utf-8")
        assert next_problem_id(tmp_path) == "p003"

    def test_save_and_reload(self, tmp_path) -> None:
        problem = parse_candidate(VALID_YAML)
        path = save_problem(problem, tmp_path)

        assert path.name == "p900.yaml"
        reloaded = load_problem(path)
        assert reloaded.id == "p900"
        assert reloaded.skeleton_source == "ai"
        assert len(reloaded.skeleton.pitfalls) == 3

    def test_save_refuses_invalid(self, tmp_path) -> None:
        """save 不得成为绕过校验的旁路。"""
        bad = parse_candidate(VALID_YAML.replace("type: reasoning", "type: memory"))
        with pytest.raises(GenerationError):
            save_problem(bad, tmp_path)


class TestGenerateBatch:
    def test_writes_multiple_files(self, tmp_path) -> None:
        templates = [
            VALID_YAML.replace("p900", f"p90{i}").replace("示例酮", f"产物{i}")
            for i in range(3)
        ]
        client = ScriptedClient(templates)

        results = generate_batch(client, 3, directory=tmp_path)

        assert [r.problem.id for r in results] == ["p001", "p002", "p003"]
        assert len(load_problems(tmp_path)) == 3

    def test_passes_existing_statements_to_avoid_duplicates(self, tmp_path) -> None:
        save_problem(parse_candidate(VALID_YAML), tmp_path)
        client = ScriptedClient([VALID_YAML.replace("p900", "p901")])

        generate_batch(client, 1, directory=tmp_path)

        # 第二次生成时，已有题干应作为「不要重复」的提示传下去
        assert "不要与下列已有题目重复" in client.users[0]
