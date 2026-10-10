"""砺锋 v2 —— 视觉系统。

**为什么要有这个模块**：界面原先是 Streamlit 默认样式 —— 能用，
但没有视觉识别，而且「请先填写学生代号」占着一整屏空白，等于没帮上忙。

## 一个刻意的技术取舍

Streamlit 内部 DOM 的类名**跨版本会变**（它不承诺公开）。所以：

- **视觉组件全部由本模块生成 HTML**（英雄区、指标卡、掌握度条、标签）
  —— 这些完全在我们控制之下，Streamlit 升级不会弄坏
- 对 Streamlit 自身只做**少量稳妥调整**（字体栈、背景、按钮圆角），
  且全部用 ``[data-testid]`` 或元素选择器，不用易变的类名

把所有样式都压在 Streamlit 内部类名上，是「今天好看、升级就崩」的做法。

## 配色

深蓝主色 + 青色点缀。冷色调、低饱和 —— 这是给人长时间盯着做题用的，
不是营销页，颜色不该抢注意力。语义色（稳/中/弱）只用于表达状态。
"""

from __future__ import annotations

import streamlit as st

from lifeng.review import MASTERED_THRESHOLD as _MASTERED_THRESHOLD
from lifeng.review import WEAK_THRESHOLD as _WEAK_THRESHOLD

# ── 设计令牌 ─────────────────────────────────────────────────────────

INK = "#0F172A"
INK_SOFT = "#334155"
MUTED = "#64748B"
LINE = "#E2E8F0"
SURFACE = "#FFFFFF"
BG = "#F6F8FC"

BRAND = "#2456E6"
BRAND_DEEP = "#1739A8"
ACCENT = "#0EA5E9"

GOOD = "#0E9F6E"
WARN = "#D97706"
BAD = "#DC2626"

#: 图表配色（与语义色一致，方便对照）
CHART_PRIMARY = BRAND
CHART_GOOD = GOOD
CHART_BAD = BAD

FONT_STACK = (
    '"Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", '
    '"Microsoft YaHei", "Helvetica Neue", Arial, sans-serif'
)

_CSS = f"""
<style>
  /* ── 全局：字体与底色 ───────────────────────────────────────────── */
  html, body, [class*="css"] {{
      font-family: {FONT_STACK};
  }}
  .stApp {{
      background: {BG};
  }}
  .block-container {{
      padding-top: 2.2rem;
      padding-bottom: 3rem;
      max-width: 1180px;
  }}

  /* ── 侧栏 ───────────────────────────────────────────────────────── */
  section[data-testid="stSidebar"] {{
      background: {SURFACE};
      border-right: 1px solid {LINE};
  }}
  section[data-testid="stSidebar"] .block-container {{
      padding-top: 1.6rem;
  }}

  /* ── 控件：统一圆角与描边 ───────────────────────────────────────── */
  .stButton > button {{
      border-radius: 10px;
      border: 1px solid {LINE};
      font-weight: 500;
      transition: all .15s ease;
  }}
  .stButton > button:hover {{
      border-color: {BRAND};
      color: {BRAND};
  }}
  .stButton > button[kind="primary"] {{
      background: {BRAND};
      border-color: {BRAND};
      color: #fff;
  }}
  .stButton > button[kind="primary"]:hover {{
      background: {BRAND_DEEP};
      border-color: {BRAND_DEEP};
      color: #fff;
  }}
  div[data-testid="stTextInput"] input,
  div[data-testid="stTextArea"] textarea {{
      border-radius: 10px;
      border: 1px solid {LINE};
  }}
  div[data-testid="stTextInput"] input:focus,
  div[data-testid="stTextArea"] textarea:focus {{
      border-color: {BRAND};
      box-shadow: 0 0 0 3px rgba(36,86,230,.12);
  }}

  /* ── 自建组件 ───────────────────────────────────────────────────── */

  .lf-hero {{
      background: linear-gradient(120deg, {BRAND_DEEP} 0%, {BRAND} 58%, {ACCENT} 140%);
      border-radius: 18px;
      padding: 26px 30px;
      color: #fff;
      margin-bottom: 22px;
      box-shadow: 0 10px 26px rgba(23,57,168,.20);
  }}
  .lf-hero-title {{
      font-size: 27px;
      font-weight: 700;
      letter-spacing: .5px;
      line-height: 1.25;
  }}
  .lf-hero-sub {{
      margin-top: 7px;
      font-size: 14px;
      opacity: .90;
  }}
  .lf-hero-tag {{
      display: inline-block;
      margin-top: 14px;
      padding: 3px 11px;
      font-size: 12px;
      border-radius: 999px;
      background: rgba(255,255,255,.18);
      border: 1px solid rgba(255,255,255,.28);
  }}

  .lf-hero.compact {{
      padding: 15px 22px;
      border-radius: 14px;
      margin-bottom: 16px;
      box-shadow: 0 5px 15px rgba(23,57,168,.15);
  }}
  .lf-hero.compact .lf-hero-title {{
      font-size: 19px;
  }}
  .lf-hero.compact .lf-hero-sub {{
      margin-top: 4px;
      font-size: 13px;
  }}
  .lf-hero.compact .lf-hero-tag {{
      margin-top: 9px;
      font-size: 11.5px;
  }}

  .lf-section {{
      display: flex;
      align-items: center;
      gap: 9px;
      margin: 26px 0 12px;
  }}
  .lf-section-bar {{
      width: 4px; height: 17px;
      border-radius: 2px;
      background: {BRAND};
  }}
  .lf-section-text {{
      font-size: 16px; font-weight: 650; color: {INK};
  }}
  .lf-section-note {{
      font-size: 12.5px; color: {MUTED}; font-weight: 400;
  }}

  .lf-stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(158px, 1fr));
      gap: 12px;
      margin-bottom: 6px;
  }}
  .lf-stat {{
      background: {SURFACE};
      border: 1px solid {LINE};
      border-radius: 14px;
      padding: 15px 17px;
      box-shadow: 0 1px 2px rgba(15,23,42,.04);
  }}
  .lf-stat-label {{
      font-size: 12.5px; color: {MUTED}; margin-bottom: 7px;
  }}
  .lf-stat-value {{
      font-size: 25px; font-weight: 700; line-height: 1.1; color: {INK};
  }}
  .lf-stat-value.good {{ color: {GOOD}; }}
  .lf-stat-value.warn {{ color: {WARN}; }}
  .lf-stat-value.bad  {{ color: {BAD}; }}
  .lf-stat-value.brand{{ color: {BRAND}; }}
  .lf-stat-hint {{
      font-size: 12px; color: {MUTED}; margin-top: 5px;
  }}

  .lf-card {{
      background: {SURFACE};
      border: 1px solid {LINE};
      border-left: 4px solid {BRAND};
      border-radius: 14px;
      padding: 16px 19px;
      margin-bottom: 11px;
      box-shadow: 0 1px 2px rgba(15,23,42,.04);
      transition: box-shadow .15s ease, transform .15s ease;
  }}
  .lf-card:hover {{
      box-shadow: 0 6px 18px rgba(15,23,42,.09);
      transform: translateY(-1px);
  }}
  .lf-card.review {{ border-left-color: {WARN}; }}
  .lf-card.new    {{ border-left-color: {BRAND}; }}
  .lf-card.weakest{{ border-left-color: {BAD}; }}

  .lf-card-head {{
      display: flex; align-items: baseline; gap: 10px; margin-bottom: 6px;
  }}
  .lf-card-id {{
      font-size: 12px; font-weight: 700; color: {BRAND};
      letter-spacing: .6px; text-transform: uppercase;
  }}
  .lf-card-title {{
      font-size: 15px; font-weight: 600; color: {INK};
  }}
  .lf-card-reason {{
      font-size: 13px; color: {MUTED}; margin-top: 4px;
  }}

  .lf-chip {{
      display: inline-block;
      padding: 2px 9px;
      border-radius: 999px;
      font-size: 11.5px;
      font-weight: 600;
      border: 1px solid transparent;
  }}
  .lf-chip.good {{ background:#E7F6F0; color:{GOOD}; border-color:#BEE7D8; }}
  .lf-chip.warn {{ background:#FDF3E3; color:{WARN}; border-color:#F6DFB8; }}
  .lf-chip.bad  {{ background:#FDECEC; color:{BAD};  border-color:#F7CFCF; }}
  .lf-chip.mute {{ background:#EEF2F7; color:{MUTED}; border-color:{LINE}; }}
  .lf-chip.brand{{ background:#E8EDFD; color:{BRAND}; border-color:#CBD7FA; }}

  .lf-bar-wrap {{ margin: 7px 0 2px; }}
  .lf-bar-track {{
      height: 8px; border-radius: 999px; background: #EDF1F7; overflow: hidden;
  }}
  .lf-bar-fill {{
      height: 100%; border-radius: 999px; background: {BRAND};
  }}
  .lf-bar-fill.good {{ background: {GOOD}; }}
  .lf-bar-fill.warn {{ background: {WARN}; }}
  .lf-bar-fill.bad  {{ background: {BAD}; }}

  .lf-empty {{
      background: {SURFACE};
      border: 1px dashed #C7D2E4;
      border-radius: 16px;
      padding: 38px 28px;
      text-align: center;
  }}
  .lf-empty-title {{
      font-size: 17px; font-weight: 650; color: {INK}; margin-bottom: 8px;
  }}
  .lf-empty-text {{
      font-size: 13.5px; color: {MUTED}; line-height: 1.7;
  }}

  .lf-quote {{
      border-left: 3px solid {BRAND};
      padding: 9px 0 9px 15px;
      margin: 11px 0;
      color: {INK_SOFT};
      font-size: 14.5px;
      line-height: 1.75;
  }}

  /* Streamlit 原生提示框统一圆角，免得和自建卡片风格打架 */
  div[data-testid="stAlert"] {{ border-radius: 12px; }}
  div[data-testid="stExpander"] {{
      border-radius: 12px;
      border: 1px solid {LINE};
      background: {SURFACE};
  }}
</style>
"""


def inject() -> None:
    """注入全局样式。每个页面渲染前调用一次即可（重复调用无副作用）。"""
    st.markdown(_CSS, unsafe_allow_html=True)


# ── 组件 ─────────────────────────────────────────────────────────────


def _compact(html: str) -> str:
    """压掉 HTML 里的缩进与空行。

    **这不是美化，是必须的。** ``st.markdown`` 会走 CommonMark 解析：
    遇到空行就终止 HTML 块，之后的缩进（≥4 空格）行会被当成**代码块**
    原样显示出来。

    实测症状：指标卡直接显示成 ``<div class="lf-stat">`` 这样的源码。
    所以任何拼出来的 HTML 都必须先过这一道。
    """
    lines = [line.strip() for line in html.splitlines()]
    return "".join(line for line in lines if line)


def hero(title: str, subtitle: str = "", tag: str = "", *, compact: bool = False) -> None:
    """页头。``compact=True`` 用于内页 —— 保留品牌一致性，但不占太多纵向空间。

    落地页用大号，内页用小号：每天都开的工具不该在每一页都浪费半屏。
    """
    tag_html = f'<div class="lf-hero-tag">{tag}</div>' if tag else ""
    cls = "lf-hero compact" if compact else "lf-hero"
    st.markdown(
        _compact(
            f"""
        <div class="{cls}">
          <div class="lf-hero-title">{title}</div>
          {f'<div class="lf-hero-sub">{subtitle}</div>' if subtitle else ''}
          {tag_html}
        </div>
        """
        ),
        unsafe_allow_html=True,
    )


def section(title: str, note: str = "") -> None:
    note_html = f'<span class="lf-section-note">{note}</span>' if note else ""
    st.markdown(
        _compact(
            f"""
        <div class="lf-section">
          <div class="lf-section-bar"></div>
          <div class="lf-section-text">{title}</div>
          {note_html}
        </div>
        """
        ),
        unsafe_allow_html=True,
    )


def stats(items: list[tuple[str, str, str, str]]) -> None:
    """指标卡行。

    Args:
        items: ``(标签, 数值, 色调, 小字提示)`` 四元组列表。
               色调取 ``""`` / ``good`` / ``warn`` / ``bad`` / ``brand``。
    """
    cards = "".join(
        f"""
        <div class="lf-stat">
          <div class="lf-stat-label">{label}</div>
          <div class="lf-stat-value {tone}">{value}</div>
          {f'<div class="lf-stat-hint">{hint}</div>' if hint else ''}
        </div>
        """
        for label, value, tone, hint in items
    )
    st.markdown(_compact(f'<div class="lf-stats">{cards}</div>'), unsafe_allow_html=True)


def chip(text: str, tone: str = "mute") -> str:
    """返回标签 HTML（字符串，供拼进其它组件）。"""
    return f'<span class="lf-chip {tone}">{text}</span>'


def mastery_bar(value: float, *, tone: str = "", scale: float = 0.95) -> str:
    """返回掌握度条 HTML。``scale`` 是满格对应的值（默认为掌握度上限）。"""
    pct = max(0.0, min(1.0, value / scale if scale else 0.0)) * 100
    return (
        f'<div class="lf-bar-wrap"><div class="lf-bar-track">'
        f'<div class="lf-bar-fill {tone}" style="width:{pct:.1f}%"></div>'
        f"</div></div>"
    )


def tone_for(
    mastery: float,
    *,
    good: float = _MASTERED_THRESHOLD,
    warn: float = _WEAK_THRESHOLD,
) -> str:
    """掌握度 → 语义色。阈值与 ``review.mastery_label`` 保持一致。"""
    if mastery >= good:
        return "good"
    if mastery >= warn:
        return "warn"
    return "bad"


def empty_state(title: str, text: str) -> None:
    st.markdown(
        _compact(
            f"""
        <div class="lf-empty">
          <div class="lf-empty-title">{title}</div>
          <div class="lf-empty-text">{text}</div>
        </div>
        """
        ),
        unsafe_allow_html=True,
    )


def altair_theme():
    """返回统一配色的 Altair 主题字典。

    Altair 随 Streamlit 一起安装，不增加依赖，但表现力远好于
    ``st.bar_chart``（可控制圆角、标签、配色、网格）。
    """
    return {
        "config": {
            "view": {"stroke": None, "continuousWidth": 400, "continuousHeight": 240},
            "axis": {
                "labelColor": MUTED,
                "titleColor": MUTED,
                "labelFontSize": 11.5,
                "titleFontSize": 12,
                "domainColor": LINE,
                "tickColor": LINE,
                "gridColor": "#EDF1F7",
                "labelFont": FONT_STACK.split(",")[0].strip('"'),
            },
            "legend": {"labelColor": MUTED, "titleColor": MUTED, "labelFontSize": 11.5},
            "range": {"category": [BRAND, ACCENT, GOOD, WARN, BAD, "#7C3AED", "#0891B2"]},
            "bar": {"cornerRadiusEnd": 4},
        }
    }
