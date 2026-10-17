"""T5 单元测试 —— 学生界面的可测核心。

Streamlit 层无法在无浏览器环境下点击验证，因此把核心交互
（:func:`request_hint` / :func:`next_level` / :func:`finish_problem`）
抽成纯函数单独测试；UI 层另有启动验证（见 README）。
"""

from __future__ import annotations

import pytest

from lifeng.hint_engine import HintError
from lifeng.models import load_problem
from lifeng.review import PENALTY_INTERVAL
from lifeng.store import Store, StoreError
from lifeng.views import MAX_LEVEL, finish_problem, next_level, request_hint


class ScriptedClient:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)

    @property
    def remaining(self) -> int:
        return len(self._responses)

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
        store.log_attempt(sid, "我自己做到了第二步", level_requested=0)

        row = list(store.iter_attempts())[0]
        assert row["level_requested"] == 0
        assert row["hint_text"] is None
        assert row["mode"] == "baseline"


class TestHintAttachment:
    """协议 §7 的记录模型：一次学生尝试 = 一行，提示挂在那一行上。

    这条模型是 T9 计算「突破依赖第几层提示」（M2）的前提 ——
    若提示另起一行，学生文本会被记两次，回填时无法判断该填哪一行。
    """

    def test_hint_does_not_create_extra_row(self, problem, store):
        sid = store.create_session(problem.id, "S1", "hint")
        store.log_attempt(sid, "我的尝试", level_requested=0)

        client = ScriptedClient(["想想这个碳缺电子吗？"])
        request_hint(problem, "我的尝试", 1, client=client, store=store, session_id=sid)

        rows = list(store.iter_attempts())
        assert len(rows) == 1, "提示不应另起一行"
        assert rows[0]["attempt_text"] == "我的尝试"
        assert rows[0]["level_requested"] == 1
        assert rows[0]["hint_text"] == "想想这个碳缺电子吗？"

    def test_second_hint_on_same_attempt_rejected(self, problem, store):
        """协议 §4：每层提示后必须先提交新的尝试。"""
        sid = store.create_session(problem.id, "S1", "hint")
        client = ScriptedClient(["提示一", "提示二"])
        request_hint(problem, "同样的尝试", 1, client=client, store=store, session_id=sid)

        with pytest.raises(HintError):
            request_hint(problem, "同样的尝试", 2, client=client, store=store, session_id=sid)

        assert len(list(store.iter_attempts())) == 1
        # 被拒绝时不应白白消耗一次 LLM 调用
        assert client.remaining == 1

    def test_changed_text_starts_new_attempt(self, problem, store):
        sid = store.create_session(problem.id, "S1", "hint")
        client = ScriptedClient(["提示一", "提示二"])

        request_hint(problem, "第一次尝试", 1, client=client, store=store, session_id=sid)
        request_hint(problem, "第二次尝试", 2, client=client, store=store, session_id=sid)

        rows = list(store.iter_attempts())
        assert len(rows) == 2
        assert [r["attempt_text"] for r in rows] == ["第一次尝试", "第二次尝试"]
        assert [r["level_requested"] for r in rows] == [1, 2]


class TestAttachHintStoreGuard:
    def test_rejects_overwrite(self, store):
        sid = store.create_session("p001", "S1", "hint")
        aid = store.log_attempt(sid, "尝试")
        store.attach_hint(aid, 1, "提示一")
        with pytest.raises(StoreError):
            store.attach_hint(aid, 2, "提示二")

    def test_rejects_level_zero(self, store):
        sid = store.create_session("p001", "S1", "hint")
        aid = store.log_attempt(sid, "尝试")
        with pytest.raises(StoreError):
            store.attach_hint(aid, 0, "提示")

    def test_rejects_empty_hint(self, store):
        sid = store.create_session("p001", "S1", "hint")
        aid = store.log_attempt(sid, "尝试")
        with pytest.raises(StoreError):
            store.attach_hint(aid, 1, "   ")

    def test_rejects_unknown_attempt(self, store):
        with pytest.raises(StoreError):
            store.attach_hint(99999, 1, "提示")


class TestFinishProblem:
    """结题：把「用了第几层提示」翻译成复习计划。

    这是本次集成的关键接口 —— 提示层级 → outcome → SM-2 间隔。
    它决定了「学生下次什么时候再见到这道题」。
    """

    def test_no_hint_gets_longer_interval(self, problem, store):
        result = finish_problem(
            problem, store=store, student_code="S1", max_level_used=0, solved=True
        )
        assert result.interval_days == 3          # outcome 1.0 → 前进一档
        assert result.new_mastery > 0.15

    def test_level3_gets_penalty_interval(self, problem, store):
        """第 3 层才突破 = 提示已相当具体，明天就该重做。

        注意：这里只断言**间隔**，不断言掌握度方向。
        从 0.15 的低起点看，"部分对"其实**高于模型预期**（模型只预测约 0.43），
        所以掌握度会微升 —— 这是模型该有的行为，不是 bug。
        真正体现惩罚的是间隔回到最短档。
        """
        result = finish_problem(
            problem, store=store, student_code="S1", max_level_used=3, solved=True
        )
        assert result.interval_days == PENALTY_INTERVAL

        # 相比无提示解出（outcome 1.0），部分对的掌握度增幅应显著更小
        full = finish_problem(
            problem, store=store, student_code="S2", max_level_used=0, solved=True
        )
        assert result.mastery_delta < full.mastery_delta

    def test_unsolved_resets_to_tomorrow(self, problem, store):
        result = finish_problem(
            problem, store=store, student_code="S1", max_level_used=1, solved=False
        )
        assert result.interval_days == PENALTY_INTERVAL

    def test_repeat_updates_same_row(self, problem, store):
        finish_problem(problem, store=store, student_code="S1", max_level_used=0, solved=True)
        finish_problem(problem, store=store, student_code="S1", max_level_used=0, solved=True)

        rows = store.student_overview("S1")
        assert len(rows) == 1, "同一学生同一题只应有一条复习记录"
        assert rows[0]["review_count"] == 2

    def test_different_students_are_isolated(self, problem, store):
        finish_problem(problem, store=store, student_code="S1", max_level_used=0, solved=True)
        finish_problem(problem, store=store, student_code="S2", max_level_used=3, solved=True)

        assert store.student_overview("S1")[0]["interval_days"] == 3
        assert store.student_overview("S2")[0]["interval_days"] == PENALTY_INTERVAL


class TestTrainingStateKey:
    """训练页状态键必须区分学生。

    回归：早先键是 ``f"{problem_id}:{mode}"``，不含学生代号。
    老师在同一个浏览器里切换学生时，session_id 不会被重置，
    后一个学生的作答被写进前一个学生的会话 —— 数据串号。
    已实测复现（两个学生的作答混在同一行归属下）。
    """

    def test_key_includes_student(self):
        from lifeng.views import training_view_key

        a = training_view_key("甲", "p001", "hint")
        b = training_view_key("乙", "p001", "hint")
        assert a != b, "换学生但键相同 → session 会复用 → 作答串号"

    def test_key_includes_problem_and_mode(self):
        from lifeng.views import training_view_key

        base = training_view_key("甲", "p001", "hint")
        assert base != training_view_key("甲", "p002", "hint")
        assert base != training_view_key("甲", "p001", "baseline")

    def test_key_is_stable_for_same_inputs(self):
        from lifeng.views import training_view_key

        assert training_view_key("甲", "p001", "hint") == training_view_key(
            "甲", "p001", "hint"
        )

    def test_key_does_not_collide_on_separators(self):
        """用冒号拼字符串时，值里含冒号会造成歧义。"""
        from lifeng.views import training_view_key

        assert training_view_key("a:b", "c", "d") != training_view_key("a", "b:c", "d")
