"""视觉系统测试。

样式本身没法断言「好不好看」，但**有一类 bug 可以钉死**：

``st.markdown`` 走 CommonMark 解析，遇到空行就终止 HTML 块，
之后的缩进行会被当成**代码块原样显示**。实测症状是页面上直接出现
``<div class="lf-stat">`` 这样的源码 —— 而且只有看截图才能发现。

所以这里断言：所有组件产出的 HTML 都不含换行。
"""

from __future__ import annotations

import inspect

import pytest

from lifeng import theme


class TestCompact:
    def test_removes_newlines_and_blank_lines(self):
        raw = """
        <div class="a">
          <div class="b">x</div>

          <div class="c">y</div>
        </div>
        """
        out = theme._compact(raw)
        assert "\n" not in out
        assert '  ' not in out.split("<div")[0]
        assert out.startswith('<div class="a">')
        assert out.endswith("</div>")

    def test_preserves_content(self):
        out = theme._compact('<p>甲</p>\n\n<p>乙</p>')
        assert "甲" in out and "乙" in out


class TestComponentsEmitSingleLineHtml:
    """这一组就是那个「显示成源码」bug 的回归测试。"""

    def _capture(self, monkeypatch, fn, *args, **kwargs) -> str:
        captured: list[str] = []
        monkeypatch.setattr(
            theme.st, "markdown", lambda html, **kw: captured.append(html)
        )
        fn(*args, **kwargs)
        assert captured, f"{fn.__name__} 没有调用 st.markdown"
        return captured[0]

    def test_hero(self, monkeypatch):
        html = self._capture(monkeypatch, theme.hero, "标题", "副标题", "标签")
        assert "\n" not in html
        assert "lf-hero-title" in html and "标题" in html

    def test_hero_without_tag(self, monkeypatch):
        html = self._capture(monkeypatch, theme.hero, "标题", "副标题")
        assert "lf-hero-tag" not in html

    def test_hero_compact(self, monkeypatch):
        html = self._capture(monkeypatch, theme.hero, "标题", compact=True)
        assert "lf-hero compact" in html

    def test_section(self, monkeypatch):
        html = self._capture(monkeypatch, theme.section, "小节", "注释")
        assert "\n" not in html
        assert "小节" in html and "注释" in html

    def test_stats(self, monkeypatch):
        """指标卡是当初真正出问题的地方 —— 卡片之间有空行。"""
        html = self._capture(
            monkeypatch,
            theme.stats,
            [("甲", "1", "good", "提示"), ("乙", "2", "", "")],
        )
        assert "\n" not in html
        assert html.count("lf-stat-label") == 2
        assert "甲" in html and "乙" in html

    def test_stats_empty(self, monkeypatch):
        html = self._capture(monkeypatch, theme.stats, [])
        assert "\n" not in html

    def test_empty_state(self, monkeypatch):
        html = self._capture(monkeypatch, theme.empty_state, "标题", "正文")
        assert "\n" not in html


class TestChipAndBar:
    def test_chip_single_line(self):
        html = theme.chip("稳", "good")
        assert "\n" not in html and "lf-chip good" in html

    def test_mastery_bar_clamped(self):
        assert "width:100.0%" in theme.mastery_bar(1.5)
        assert "width:0.0%" in theme.mastery_bar(-1.0)

    def test_mastery_bar_scale(self):
        assert "width:50.0%" in theme.mastery_bar(0.475, scale=0.95)

    def test_mastery_bar_zero_scale_is_safe(self):
        """scale 为 0 不能崩 —— 除法保护。"""
        assert "width:0.0%" in theme.mastery_bar(0.5, scale=0)


class TestTone:
    def test_thresholds_match_review_labels(self):
        """语义色必须和 review.mastery_label 用的是同一组阈值。

        两处不一致会出现「标签说稳、颜色是红」这种自相矛盾的界面。
        """
        from lifeng.review import MASTERED_THRESHOLD, WEAK_THRESHOLD

        params = inspect.signature(theme.tone_for).parameters
        assert params["good"].default == MASTERED_THRESHOLD
        assert params["warn"].default == WEAK_THRESHOLD

    @pytest.mark.parametrize(
        "value,expected", [(0.05, "bad"), (0.30, "warn"), (0.60, "good")]
    )
    def test_tone(self, value, expected):
        assert theme.tone_for(value) == expected

    def test_tones_agree_with_labels(self):
        from lifeng.review import mastery_label

        for value in (0.05, 0.2, 0.3, 0.45, 0.7, 0.9):
            label = mastery_label(value)
            tone = theme.tone_for(value)
            mapping = {"生": "bad", "还行": "warn", "稳": "good"}
            assert tone == mapping[label], f"{value} 标签={label} 颜色={tone} 不一致"


class TestPaletteIsSingleSourced:
    def test_config_toml_matches_theme(self):
        """config.toml 与 theme.py 的配色必须一致。

        config.toml 管原生控件（单选、焦点环），theme.py 管自建组件。
        两处不一致时，界面会同时出现两种蓝。
        """
        from pathlib import Path

        config = Path(".streamlit/config.toml")
        assert config.is_file(), "缺少 .streamlit/config.toml"
        text = config.read_text(encoding="utf-8")
        assert theme.BRAND in text, f"config.toml 里没有品牌色 {theme.BRAND}"
        assert theme.INK in text

    def test_launcher_passes_same_palette(self):
        """打包版不会读到仓库里的 config.toml，必须由 launcher 显式传。"""
        from pathlib import Path

        src = Path("lifeng/launcher.py").read_text(encoding="utf-8")
        assert f"--theme.primaryColor={theme.BRAND}" in src
