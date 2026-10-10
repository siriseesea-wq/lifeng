"""砺锋 v2 —— 界面各页面。

分成四个页面，按学生的实际动作组织，而不是按代码模块组织：

| 页面 | 学生来干什么 |
|---|---|
| 训练 | 做题、卡住时取提示 |
| 复习 | 看今天该重做哪些题 |
| 进度 | 看自己哪儿稳、哪儿生 |
| 心理 | 状态不对时找个具体做法 |

**图形化**：图表全部用 Streamlit 内置组件（``st.bar_chart`` /
``st.progress`` / ``st.metric``），**不引入 plotly 等新依赖** ——
打包体积已经很可观了。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

from lifeng.concepts import ConceptError, ConceptGraph
from lifeng.hint_engine import HintError
from lifeng.leak_check import guard_hint, skeleton_leaks_answer
from lifeng.materials import load_materials, retrieve
from lifeng.models import Problem, load_problems
from lifeng.planner import build_daily_plan
from lifeng.profile import build_profile, concept_titles
from lifeng.psych import (
    AROUSAL_NOTE,
    BOUNDARY_ADVICE,
    BOUNDARY_SIGNALS,
    SCENARIOS,
)
from lifeng.review import (
    apply_outcome,
    due_for_student,
    mastery_label,
    outcome_from_levels,
)
from lifeng.store import Store, StoreError

LEVEL_LABELS = {0: "无提示", 1: "第 1 层", 2: "第 2 层", 3: "第 3 层", 4: "直接看答案"}

MAX_LEVEL = 4


# --------------------------------------------------------------------------
# 可测核心（不依赖 Streamlit 的运行环境）
#
# 这些函数故意与渲染分开：分层提示的交互逻辑是本项目最需要被验证的部分，
# 不能因为「放进视图更省事」而失去离线测试能力。
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
    """生成一层提示，并**挂到对应的学生尝试上**。

    一次学生尝试 = 一行；提示是这次尝试获得的帮助，不另起一行。
    否则按卡点统计「突破依赖第几层提示」就算不出来。

    若最近一次尝试已有提示则拒绝 —— 协议 §4 要求每层提示后
    必须先提交新的尝试。
    """
    target = store.latest_attempt_id(session_id)
    if target is None:
        target = store.log_attempt(session_id, attempt_text, level_requested=0)
    elif store.get_attempt(target)["attempt_text"].strip() != attempt_text.strip():
        # 学生改了文本框但直接点了「请求提示」—— 先记下这次尝试，避免丢失
        target = store.log_attempt(session_id, attempt_text, level_requested=0)
    elif store.get_attempt(target)["hint_text"] is not None:
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


def finish_problem(
    problem: Problem,
    *,
    store: Store,
    student_code: str,
    max_level_used: int,
    solved: bool,
):
    """结题：把「用了第几层提示」翻译成 outcome，写入复习计划。"""
    outcome = outcome_from_levels(solved=solved, max_level_used=max_level_used)
    return apply_outcome(
        store,
        student_code=student_code,
        problem_id=problem.id,
        outcome=outcome,
    )


def build_material_query(
    problem: Problem,
    *,
    concepts_path: Path | None = None,
    attempt_text: str = "",
) -> str:
    """构造资料检索的查询串。

    **必须用知识点的中文标题，不能用 id。** id 是英文 slug
    （如 ``electrophilic-aromatic-substitution``），而资料是中文 ——
    把 slug 塞进查询只会灌噪声。这是实测发现的问题：用 id 时，
    芳香亲电取代那道题会把「共轭二烯的加成」排到第一位。
    """
    parts = [problem.statement[:300]]

    titles: list[str] = []
    if concepts_path is not None and problem.concepts:
        try:
            graph = ConceptGraph.load(concepts_path)
            titles = concept_titles(graph, tuple(problem.concepts))
        except ConceptError:
            titles = []
    # 图谱不可用时退回 id（至少不比空查询差），并在调用方不做特殊处理
    parts.append(" ".join(titles) if titles else " ".join(problem.concepts))

    if attempt_text.strip():
        parts.append(attempt_text[:200])
    return " ".join(part for part in parts if part.strip())


# --------------------------------------------------------------------------
# 训练
# --------------------------------------------------------------------------


def render_train(
    problems: list[Problem],
    *,
    student_code: str,
    mode: str,
    problem_id: str,
    db_path: Path,
    client,
) -> None:
    problem = next((p for p in problems if p.id == problem_id), None)
    if problem is None:
        st.error(f"题库里没有 {problem_id}")
        return

    view_key = f"{problem_id}:{mode}"
    if st.session_state.get("view_key") != view_key:
        st.session_state.view_key = view_key
        st.session_state.level = 0
        st.session_state.history = []
        st.session_state.session_id = None
        st.session_state.last_attempt = ""

    st.subheader(f"题目 {problem.id}")
    st.markdown(problem.statement)

    # ---- 计时与进度（图形化）--------------------------------------------
    col_t, col_l, col_h = st.columns(3)
    with Store(db_path) as store:
        elapsed = (
            store.elapsed_seconds(st.session_state.session_id)
            if st.session_state.get("session_id")
            else 0
        )
        attempts = (
            store.attempt_count(st.session_state.session_id)
            if st.session_state.get("session_id")
            else 0
        )
    col_t.metric("用时", f"{(elapsed or 0) // 60} 分 {(elapsed or 0) % 60} 秒")
    col_l.metric("已用提示", LEVEL_LABELS.get(st.session_state.level, "—"))
    col_h.metric("尝试次数", attempts)

    st.progress(
        min(st.session_state.level / 4, 1.0),
        text=f"提示进度 {st.session_state.level} / 4",
    )

    with st.expander("实验者信息（学生可忽略）"):
        st.write(
            f"领域：{problem.domain} · 类型：{problem.type} · "
            f"骨架来源：{problem.skeleton_source}"
        )
        if problem.skeleton_source == "ai":
            st.info(
                "本条骨架由 AI 生成。Design Spec 决策 4 验证的是"
                "「人工骨架 + LLM 措辞」—— AI 骨架属于另一个命题。"
            )
        leaks = skeleton_leaks_answer(problem)
        if leaks:
            st.warning(f"骨架含答案关键量，结构性防泄漏已失效：{leaks}")
        else:
            st.success("骨架卫生：通过")

    # ---- 资料检索（学生主动查）------------------------------------------
    #
    # 刻意不自动展示。学生一卡住就看到讲义，等于跳过了那段费力思考 ——
    # 而那段思考正是这个工具存在的理由。要查得自己按。
    with st.expander("📖 查资料（讲义里的相关段落）"):
        st.caption(
            "这里只给讲义原文，**不直接给答案** —— 怎么用由你判断。"
        )
        if st.button("检索相关段落", key=f"findmat_{problem.id}"):
            materials = load_materials(st.session_state.get("materials_dir", "materials"))
            query = build_material_query(
                problem,
                concepts_path=Path(st.session_state.get("concepts_path", "concepts.yaml")),
                attempt_text=st.session_state.get("last_attempt", ""),
            )
            st.session_state.material_hits = [
                (m.label, m.text, round(score, 2))
                for m, score in retrieve(materials, query, limit=3)
            ]
            st.session_state.material_total = len(materials)

        hits = st.session_state.get("material_hits")
        if hits:
            for label, text, score in hits:
                st.markdown(f"**{label}**　`{score}`")
                st.markdown(text)
                st.divider()
        elif hits is not None:
            total = st.session_state.get("material_total", 0)
            if total == 0:
                st.info(
                    "资料库是空的。把讲义或笔记放成 .md / .txt 放到 `materials/` 目录就会自动索引。"
                )
            else:
                st.info(f"在 {total} 个段落里没找到相关度足够高的内容。")

    # ---- 学生尝试 -------------------------------------------------------
    attempt_text = st.text_area(
        "写下你目前的尝试，或说明你卡在哪一步",
        value=st.session_state.get("last_attempt", ""),
        height=140,
        key="attempt_input",
    )

    c1, c2 = st.columns(2)
    with c1:
        submit = st.button("提交本次尝试", use_container_width=True)
    with c2:
        can_hint = mode == "hint" and st.session_state.level < 4
        ask_hint = st.button(
            f"请求第 {st.session_state.level + 1} 层提示"
            if can_hint
            else ("已到最后一层" if mode == "hint" else "基线组无提示"),
            disabled=not can_hint,
            use_container_width=True,
        )

    if not student_code:
        st.info("请先在左侧填写学生代号。")
        return

    if submit:
        if not attempt_text.strip():
            st.warning("尝试内容不能为空。")
        else:
            try:
                with Store(db_path) as store:
                    if st.session_state.session_id is None:
                        st.session_state.session_id = store.create_session(
                            problem.id, student_code, mode
                        )
                    store.log_attempt(
                        st.session_state.session_id, attempt_text, level_requested=0
                    )
                st.session_state.last_attempt = attempt_text
                st.session_state.history.append(("尝试", attempt_text))
                st.success("已记录。")
                st.rerun()
            except StoreError as exc:
                st.error(f"记录失败：{exc}")

    if ask_hint:
        if not attempt_text.strip():
            st.warning("请先写下你的尝试，再请求提示。")
        elif client is None:
            st.error("未配置模型密钥，无法生成提示。")
        else:
            level = next_level(st.session_state.level)
            try:
                with Store(db_path) as store:
                    if st.session_state.session_id is None:
                        st.session_state.session_id = store.create_session(
                            problem.id, student_code, mode
                        )
                    text, meta = request_hint(
                        problem,
                        attempt_text,
                        level,
                        client=client,
                        store=store,
                        session_id=st.session_state.session_id,
                    )

                st.session_state.level = level
                st.session_state.history.append((f"第 {level} 层提示", text))
                if meta["degraded"]:
                    st.warning("本层提示经过掩码处理（模型多次泄露关键结论），已记录。")
                st.rerun()
            except HintError as exc:
                st.warning(str(exc))
            except StoreError as exc:
                st.error(f"记录失败：{exc}")

    # ---- 历史 -----------------------------------------------------------
    if st.session_state.get("history"):
        st.divider()
        for label, content in st.session_state.history:
            if label.startswith("第"):
                st.info(f"**{label}**\n\n{content}")
            else:
                st.markdown(f"**{label}**：{content}")

    # ---- 完成本题 → 写入复习计划 ----------------------------------------
    st.divider()
    st.caption("做完就点一下，系统据此安排复习时间。")
    d1, d2 = st.columns(2)
    with d1:
        solved = st.button("✅ 我做出来了", use_container_width=True, type="primary")
    with d2:
        gave_up = st.button("🏳️ 卡住了，先放着", use_container_width=True)

    if solved or gave_up:
        max_level = st.session_state.level
        try:
            with Store(db_path) as store:
                result = finish_problem(
                    problem,
                    store=store,
                    student_code=student_code,
                    max_level_used=max_level,
                    solved=bool(solved),
                )
            days = (result.next_review - date.today()).days
            st.success(
                f"已记录（{LEVEL_LABELS.get(max_level, max_level)}）。"
                f"掌握度 {result.new_mastery:.2f}（{mastery_label(result.new_mastery)}），"
                f"建议 **{days} 天后**重做这道题。"
            )
            st.session_state.level = 0
            st.session_state.history = []
            st.session_state.session_id = None
            st.session_state.last_attempt = ""
        except StoreError as exc:
            st.error(f"记录失败：{exc}")


# --------------------------------------------------------------------------
# 今日（系统主导）
# --------------------------------------------------------------------------


def render_today(*, student_code: str, db_path: Path, problems_dir: Path) -> None:
    """今天做什么 —— 由系统决定，不由学生挑。

    对卡住的人来说，"今天做哪道"这个决定本身就是负担，
    结果往往是挑最熟的做、或者干脆不做。这一页把这个决定拿走。
    """
    st.subheader("今天做什么")
    if not student_code:
        st.info("请先在左侧填写学生代号。")
        return

    try:
        problems = load_problems(problems_dir)
    except Exception as exc:  # noqa: BLE001
        st.error(f"题库装载失败：{exc}")
        return

    with Store(db_path) as store:
        plan = build_daily_plan(store, problems, student_code)

    if plan.is_empty:
        st.success("今天没有任务。可以去「训练」页自己挑一道。")
        return

    st.markdown(f"### {plan.headline}")

    c1, c2, c3 = st.columns(3)
    c1.metric("到期复习", plan.review_count)
    c2.metric("新题", plan.new_count)
    c3.metric("补弱", plan.weakest_count)

    by_id = {p.id: p for p in problems}
    for index, item in enumerate(plan.items, start=1):
        problem = by_id.get(item.problem_id)
        with st.container(border=True):
            left, right = st.columns([3, 1])
            with left:
                st.markdown(f"**{index}. {item.problem_id}**")
                st.caption(item.reason)
                if problem is not None:
                    first_line = problem.statement.strip().splitlines()[0]
                    st.markdown(f"{first_line[:60]}…")
            with right:
                if st.button(
                    "开始", key=f"today_{item.problem_id}", use_container_width=True
                ):
                    st.session_state.pending_problem = item.problem_id
                    st.session_state.pending_page = "训练"
                    st.rerun()

    st.caption(
        "顺序是系统排的：先清到期复习（不复习会忘），再做新题，"
        "都够了就回去补最弱的那块。"
    )


# --------------------------------------------------------------------------
# 复习
# --------------------------------------------------------------------------


def render_review(*, student_code: str, db_path: Path) -> None:
    st.subheader("今天该重做哪些题")
    if not student_code:
        st.info("请先在左侧填写学生代号。")
        return

    with Store(db_path) as store:
        try:
            problems = load_problems(st.session_state.get("problems_dir", "problems"))
        except Exception:  # noqa: BLE001
            problems = []
        by_id = {p.id: p for p in problems}
        due = due_for_student(store, student_code)
        overview = store.student_overview(student_code)

    if not overview:
        st.info("还没有做过题。先去「训练」页做一道。")
        return

    # 统计卡
    c1, c2, c3 = st.columns(3)
    c1.metric("在跟踪的题", len(overview))
    c2.metric("今天到期", len(due))
    avg = sum(float(r["mastery"]) for r in overview) / len(overview)
    c3.metric("平均掌握度", f"{avg:.2f}", mastery_label(avg))

    # 未来 14 天的到期分布（图形化）
    st.markdown("**接下来 14 天的复习量**")
    today = date.today()
    buckets = {today + timedelta(days=i): 0 for i in range(14)}
    overdue = 0
    for row in overview:
        try:
            d = date.fromisoformat(str(row["next_review"]))
        except ValueError:
            continue
        if d <= today:
            overdue += 1
        elif d in buckets:
            buckets[d] += 1

    import pandas as pd

    chart = pd.DataFrame(
        {"到期题数": list(buckets.values())},
        index=[d.strftime("%m-%d") for d in buckets],
    )
    st.bar_chart(chart, height=200)
    if overdue:
        st.caption(f"另有 **{overdue}** 道已逾期未复习。")

    # 到期清单
    st.markdown("**到期清单**（最弱的排前面）")
    if not due:
        st.success("今天没有到期项。")
        return

    for row in due:
        pid = str(row["problem_id"])
        problem = by_id.get(pid)
        mastery = float(row["mastery"])
        with st.container(border=True):
            left, right = st.columns([3, 1])
            with left:
                title = problem.statement.strip().splitlines()[0] if problem else pid
                st.markdown(f"**{pid}** · {mastery_label(mastery)}　{title[:40]}")
                st.progress(min(mastery / 0.95, 1.0), text=f"掌握度 {mastery:.2f}")
            with right:
                if st.button("开始重做", key=f"redo_{pid}", use_container_width=True):
                    # 不直接改 st.session_state.nav / problem_id ——
                    # 那两个 widget 已经实例化，改了会报错。
                    # 交给 app.py 在下一轮开头处理。
                    st.session_state.pending_problem = pid
                    st.session_state.pending_page = "训练"
                    st.rerun()


# --------------------------------------------------------------------------
# 进度
# --------------------------------------------------------------------------


def render_progress(*, student_code: str, db_path: Path) -> None:
    st.subheader("我哪儿稳、哪儿生")
    if not student_code:
        st.info("请先在左侧填写学生代号。")
        return

    with Store(db_path) as store:
        overview = store.student_overview(student_code)
        attempts = [a for a in store.iter_attempts() if a["student_code"] == student_code]

    if not overview and not attempts:
        st.info("还没有数据。先去「训练」页做一道题。")
        return

    # 顶部指标
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("已练题数", len({a["problem_id"] for a in attempts}))
    c2.metric("累计尝试", len(attempts))
    hinted = sum(1 for a in attempts if a["hint_text"])
    c3.metric("用了提示的次数", hinted)
    if overview:
        avg = sum(float(r["mastery"]) for r in overview) / len(overview)
        c4.metric("平均掌握度", f"{avg:.2f}", mastery_label(avg))
    else:
        c4.metric("平均掌握度", "—")

    if not overview:
        st.info("还没有完成记录。做完整题后点「我做出来了」才会进入掌握度统计。")
        return

    # 掌握度条形图
    st.markdown("**各题掌握度**")
    import pandas as pd

    rows = sorted(overview, key=lambda r: float(r["mastery"]))
    frame = pd.DataFrame(
        {"掌握度": [float(r["mastery"]) for r in rows]},
        index=[str(r["problem_id"]) for r in rows],
    )
    st.bar_chart(frame, height=max(220, 28 * len(rows)))

    # 明细表
    st.markdown("**明细**")
    table = [
        {
            "题号": str(r["problem_id"]),
            "状态": mastery_label(float(r["mastery"])),
            "掌握度": round(float(r["mastery"]), 3),
            "间隔(天)": int(r["interval_days"]),
            "下次复习": str(r["next_review"]),
            "复习次数": int(r["review_count"]),
        }
        for r in rows
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)

    # 提示使用情况 —— 这是"引导有没有用"的直接信号
    if attempts:
        st.markdown("**提示使用分布**")
        counts = {f"第 {i} 层": 0 for i in range(1, 5)}
        for a in attempts:
            lvl = int(a["level_requested"])
            if lvl >= 1:
                counts[f"第 {lvl} 层"] = counts.get(f"第 {lvl} 层", 0) + 1
        st.bar_chart(pd.DataFrame({"次数": list(counts.values())}, index=list(counts)), height=180)
        st.caption(
            "提示用在哪一层，比「做没做对」更能说明问题："
            "第 1~2 层就能推进，说明方向性提问真的起作用。"
        )


# --------------------------------------------------------------------------
# 心理资源
# --------------------------------------------------------------------------


def render_psych() -> None:
    st.subheader("状态不对时，先看这里")
    st.caption(
        "这里只提供自助方法和求助指引，**不做情绪检测、不做心理判断**。"
        "由你自己判断现在属于哪种情况。"
    )

    for scenario in SCENARIOS:
        with st.expander(scenario.title, expanded=False):
            st.markdown(f"*{scenario.signs}*")
            for tech in scenario.techniques:
                st.markdown(f"**{tech.name}**")
                st.markdown(f"- 什么时候用：{tech.when}")
                st.markdown(f"- 怎么做：{tech.how}")
                if tech.note:
                    st.caption(tech.note)

    st.divider()
    st.markdown("### 什么时候该找成年人")
    st.error(
        "下面任何一条成立，**请找成年人** —— 监护人、学校心理老师，"
        "或当地心理援助热线。"
    )
    for signal in BOUNDARY_SIGNALS:
        st.markdown(f"- {signal}")
    st.warning(BOUNDARY_ADVICE)

    st.divider()
    with st.expander("关于「适度紧张最好」这个说法"):
        st.markdown(AROUSAL_NOTE)


# --------------------------------------------------------------------------
# 图谱
# --------------------------------------------------------------------------


def _load_graph(concepts_path: Path) -> ConceptGraph | None:
    try:
        graph = ConceptGraph.load(concepts_path)
    except ConceptError as exc:
        st.error(f"知识点图装载失败：{exc}")
        return None
    if graph.errors:
        for problem in graph.errors:
            st.error(f"知识点图有问题：{problem}")
        return None
    return graph


def render_graph(
    *,
    student_code: str,
    db_path: Path,
    concepts_path: Path,
    problems_dir: Path,
) -> None:
    """知识点图谱可视化。

    用 Streamlit 内置的 ``st.graphviz_chart``（接受 DOT 字符串）——
    **不需要 graphviz 包，也不需要 plotly**，打包体积不涨。
    """
    st.subheader("知识点图谱")
    graph = _load_graph(concepts_path)
    if graph is None:
        return

    try:
        problems = load_problems(problems_dir)
    except Exception as exc:  # noqa: BLE001
        st.error(f"题库装载失败：{exc}")
        return

    known: set[str] = set()
    weak: set[str] = set()
    if student_code:
        with Store(db_path) as store:
            profile = build_profile(store, problems, graph, student_code)
        known = set(profile.mastered)
        weak = set(profile.weak)
        st.caption(f"{student_code}：{profile.headline}")

    st.graphviz_chart(graph.to_dot(emphasise=known, missing=weak), use_container_width=True)
    st.caption("实线箭头 = 前置依赖；虚线 = 相关概念。绿色已稳、红色薄弱、灰色未触及。")

    c1, c2, c3 = st.columns(3)
    c1.metric("知识点总数", len(graph))
    c2.metric("已稳", len(known))
    c3.metric("薄弱", len(weak))

    # 题库覆盖缺口 —— 图谱比题库大的时候，这里直接告诉你该补什么题
    covered = {c for p in problems for c in p.concepts}
    uncovered = sorted(set(graph.concepts) - covered)
    if uncovered:
        with st.expander(f"图谱里有 {len(uncovered)} 个知识点还没有对应题目"):
            for cid in uncovered:
                concept = graph.get(cid)
                st.markdown(f"- **{concept.title if concept else cid}**")
            st.caption("这是题库的空白，不是学生的空白。")

    untagged = [p.id for p in problems if not p.concepts]
    if untagged:
        with st.expander(f"有 {len(untagged)} 道题还没打知识点标签"):
            st.write(", ".join(untagged))


# --------------------------------------------------------------------------
# 画像（L3 综合判断）
# --------------------------------------------------------------------------


def render_profile(
    *,
    student_code: str,
    db_path: Path,
    concepts_path: Path,
    problems_dir: Path,
) -> None:
    """学生画像与卡点诊断。

    这是三层记忆里的 **L3**：L1 是当前会话（session_state）、
    L2 是跨会话事实（store.db），L3 是从 L2 推导出的综合判断 —— 就是这一页。

    它回答的是「我到底缺什么、下一步动哪一块」，而不是再列一遍数据。
    """
    st.subheader("学生画像")
    if not student_code:
        st.info("请先在左侧填写学生代号。")
        return

    graph = _load_graph(concepts_path)
    if graph is None:
        return
    try:
        problems = load_problems(problems_dir)
    except Exception as exc:  # noqa: BLE001
        st.error(f"题库装载失败：{exc}")
        return

    with Store(db_path) as store:
        profile = build_profile(store, problems, graph, student_code)

    if profile.is_empty:
        st.info("还没有可用的数据。先去「今日」或「训练」页做几道题。")
        return

    st.info(profile.headline)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("已稳知识点", len(profile.mastered))
    c2.metric("薄弱知识点", len(profile.weak))
    c3.metric("未触及", len(profile.untouched))
    c4.metric("提示依赖", f"{profile.hint_dependency:.1f} 层")

    # ── 概念层面的掌握度（图形化）───────────────────────────────────
    st.markdown("**各知识点掌握度**")
    import pandas as pd

    rows = sorted(profile.standings, key=lambda s: s.mastery)
    frame = pd.DataFrame(
        {"掌握度": [s.mastery for s in rows]},
        index=[s.title for s in rows],
    )
    st.bar_chart(frame, height=max(200, 26 * len(rows)))

    st.markdown("**明细**")
    st.dataframe(
        [
            {
                "知识点": s.title,
                "状态": s.label,
                "掌握度": round(s.mastery, 3),
                "涉及题号": ", ".join(s.problem_ids),
            }
            for s in rows
        ],
        use_container_width=True,
        hide_index=True,
    )

    # ── 下一步：前置已满足的可学项 ──────────────────────────────────
    st.markdown("**下一步可以学什么**")
    if profile.next_concepts:
        st.write("、".join(concept_titles(graph, profile.next_concepts[:8])))
        if profile.missing_prerequisites:
            st.caption(
                "完整的前置缺口（含暂时学不了的）共 "
                f"{len(profile.missing_prerequisites)} 个。"
            )
    else:
        st.success("前置都已满足，没有待补的项。")

    # ── 卡点诊断（错误模式）─────────────────────────────────────────
    st.divider()
    st.markdown("**卡点诊断**")
    if not profile.stall_patterns:
        st.caption(
            "还没有卡点记录。在训练页点「卡住了，先放着」时如果标了卡点，"
            "这里会显示你**总卡在哪一类地方**。"
        )
    else:
        st.bar_chart(
            pd.DataFrame(
                {"次数": [count for _, count in profile.stall_patterns]},
                index=[f"卡点 {i + 1}" for i, _ in profile.stall_patterns],
            ),
            height=180,
        )
        st.caption(
            "比「做对没做对」更有用的信号：如果总是卡在第 1 类，"
            "说明问题在**方向**；总卡在最后一类，说明在**收尾**。"
        )
