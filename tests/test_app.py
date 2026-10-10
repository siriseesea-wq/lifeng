"""T5 单元测试 —— 学生界面的可测核心。

Streamlit 层无法在无浏览器环境下点击验证，因此把核心交互
（:func:`request_hint` / :func:`log_baseline_attempt` / :func:`next_level`）
抽成纯函数单独测试；UI 层另有启动验证（见 README）。
"""

from __future__ import annotations

import pytest

from lifeng.app import MAX_LEVEL, log_baseline_attempt, next_level, request_hint
from lifeng.models import load_problem
from lifeng.store import Store


class ScriptedClient:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)

    def complete(self, system: str, user: str) -> str:
        if not self._responses:
            raise AssertionError("剧本已用尽")
        return self._responses.pop(0)


@pytest.fixture
def problem():
    return load_problem("problems/p001.yaml")


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "t.db") as s:
        yield s


class TestNextLevel:
    def test_progression(self):
        assert next_level(0) == 1
        assert next_level(1) == 2
        assert next_level(2) == 3
        assert next_level(3) == 4

    def test_capped_at_max(self):
        assert next_level(MAX_LEVEL) == MAX_LEVEL
        assert next_level(99) == MAX_LEVEL

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            next_level(-1)


class TestRequestHint:
    def test_logs_attempt_and_returns_hint(self, problem, store):
        sid = store.create_session(problem.id, "S1", "hint")
        client = ScriptedClient(["这个碳上连了什么基团？"])

        text, meta = request_hint(problem, "我卡住了", 1, client=client, store=store, session_id=sid)

        assert text == "这个碳上连了什么基团？"
        assert meta["degraded"] is False
        assert meta["generations"] == 1

        rows = list(store.iter_attempts())
        assert len(rows) == 1
        assert rows[0]["level_requested"] == 1
        assert rows[0]["hint_text"] == text
        assert rows[0]["mode"] == "hint"
        assert rows[0]["leaked"] == 0

    def test_degraded_path_is_recorded(self, problem, store):
        """模型持续泄漏 → 掩码兜底，并把失败落库为 leaked=1。"""
        sid = store.create_session(problem.id, "S1", "hint")
        client = ScriptedClient(["频哪酮", "频哪酮", "频哪酮"])

        text, meta = request_hint(problem, "卡住", 1, client=client, store=store, session_id=sid)

        assert meta["degraded"] is True
        assert "频哪酮" not in text

        row = list(store.iter_attempts())[0]
        assert row["leaked"] == 1
        assert "频哪酮" not in row["hint_text"]

    def test_level4_allows_answer(self, problem, store):
        sid = store.create_session(problem.id, "S1", "hint")
        client = ScriptedClient(["答案是频哪酮"])
        text, meta = request_hint(problem, "卡住", 4, client=client, store=store, session_id=sid)
        assert "频哪酮" in text
        assert meta["degraded"] is False


class TestBaseline:
    def test_logs_level_zero_without_hint(self, problem, store):
        sid = store.create_session(problem.id, "S1", "baseline")
        log_baseline_attempt("我自己做到了第二步", store=store, session_id=sid)

        row = list(store.iter_attempts())[0]
        assert row["level_requested"] == 0
        assert row["hint_text"] is None
        assert row["mode"] == "baseline"
