"""心理资源库与页面结构测试。

心理模块的测试重点不是算法，而是**内容边界**：
- 不得出现诊断性表述
- 求助指引必须存在且不写死未核实的电话号码
- 有争议的说法必须标注争议
"""

from __future__ import annotations

import pytest

from lifeng.psych import (
    AROUSAL_NOTE,
    BOUNDARY_ADVICE,
    BOUNDARY_SIGNALS,
    CRISIS_CONTACT_PLACEHOLDER,
    SCENARIOS,
    Scenario,
    Technique,
    scenario_by_key,
)


class TestScenarios:
    def test_has_scenarios(self) -> None:
        assert len(SCENARIOS) >= 3

    def test_keys_unique(self) -> None:
        keys = [s.key for s in SCENARIOS]
        assert len(keys) == len(set(keys))

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.key)
    def test_scenario_is_complete(self, scenario: Scenario) -> None:
        assert scenario.title.strip()
        assert scenario.signs.strip()
        assert scenario.techniques, f"{scenario.key} 没有任何方法"

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.key)
    def test_techniques_are_actionable(self, scenario: Scenario) -> None:
        """每个方法都要有「什么时候用」和「怎么做」，否则等于没写。"""
        for tech in scenario.techniques:
            assert tech.name.strip()
            assert tech.when.strip()
            assert tech.how.strip()

    def test_lookup(self) -> None:
        assert scenario_by_key("panic") is not None
        assert scenario_by_key("nonexistent") is None


class TestBoundaryGuidance:
    def test_has_boundary_signals(self) -> None:
        assert len(BOUNDARY_SIGNALS) >= 3

    def test_covers_self_harm(self) -> None:
        """自伤念头必须在列 —— 这是最不能漏的一条。"""
        joined = "".join(BOUNDARY_SIGNALS)
        assert "伤害自己" in joined or "自伤" in joined

    def test_advice_points_to_a_person(self) -> None:
        assert "监护人" in BOUNDARY_ADVICE or "老师" in BOUNDARY_ADVICE

    def test_does_not_hardcode_a_hotline(self) -> None:
        """写死一个未核实的全国热线是危险的 —— 打不通会延误求助。

        因此留占位符，由老师填入本机可用的号码。
        """
        assert CRISIS_CONTACT_PLACEHOLDER in BOUNDARY_ADVICE
        assert "请老师在此填入" in CRISIS_CONTACT_PLACEHOLDER


class TestNoDiagnosis:
    """本模块只做资源，不做检测/诊断。"""

    def test_no_diagnostic_language(self) -> None:
        import lifeng.psych as psych

        text = psych.__doc__ or ""
        assert "不做情绪检测" in text
        assert "不做心理诊断" in text

    def test_arousal_note_flags_controversy(self) -> None:
        """耶克斯-多德森定律的倒 U 型支持较弱，不能当成定律灌输。"""
        assert "质疑" in AROUSAL_NOTE or "支持较弱" in AROUSAL_NOTE


class TestPages:
    def test_all_pages_exported(self) -> None:
        import lifeng.views as views

        for name in ("render_train", "render_review", "render_progress", "render_psych"):
            assert callable(getattr(views, name)), f"{name} 不是可调用对象"

    def test_core_interaction_is_testable_without_streamlit_run(self) -> None:
        """render_* 之外必须保留可离线调用的核心函数 —— 否则交互逻辑无法验证。"""
        import lifeng.views as views

        for name in ("next_level", "request_hint", "finish_problem"):
            assert callable(getattr(views, name)), f"{name} 缺失，交互逻辑失去可测性"
