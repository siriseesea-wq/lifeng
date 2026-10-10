"""砺锋 v2 —— 界面入口（多页外壳）。

四个页面在 :mod:`lifeng.views` 里；本模块只做导航与状态管理。

运行::

    .venv/bin/streamlit run lifeng/app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from lifeng.hint_engine import DeepSeekClient, HintError
from lifeng.models import SkeletonError, load_problems
from lifeng.views import render_progress, render_psych, render_review, render_train

PROBLEMS_DIR = Path(os.environ.get("LIFENG_PROBLEMS", "problems"))
DB_PATH = Path(os.environ.get("LIFENG_DB", "store.db"))

PAGES = ("训练", "复习", "进度", "心理")
MODE_LABELS = {"hint": "实验组（可请求提示）", "baseline": "基线组（无提示）"}


@st.cache_resource
def _client():
    """模型客户端只建一次。返回 (client, 错误消息)。"""
    try:
        return DeepSeekClient(), None
    except HintError as exc:
        return None, str(exc)


def _apply_pending_navigation() -> None:
    """处理来自其他页面的跳转请求。

    必须在对应的 widget 被创建**之前**执行 —— Streamlit 不允许在
    widget 实例化之后再改它的 session_state。
    """
    if "pending_page" in st.session_state:
        st.session_state.nav = st.session_state.pop("pending_page")
    if "pending_problem" in st.session_state:
        st.session_state.problem_id = st.session_state.pop("pending_problem")


def main() -> None:
    st.set_page_config(page_title="砺锋 · 化学竞赛引导", layout="wide")

    try:
        problems = load_problems(PROBLEMS_DIR)
    except SkeletonError as exc:
        st.error(f"题库装载失败：{exc}")
        return

    hintable = [p for p in problems if p.hintable]
    if not hintable:
        st.error("没有可引导的题目（需要 type=reasoning 且已标注 skeleton）。")
        return

    st.session_state.setdefault("problems_dir", str(PROBLEMS_DIR))
    _apply_pending_navigation()

    st.title("砺锋 · 化学竞赛引导")
    st.caption("苏格拉底式分层引导 —— 卡住时给方向，不直接给答案")

    with st.sidebar:
        st.header("设置")
        student_code = st.text_input("学生代号", value="").strip()
        st.radio("页面", PAGES, key="nav", horizontal=False)
        st.divider()

        page = st.session_state.get("nav", PAGES[0])

        mode = "hint"
        problem_id = hintable[0].id
        if page == "训练":
            mode = st.radio(
                "模式",
                options=list(MODE_LABELS),
                format_func=lambda m: MODE_LABELS[m],
            )
            ids = [p.id for p in hintable]
            default = st.session_state.get("problem_id")
            index = ids.index(default) if default in ids else 0
            problem_id = st.selectbox("题号", ids, index=index, key="problem_id")
            st.divider()
            client, err = _client()
            if client is None:
                st.warning("未配置模型密钥，提示功能不可用（其余功能正常）")
                with st.expander("如何配置"):
                    st.code("export DEEPSEEK_API_KEY=sk-...", language="bash")
                    st.caption(err or "")
        else:
            client = None
        st.caption(f"题库：{PROBLEMS_DIR}　共 {len(hintable)} 题")

    if page == "训练":
        render_train(
            hintable,
            student_code=student_code,
            mode=mode,
            problem_id=problem_id,
            db_path=DB_PATH,
            client=client,
        )
    elif page == "复习":
        render_review(student_code=student_code, db_path=DB_PATH)
    elif page == "进度":
        render_progress(student_code=student_code, db_path=DB_PATH)
    else:
        render_psych()


if __name__ == "__main__":
    main()
