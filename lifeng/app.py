"""砺锋 v2 —— 学生界面（T5）。

刻意保持最小（计划 §2 非目标）：**不做**登录、账号、权限、题目搜索、
历史回看、美观主题。

结构上把「一次提示请求」的逻辑抽成不依赖 Streamlit 的纯函数
（:func:`request_hint`），使核心交互可离线测试，
Streamlit 层只负责渲染与状态。

运行::

    .venv/bin/streamlit run lifeng/app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from lifeng.hint_engine import (
    C1_MAX_LEVEL,
    VALID_LEVELS,
    DeepSeekClient,
    HintError,
)
from lifeng.leak_check import guard_hint, skeleton_leaks_answer
from lifeng.models import Problem, SkeletonError, load_problems
from lifeng.store import Store, StoreError

PROBLEMS_DIR = Path(os.environ.get("LIFENG_PROBLEMS", "problems"))
DB_PATH = Path(os.environ.get("LIFENG_DB", "store.db"))

#: 最高层级。层 4 给答案与复盘。
MAX_LEVEL: int = max(VALID_LEVELS)

MODE_LABELS = {
    "hint": "实验组（可请求提示）",
    "baseline": "基线组（无提示）",
}


# --------------------------------------------------------------------------
# 可测核心（不依赖 Streamlit）
# --------------------------------------------------------------------------


def next_level(current_level: int) -> int:
    """下一个可请求的层级，封顶在 :data:`MAX_LEVEL`。"""
    if current_level < 0:
        raise ValueError(f"current_level 不能为负：{current_level}")
    return min(current_level + 1, MAX_LEVEL)


def request_hint(
    problem: Problem,
    attempt_text: str,
    level: int,
    *,
    client,
    store: Store,
    session_id: int,
) -> tuple[str, dict]:
    """请求一层提示：生成 → 防泄漏 → 挂到对应的学生尝试上。

    **一次学生尝试 = 一行**，提示是这次尝试「获得的帮助」，不另起一行。
    否则 T9 无法按卡点统计「突破依赖第几层提示」（协议 §6.2 M2）。

    返回 ``(提示文本, 记录信息)``。抽成纯函数是为了让核心交互
    可以在不启动 Streamlit 的情况下验证。
    """
    target = store.latest_attempt_id(session_id)

    if target is None:
        target = store.log_attempt(session_id, attempt_text, level_requested=0)
    elif store.get_attempt(target)["attempt_text"].strip() != attempt_text.strip():
        # 学生改了文本框但直接点了「请求提示」——先把这次尝试记下来，避免丢失
        target = store.log_attempt(session_id, attempt_text, level_requested=0)
    elif store.get_attempt(target)["hint_text"] is not None:
        # 协议 §4：每层提示后必须先提交一次新尝试
        raise HintError(
            "这次尝试已经用过提示了。请先写下新的进展或新的卡点，再请求下一层。"
        )

    result = guard_hint(problem, attempt_text, level, client=client)
    store.attach_hint(target, level, result.text, leaked=result.degraded)

    return result.text, {
        "attempt_id": target,
        "generations": result.attempts,
        "degraded": result.degraded,
        "masked_terms": result.masked_terms,
    }


def log_baseline_attempt(
    attempt_text: str, *, store: Store, session_id: int
) -> int:
    """基线组：记录一次无提示的尝试。"""
    return store.log_attempt(session_id, attempt_text, level_requested=0)


# --------------------------------------------------------------------------
# Streamlit 渲染
# --------------------------------------------------------------------------


def _get_client():
    """惰性构造 LLM 客户端；失败时返回 (None, 错误消息)。"""
    try:
        return DeepSeekClient(), None
    except HintError as exc:
        return None, str(exc)


def _reset_progress() -> None:
    st.session_state.level = 0
    st.session_state.history = []
    st.session_state.session_id = None
    st.session_state.last_attempt = ""


def main() -> None:
    st.set_page_config(page_title="砺锋 · 化学竞赛引导", layout="centered")
    st.title("砺锋 · 化学竞赛引导")
    st.caption("阶段一验证实验 · 骨架约束下的分层提示")

    # ---- 装载题库 -------------------------------------------------------
    try:
        problems = load_problems(PROBLEMS_DIR)
    except SkeletonError as exc:
        st.error(f"题库装载失败：{exc}")
        return

    if not problems:
        st.error(f"{PROBLEMS_DIR}/ 下没有可用的题目文件。")
        return

    hintable = [p for p in problems if p.hintable]
    if not hintable:
        st.error("没有可引导的题目（需要 type=reasoning 且已标注 skeleton）。")
        return

    # ---- 侧栏：学生 / 模式 / 题号 ---------------------------------------
    with st.sidebar:
        st.header("实验设置")
        student_code = st.text_input("学生代号", value="").strip()
        mode = st.radio(
            "模式",
            options=list(MODE_LABELS),
            format_func=lambda m: MODE_LABELS[m],
            horizontal=False,
        )
        problem_id = st.selectbox("题号", [p.id for p in hintable])
        st.divider()
        if st.button("重置本题进度"):
            _reset_progress()
            st.rerun()

    problem = next(p for p in hintable if p.id == problem_id)

    # 切换题目或模式时重置进度
    view_key = f"{problem_id}:{mode}"
    if st.session_state.get("view_key") != view_key:
        _reset_progress()
        st.session_state.view_key = view_key

    if st.session_state.get("session_id") is None:
        st.session_state.session_id = None  # 延后到首次提交时建会话

    # ---- 题目 -----------------------------------------------------------
    st.subheader(f"题目 {problem.id}")
    st.markdown(problem.statement)

    # 标注卫生警告（仅实验者可见的信息，用 expander 收纳）
    with st.expander("实验者信息（学生可忽略）"):
        st.write(
            f"领域：{problem.domain} · 类型：{problem.type} · "
            f"骨架来源：{problem.skeleton_source}"
        )
        if problem.skeleton_source == "ai":
            st.info(
                "本条骨架由 AI 生成。Design Spec 决策 4 验证的是"
                "「人工骨架 + LLM 措辞」—— AI 骨架属于另一个命题，分析时需区分。"
            )
        leaks = skeleton_leaks_answer(problem)
        if leaks:
            st.warning(f"骨架标注含答案关键量，结构性防泄漏已失效：{leaks}")
        else:
            st.success("骨架标注卫生：通过")

    # ---- 学生尝试 -------------------------------------------------------
    attempt_text = st.text_area(
        "写下你目前的尝试，或说明你卡在哪一步",
        value=st.session_state.get("last_attempt", ""),
        height=140,
        key="attempt_input",
    )

    col1, col2 = st.columns([1, 1])

    with col1:
        submit = st.button("提交本次尝试", use_container_width=True)

    with col2:
        can_hint = mode == "hint" and st.session_state.level < MAX_LEVEL
        ask_hint = st.button(
            f"请求第 {next_level(st.session_state.level)} 层提示"
            if can_hint
            else ("已到最后一层" if mode == "hint" else "基线组无提示"),
            disabled=not can_hint,
            use_container_width=True,
        )

    if not student_code:
        st.info("请先在左侧填写学生代号。")
        return

    # ---- 提交 -----------------------------------------------------------
    if submit:
        if not attempt_text.strip():
            st.warning("尝试内容不能为空。")
        else:
            try:
                with Store(DB_PATH) as store:
                    if st.session_state.session_id is None:
                        st.session_state.session_id = store.create_session(
                            problem.id, student_code, mode
                        )
                    if mode == "baseline":
                        log_baseline_attempt(
                            attempt_text, store=store,
                            session_id=st.session_state.session_id,
                        )
                    else:
                        # 实验组的「提交」也记一条 level 0 记录，
                        # 用于观察学生未请求提示时的自然推进
                        store.log_attempt(
                            st.session_state.session_id,
                            attempt_text,
                            level_requested=0,
                        )
                st.session_state.last_attempt = attempt_text
                st.session_state.history.append(("尝试", attempt_text))
                st.success("已记录。")
            except StoreError as exc:
                # StoreError 同时覆盖参数错误与底层存储失败
                # （StorageError 是其子类）。界面层不知道后端是 SQLite 还是别的 ——
                # 这是「先本地、之后再考虑服务器」能低成本迁移的前提。
                st.error(f"记录失败：{exc}")

    # ---- 请求提示 -------------------------------------------------------
    if ask_hint:
        if not attempt_text.strip():
            st.warning("请先写下你的尝试，再请求提示。")
        else:
            client, err = _get_client()
            if client is None:
                st.error(f"无法连接模型：{err}")
            else:
                level = next_level(st.session_state.level)
                try:
                    with Store(DB_PATH) as store:
                        if st.session_state.session_id is None:
                            st.session_state.session_id = store.create_session(
                                problem.id, student_code, mode
                            )
                        text, meta = request_hint(
                            problem, attempt_text, level,
                            client=client, store=store,
                            session_id=st.session_state.session_id,
                        )
                    st.session_state.level = level
                    st.session_state.history.append((f"第 {level} 层提示", text))
                    if meta["degraded"]:
                        st.warning(
                            "本层提示经过掩码处理（模型多次泄露关键结论）。"
                            "该情况已记录，实验者可事后检查。"
                        )
                except HintError as exc:
                    st.error(f"生成失败：{exc}")

    # ---- 历史 -----------------------------------------------------------
    if st.session_state.get("history"):
        st.divider()
        st.subheader("本题会话记录")
        for label, content in st.session_state.history:
            if label.startswith("第"):
                st.info(f"**{label}**\n\n{content}")
            else:
                st.markdown(f"**{label}**：{content}")

        st.caption(
            f"已用层级 {st.session_state.level} / {MAX_LEVEL}"
            f"（层 1~{C1_MAX_LEVEL} 受不泄答案约束）"
        )


if __name__ == "__main__":
    main()
