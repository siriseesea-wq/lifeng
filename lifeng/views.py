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

import altair as alt
import streamlit as st

from lifeng import prompts, theme
from lifeng.hint_engine import DeepSeekClient
from lifeng.concepts import ConceptError, ConceptGraph
from lifeng.generator import GenerationError, generate_batch
from lifeng.models import MIN_PITFALLS, VALID_DOMAINS
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


def training_view_key(student_code: str, problem_id: str, mode: str) -> str:
    """训练页的状态键。**必须包含学生代号。**

    少了学生代号会出真事故：老师在同一个浏览器里把「学生代号」从 A 换成 B，
    而题目与模式没变 → 键不变 → 不重置 session_id →
    B 的作答被写进 A 的会话，日志里还标成 student=A。
    已实测复现过。

    这个函数单独存在，就是为了能被测试直接钉住 ——
    它藏在 render_train 里时，只能靠人肉点界面才会发现错。

    用 ASCII 的**单元分隔符**而不是冒号：学生代号是自由文本，
    写成 ``a:b`` 时，用冒号拼接会让两组不同的输入得到同一个键。
    """
    return "\x1f".join((student_code, problem_id, mode))


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

    view_key = training_view_key(student_code, problem_id, mode)
    if st.session_state.get("view_key") != view_key:
        st.session_state.view_key = view_key
        st.session_state.level = 0
        st.session_state.history = []
        st.session_state.session_id = None
        st.session_state.last_attempt = ""

    theme.hero(
        f"训练 · {problem.id}",
        "卡住时按层取提示 —— 第 1~3 层不会给答案",
        compact=True,
    )
    theme.section("题干", f"{problem.domain} · reasoning")
    st.markdown(f'<div class="lf-quote">{problem.statement}</div>', unsafe_allow_html=True)

    # ---- 计时与进度（图形化）--------------------------------------------
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
    level = st.session_state.level
    theme.stats(
        [
            ("用时", f"{(elapsed or 0) // 60}:{(elapsed or 0) % 60:02d}", "", "分:秒"),
            ("已用提示", LEVEL_LABELS.get(level, "—"), "warn" if level else "good", f"{level} / 4 层"),
            ("尝试次数", str(attempts), "brand", "本会话"),
        ]
    )
    st.markdown(theme.mastery_bar(level, tone="warn", scale=4.0), unsafe_allow_html=True)

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
    with st.expander("查资料 —— 讲义里的相关段落"):
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
        submit = st.button("提交本次尝试", width="stretch")
    with c2:
        can_hint = mode == "hint" and st.session_state.level < 4
        ask_hint = st.button(
            f"请求第 {st.session_state.level + 1} 层提示"
            if can_hint
            else ("已到最后一层" if mode == "hint" else "基线组无提示"),
            disabled=not can_hint,
            width="stretch",
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
                    # 必须给加载提示。模型要等 5~15 秒，没有提示时
                    # 学生的感受是「点了没反应」，会反复点。
                    with st.spinner(f"正在想第 {level} 层提示…"):
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
        solved = st.button("我做出来了", width="stretch", type="primary")
    with d2:
        gave_up = st.button("卡住了，先放着", width="stretch")

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
    if not student_code:
        theme.hero(
            "砺锋",
            "化学竞赛 · 苏格拉底式分层引导 —— 卡住时给方向，不直接给答案",
            "本地运行 · 数据不出本机",
        )
        theme.empty_state(
            "先在左边填一个学生代号",
            "随便起一个，比如名字缩写或学号。<br>"
            "代号只用来区分不同学生的记录，不会上传到任何地方。",
        )
        return

    try:
        problems = load_problems(problems_dir)
    except Exception as exc:  # noqa: BLE001
        st.error(f"题库装载失败：{exc}")
        return

    with Store(db_path) as store:
        plan = build_daily_plan(store, problems, student_code)

    theme.hero(
        f"今天，{student_code}",
        plan.headline,
        f"{len(plan.items)} 项任务",
    )

    if plan.is_empty:
        theme.empty_state(
            "今天没有任务",
            "可能是题库空了，或者这些题都还没标骨架。<br>"
            "可以去「出题」页生成几道，或者去「训练」页自己挑一道。",
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("去出题", width="stretch"):
                st.session_state.pending_page = "出题"
                st.rerun()
        with c2:
            if st.button("去训练", width="stretch"):
                st.session_state.pending_page = "训练"
                st.rerun()
        return

    theme.stats(
        [
            ("到期复习", str(plan.review_count), "warn" if plan.review_count else "", "不复习会忘"),
            ("新题", str(plan.new_count), "brand" if plan.new_count else "", "往前推进度"),
            ("补弱", str(plan.weakest_count), "bad" if plan.weakest_count else "", "回头补最弱的"),
        ]
    )

    tone_map = {"review": "warn", "new": "brand", "weakest": "bad"}
    kind_map = {"review": "复习", "new": "新题", "weakest": "补弱"}

    theme.section("任务清单", "顺序由系统排定")
    by_id = {p.id: p for p in problems}
    for index, item in enumerate(plan.items, start=1):
        problem = by_id.get(item.problem_id)
        first_line = problem.statement.strip().splitlines()[0] if problem else ""
        tone = tone_map.get(item.kind, "mute")
        card_col, btn_col = st.columns([6, 1], vertical_alignment="center")
        with card_col:
            st.markdown(
                theme._compact(
                    f"""
                <div class="lf-card {item.kind}">
                  <div class="lf-card-head">
                    <span class="lf-card-id">{item.problem_id}</span>
                    {theme.chip(kind_map.get(item.kind, item.kind), tone)}
                  </div>
                  <div class="lf-card-title">{index}. {first_line[:52]}{'…' if len(first_line) > 52 else ''}</div>
                  <div class="lf-card-reason">{item.reason}</div>
                </div>
                """
                ),
                unsafe_allow_html=True,
            )
        with btn_col:
            if st.button("开始", key=f"today_{item.problem_id}", width="stretch"):
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
    theme.hero("复习", "今天该重做哪些题 —— 由间隔算法排定", compact=True)
    if not student_code:
        theme.empty_state("先填学生代号", "在左边填一个，才能看到你的复习安排。")
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
        theme.empty_state("还没有记录", "先去「今日」或「训练」页做一道题。")
        return

    avg = sum(float(r["mastery"]) for r in overview) / len(overview)
    theme.stats(
        [
            ("在跟踪的题", str(len(overview)), "brand", "已进入复习计划"),
            ("今天到期", str(len(due)), "warn" if due else "good", "该重做的"),
            ("平均掌握度", f"{avg:.2f}", theme.tone_for(avg), mastery_label(avg)),
        ]
    )

    theme.section("接下来 14 天的复习量")
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
        {
            "日期": [d.strftime("%m-%d") for d in buckets],
            "到期题数": list(buckets.values()),
        }
    )
    base = alt.Chart(chart).encode(
        x=alt.X("日期:N", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("到期题数:Q", axis=alt.Axis(tickMinStep=1)),
        tooltip=["日期", "到期题数"],
    )
    st.altair_chart(
        (base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=theme.BRAND, size=34))
        .properties(height=190)
        .configure(**theme.altair_theme()["config"]),
        width="stretch",
    )
    if overdue:
        st.caption(f"另有 **{overdue}** 道已逾期未复习。")

    theme.section("到期清单", "最弱的排前面")
    if not due:
        st.success("今天没有到期项，去「今日」页看看安排。")
        return

    for row in due:
        pid = str(row["problem_id"])
        problem = by_id.get(pid)
        mastery = float(row["mastery"])
        title = problem.statement.strip().splitlines()[0] if problem else pid
        card_col, redo_col = st.columns([6, 1], vertical_alignment="center")
        with card_col:
            st.markdown(
                theme._compact(
                    f"""
                <div class="lf-card review">
                  <div class="lf-card-head">
                    <span class="lf-card-id">{pid}</span>
                    {theme.chip(mastery_label(mastery), theme.tone_for(mastery))}
                    <span class="lf-card-reason" style="margin:0">掌握度 {mastery:.2f}</span>
                  </div>
                  <div class="lf-card-title">{title[:56]}</div>
                  {theme.mastery_bar(mastery, tone=theme.tone_for(mastery))}
                </div>
                """
                ),
                unsafe_allow_html=True,
            )
        with redo_col:
            if st.button("重做", key=f"redo_{pid}", width="stretch"):
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
    theme.hero("进度", "我哪儿稳、哪儿生", compact=True)
    if not student_code:
        theme.empty_state("先填学生代号", "在左边填一个，才能看到你的进度。")
        return

    with Store(db_path) as store:
        overview = store.student_overview(student_code)
        attempts = [a for a in store.iter_attempts() if a["student_code"] == student_code]

    if not overview and not attempts:
        theme.empty_state("还没有数据", "先去「今日」或「训练」页做一道题。")
        return

    import pandas as pd

    hinted = sum(1 for a in attempts if a["hint_text"])
    avg = sum(float(r["mastery"]) for r in overview) / len(overview) if overview else 0.0
    # 「已练题数」按**不同题目**计数。attempt 的 problem_id 来自它所属的 session，
    # 而 session 是每题一个 —— 所以这个数就是真正做过的题数。
    practiced = len({a["problem_id"] for a in attempts})
    theme.stats(
        [
            ("已练题数", str(practiced), "brand", "不同题目"),
            ("累计尝试", str(len(attempts)), "", "含重做"),
            ("用了提示", str(hinted), "warn" if hinted else "good", "次数"),
            (
                "平均掌握度",
                f"{avg:.2f}" if overview else "—",
                theme.tone_for(avg) if overview else "",
                mastery_label(avg) if overview else "还没有完成记录",
            ),
        ]
    )

    if not overview:
        st.info("做完整题后点「我做出来了」，才会进入掌握度统计。")
        return

    rows = sorted(overview, key=lambda r: float(r["mastery"]))

    theme.section("各题掌握度", "从弱到强")
    frame = pd.DataFrame(
        {
            "题号": [str(r["problem_id"]) for r in rows],
            "掌握度": [float(r["mastery"]) for r in rows],
        }
    )
    # 颜色用**离散分档**，不用连续映射：连续映射会让同一根柱子左红右橙，
    # 看着像两根。分档也更容易一眼读懂「稳 / 还行 / 生」。
    frame["状态"] = [mastery_label(v) for v in frame["掌握度"]]
    bars = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, size=17)
        .encode(
            y=alt.Y("题号:N", sort="-x", axis=alt.Axis(title=None)),
            x=alt.X(
                "掌握度:Q",
                scale=alt.Scale(domain=[0, 0.6]),
                axis=alt.Axis(title=None, format=".0%", tickCount=4),
            ),
            color=alt.Color(
                "状态:N",
                scale=alt.Scale(
                    domain=["生", "还行", "稳"],
                    range=[theme.BAD, theme.WARN, theme.GOOD],
                ),
                legend=alt.Legend(orient="bottom", title=None),
            ),
            tooltip=["题号", alt.Tooltip("掌握度:Q", format=".3f"), "状态"],
        )
        .properties(height=max(170, 30 * len(rows)))
        .configure(**theme.altair_theme()["config"])
    )
    st.altair_chart(bars, width="stretch")

    theme.section("明细")
    st.dataframe(
        [
            {
                "题号": str(r["problem_id"]),
                "状态": mastery_label(float(r["mastery"])),
                "掌握度": round(float(r["mastery"]), 3),
                "间隔(天)": int(r["interval_days"]),
                "下次复习": str(r["next_review"]),
                "复习次数": int(r["review_count"]),
            }
            for r in rows
        ],
        width="stretch",
        hide_index=True,
    )

    # 提示使用情况 —— 这是"引导有没有用"的直接信号
    if attempts:
        theme.section("提示使用分布", "比「做没做对」更能说明问题")
        counts = {f"第 {i} 层": 0 for i in range(1, 5)}
        for a in attempts:
            lvl = int(a["level_requested"])
            if lvl >= 1:
                counts[f"第 {lvl} 层"] = counts.get(f"第 {lvl} 层", 0) + 1
        hint_frame = pd.DataFrame(
            {"层级": list(counts), "次数": list(counts.values())}
        )
        hint_chart = (
            alt.Chart(hint_frame)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=theme.ACCENT, size=48)
            .encode(
                x=alt.X("层级:N", axis=alt.Axis(labelAngle=0, title=None)),
                y=alt.Y("次数:Q", axis=alt.Axis(tickMinStep=1, title=None)),
                tooltip=["层级", "次数"],
            )
        )
        st.altair_chart(
            hint_chart.properties(height=170).configure(**theme.altair_theme()["config"]),
            width="stretch",
        )
        st.caption(
            "第 1~2 层就能推进，说明方向性提问真的起作用；"
            "如果都堆在第 4 层，说明提示给得太晚了。"
        )


# --------------------------------------------------------------------------
# 心理资源
# --------------------------------------------------------------------------


def render_psych() -> None:
    theme.hero("心理资源", "状态不对时，先看这里 —— 由你自己判断属于哪种", compact=True)
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

    theme.section("什么时候该找成年人", "这几条不属于自助能处理的范围")
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
    theme.hero(
        "知识点图谱",
        "实线 = 前置依赖，虚线 = 相关概念",
        compact=True,
    )
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

    st.graphviz_chart(graph.to_dot(emphasise=known, missing=weak), width="stretch")
    st.caption("实线箭头 = 前置依赖；虚线 = 相关概念。绿色已稳、红色薄弱、灰色未触及。")

    theme.stats(
        [
            ("知识点总数", str(len(graph)), "brand", "图谱规模"),
            ("已稳", str(len(known)), "good", "掌握度达标"),
            ("薄弱", str(len(weak)), "bad", "需要回头补"),
        ]
    )

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
    theme.hero("画像", "从你的作答里推出的判断 —— 缺什么、下一步动哪块", compact=True)
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

    theme.stats(
        [
            ("已稳知识点", str(len(profile.mastered)), "good", "掌握度达标"),
            ("薄弱知识点", str(len(profile.weak)), "bad", "做过但没稳"),
            ("未触及", str(len(profile.untouched)), "", "还没有对应的题"),
            (
                "提示依赖",
                f"{profile.hint_dependency:.1f}",
                "warn" if profile.hint_dependency > 2.5 else "good",
                "平均用到第几层",
            ),
        ]
    )

    # ── 概念层面的掌握度（图形化）───────────────────────────────────
    theme.section("各知识点掌握度", "从弱到强")
    import pandas as pd

    rows = sorted(profile.standings, key=lambda s: s.mastery)
    frame = pd.DataFrame(
        {
            "知识点": [s.title for s in rows],
            "掌握度": [s.mastery for s in rows],
            "状态": [s.label for s in rows],
        }
    )
    cbars = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, size=17)
        .encode(
            y=alt.Y("知识点:N", sort="-x", axis=alt.Axis(title=None, labelLimit=180)),
            x=alt.X(
                "掌握度:Q",
                scale=alt.Scale(domain=[0, 0.6]),
                axis=alt.Axis(title=None, format=".0%", tickCount=4),
            ),
            color=alt.Color(
                "状态:N",
                scale=alt.Scale(
                    domain=["生", "还行", "稳"],
                    range=[theme.BAD, theme.WARN, theme.GOOD],
                ),
                legend=alt.Legend(orient="bottom", title=None),
            ),
            tooltip=["知识点", alt.Tooltip("掌握度:Q", format=".3f"), "状态"],
        )
    )
    st.altair_chart(
        cbars.properties(height=max(170, 32 * len(rows))).configure(
            **theme.altair_theme()["config"]
        ),
        width="stretch",
    )

    theme.section("明细")
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
        width="stretch",
        hide_index=True,
    )

    # ── 下一步：前置已满足的可学项 ──────────────────────────────────
    theme.section("下一步可以学什么")
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
    theme.section("卡点诊断", "你总卡在哪一类地方")
    if not profile.stall_patterns:
        st.caption(
            "还没有卡点记录。在训练页点「卡住了，先放着」时如果标了卡点，"
            "这里会显示你**总卡在哪一类地方**。"
        )
    else:
        stall_frame = pd.DataFrame(
            {
                "卡点": [f"卡点 {i + 1}" for i, _ in profile.stall_patterns],
                "次数": [count for _, count in profile.stall_patterns],
            }
        )
        stall_chart = (
            alt.Chart(stall_frame)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=theme.BAD, size=46)
            .encode(
                x=alt.X("卡点:N", axis=alt.Axis(labelAngle=0, title=None)),
                y=alt.Y("次数:Q", axis=alt.Axis(tickMinStep=1, title=None)),
                tooltip=["卡点", "次数"],
            )
        )
        st.altair_chart(
            stall_chart.properties(height=165).configure(
                **theme.altair_theme()["config"]
            ),
            width="stretch",
        )
        st.caption(
            "比「做对没做对」更有用的信号：如果总是卡在第 1 类，"
            "说明问题在**方向**；总卡在最后一类，说明在**收尾**。"
        )


# --------------------------------------------------------------------------
# 出题
# --------------------------------------------------------------------------


def render_generate(
    *,
    db_path: Path,
    problems_dir: Path,
    concepts_path: Path,
    client,
    client_error: str | None = None,
) -> None:
    """AI 出题。

    本项目**不预置题库** —— 题目由 AI 生成，落在本地 ``problems/`` 目录，
    也就是"本地存储 + AI 出题"这个设计里生成的那一端。

    生成后立刻做三件事，任何一件不过就不写盘：
    1. schema 校验
    2. 骨架不得含答案关键量（否则防泄漏的结构性防线失效）
    3. ``concepts`` 必须是图谱里真实存在的 id（否则图谱/画像静默失效）
    """
    theme.hero(
        "出题",
        "题库由 AI 生成，存在本机 —— 不需要预先准备题目",
        compact=True,
    )

    if client is None:
        theme.empty_state(
            "需要先配置模型密钥",
            f"{client_error or '未找到密钥'}<br><br>"
            "在环境变量里设置 <code>DEEPSEEK_API_KEY</code> 后重启即可。",
        )
        return

    with Store(db_path) as store:
        pass  # 仅为保持与其他页面一致的存储初始化时机

    try:
        existing = load_problems(problems_dir)
    except Exception:  # noqa: BLE001
        existing = []

    graph = None
    if concepts_path.is_file():
        try:
            graph = ConceptGraph.load(concepts_path)
        except ConceptError:
            graph = None
    if graph is None:
        st.warning(
            f"找不到知识点图谱 {concepts_path} —— 生成的题将没有知识点标注，"
            "「图谱」与「画像」两页用不上它们。"
        )

    theme.stats(
        [
            ("已有题目", str(len(existing)), "brand", "本地存储"),
            ("知识点", str(len(graph)) if graph else "—", "", "图谱规模"),
            ("出口", str(problems_dir), "", "生成的文件落在这里"),
        ]
    )

    theme.section("生成新题")
    c1, c2, c3 = st.columns(3)
    with c1:
        count = st.number_input("生成几道", min_value=1, max_value=10, value=2)
    with c2:
        difficulty = st.selectbox(
            "难度",
            options=list(prompts.DIFFICULTY_LEVELS),
            format_func=lambda k: {
                "basic": "初赛",
                "standard": "复赛",
                "advanced": "决赛",
            }.get(k, k),
            index=1,
        )
    with c3:
        domain = st.selectbox("方向", options=list(VALID_DOMAINS), index=0)

    st.markdown(
        theme._compact(
            """
        <div class="lf-stats" style="margin:6px 0 14px">
          <div class="lf-stat"><div class="lf-stat-label">关卡 1</div>
            <div class="lf-stat-value brand" style="font-size:16px">格式</div>
            <div class="lf-stat-hint">YAML 字段齐全、推理型</div></div>
          <div class="lf-stat"><div class="lf-stat-label">关卡 2</div>
            <div class="lf-stat-value brand" style="font-size:16px">不泄答案</div>
            <div class="lf-stat-hint">骨架里不得含答案关键量</div></div>
          <div class="lf-stat"><div class="lf-stat-label">关卡 3</div>
            <div class="lf-stat-value brand" style="font-size:16px">知识点真实</div>
            <div class="lf-stat-hint">id 必须在图谱里存在</div></div>
          <div class="lf-stat"><div class="lf-stat-label">不过怎么办</div>
            <div class="lf-stat-value warn" style="font-size:16px">带反馈重试</div>
            <div class="lf-stat-hint">重试仍不过则跳过，不写盘</div></div>
        </div>
        """
        ),
        unsafe_allow_html=True,
    )

    if st.button("开始生成", type="primary"):
        concept_ids = (
            [(c.id, c.title) for c in graph.concepts.values()] if graph else []
        )
        progress = st.progress(0.0, text="准备中…")
        produced: list[str] = []
        failures: list[str] = []

        for i in range(int(count)):
            progress.progress(i / int(count), text=f"正在生成第 {i + 1} / {count} 道…")
            try:
                results = generate_batch(
                    client,
                    1,
                    directory=problems_dir,
                    domain=domain,
                    difficulty=difficulty,
                    concepts_path=concepts_path if graph else None,
                )
            except GenerationError as exc:
                failures.append(f"第 {i + 1} 道：{exc}")
                continue
            for r in results:
                n_pitfalls = len(r.problem.skeleton.pitfalls) if r.problem.skeleton else 0
                produced.append(
                    f"{r.problem.id}（卡点 {n_pitfalls}｜知识点 "
                    f"{'、'.join(r.problem.concepts) or '无'}）"
                )

        progress.progress(1.0, text="完成")

        if produced:
            st.success(f"已生成 {len(produced)} 道，写入 {problems_dir}")
            for line in produced:
                st.markdown(
                    theme._compact(
                        f'<div class="lf-card new" style="padding:11px 16px">'
                        f'<div class="lf-card-title">{line}</div></div>'
                    ),
                    unsafe_allow_html=True,
                )
            st.info("新题会出现在「训练」页的题号列表里。刷新页面即可看到。")

        for failure in failures:
            st.warning(failure)

        if not produced and not failures:
            st.warning("没有产出任何题目。")

    # ── 给已有题目补骨架 ────────────────────────────────────────────
    with st.expander("已有题目？让 AI 帮你补骨架"):
        st.caption(
            "老师手上有题干和答案、但没有骨架时用这个。"
            "骨架是引导提示的天花板，没有骨架的题无法进入引导流程。"
        )
        statement = st.text_area("题干", height=120, key="sk_statement")
        answer = st.text_area("参考答案", height=90, key="sk_answer")
        if st.button("生成骨架", key="sk_go"):
            if not statement.strip() or not answer.strip():
                st.warning("题干与答案都要填。")
            else:
                try:
                    raw = client.complete(
                        prompts.skeleton_system_prompt(min_pitfalls=MIN_PITFALLS),
                        prompts.skeleton_user_prompt(
                            statement=statement,
                            answer=answer,
                            min_pitfalls=MIN_PITFALLS,
                        ),
                    )
                    st.code(raw, language="yaml")
                    st.caption(
                        "把这段贴进对应题目的 YAML 里。写盘前仍会走同一套校验。"
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"生成失败：{exc}")


# --------------------------------------------------------------------------
# 模型设置
# --------------------------------------------------------------------------


def render_model_settings(on_saved=None) -> None:
    """侧栏里的 API 配置。

    **为什么要有它**：让老师去改系统环境变量，对一个「双击就用」的工具
    是硬门槛 —— Windows 要开系统属性、macOS 要改 shell 配置。
    写在这里，填一次就好。

    密钥只显示掩码；保存后落在用户配置目录（权限 0600），
    不写进仓库、不写进日志。
    """
    from lifeng.settings import (
        KNOWN_BASE_URLS,
        KNOWN_MODELS,
        env_key_present,
        load_settings,
        save_settings,
    )

    current = load_settings()
    overridden = env_key_present()

    with st.expander("模型设置", expanded=not current.configured):
        if overridden:
            st.caption("已由环境变量 `DEEPSEEK_API_KEY` 提供，界面上的填写会被它覆盖。")
        else:
            st.caption(f"当前密钥：`{current.masked_key}`")

        key_input = st.text_input(
            "API Key",
            value="",
            type="password",
            placeholder="sk-..." if not current.configured else "留空则不修改",
            key="settings_api_key",
            help="只保存在本机，不会上传到任何地方。",
        )

        base_url = st.text_input(
            "接口地址",
            value=current.base_url,
            key="settings_base_url",
            help="换供应商或走本地代理时改这里。",
        )

        model = st.selectbox(
            "模型",
            options=list(KNOWN_MODELS),
            index=(
                list(KNOWN_MODELS).index(current.model)
                if current.model in KNOWN_MODELS
                else 0
            ),
            key="settings_model",
        )

        # 常见端点的快捷填充 —— 手打 URL 很容易错
        with st.expander("常见接口地址"):
            for url in KNOWN_BASE_URLS:
                st.code(url, language=None)

        c1, c2 = st.columns(2)
        with c1:
            if st.button("保存", type="primary", key="settings_save"):
                updated = replace_settings(current, key_input, base_url, model)
                try:
                    path = save_settings(updated)
                    st.success("已保存")
                    st.caption(str(path))
                    if on_saved is not None:
                        on_saved()
                except OSError as exc:
                    st.error(f"保存失败：{exc}")
        with c2:
            if st.button("测试连接", key="settings_test"):
                probe = replace_settings(current, key_input, base_url, model)
                if not probe.configured:
                    st.warning("先填 API Key。")
                else:
                    try:
                        client = DeepSeekClient(
                            api_key=probe.api_key,
                            model=probe.model,
                            base_url=probe.base_url,
                        )
                    except HintError as exc:
                        st.error(str(exc))
                    else:
                        with st.spinner("正在请求…"):
                            ok, message = client.test_connection()
                        (st.success if ok else st.error)(message)


def replace_settings(current, key_input: str, base_url: str, model: str):
    """把界面上的输入合成一份新配置。

    密钥留空表示**不修改** —— 界面上显示的是掩码，
    若把掩码当成新密钥存回去，密钥就被毁了。
    """
    from dataclasses import replace as _replace

    from lifeng.settings import DEFAULT_BASE_URL, DEFAULT_MODEL

    return _replace(
        current,
        api_key=key_input.strip() or current.api_key,
        base_url=base_url.strip() or DEFAULT_BASE_URL,
        model=model.strip() or DEFAULT_MODEL,
    )
