"""砺锋 v2 —— 界面入口（多页外壳）。

四个页面在 :mod:`lifeng.views` 里；本模块只做导航与状态管理。

运行::

    .venv/bin/streamlit run lifeng/app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from lifeng import theme
from lifeng.hint_engine import DeepSeekClient, HintError
from lifeng.models import SkeletonError, load_problems
from lifeng.views import (
    render_graph,
    render_profile,
    render_progress,
    render_psych,
    render_review,
    render_today,
    render_train,
)

PROBLEMS_DIR = Path(os.environ.get("LIFENG_PROBLEMS", "problems"))
DB_PATH = Path(os.environ.get("LIFENG_DB", "store.db"))
CONCEPTS_PATH = Path(os.environ.get("LIFENG_CONCEPTS", "concepts.yaml"))
MATERIALS_DIR = Path(os.environ.get("LIFENG_MATERIALS", "materials"))

# 今日排在第一位：系统主导，学生不必先做「今天做什么」这个决定。
PAGES = ("今日", "训练", "复习", "进度", "图谱", "画像", "心理")
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
    st.session_state.setdefault("materials_dir", str(MATERIALS_DIR))
    st.session_state.setdefault("concepts_path", str(CONCEPTS_PATH))
    theme.inject()
    _apply_pending_navigation()

    with st.sidebar:
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:18px">
              <div style="width:34px;height:34px;border-radius:9px;
                          background:linear-gradient(135deg,{theme.BRAND_DEEP},{theme.ACCENT});
                          display:flex;align-items:center;justify-content:center;
                          color:#fff;font-weight:700;font-size:17px">砺</div>
              <div>
                <div style="font-weight:700;font-size:16px;color:{theme.INK}">砺锋</div>
                <div style="font-size:11.5px;color:{theme.MUTED}">化学竞赛引导</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
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

    if page == "今日":
        render_today(
            student_code=student_code, db_path=DB_PATH, problems_dir=PROBLEMS_DIR
        )
    elif page == "训练":
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
    elif page == "图谱":
        render_graph(
            student_code=student_code,
            db_path=DB_PATH,
            concepts_path=CONCEPTS_PATH,
            problems_dir=PROBLEMS_DIR,
        )
    elif page == "画像":
        render_profile(
            student_code=student_code,
            db_path=DB_PATH,
            concepts_path=CONCEPTS_PATH,
            problems_dir=PROBLEMS_DIR,
        )
    else:
        render_psych()


if __name__ == "__main__":
    main()
