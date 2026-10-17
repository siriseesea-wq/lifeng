"""砺锋 v2 —— 会话日志与存储（T2）。

职责边界：只做持久化与基础读取。指标计算属于 T9（``lifeng/analyze.py``），
不放在这里 —— 否则本模块会变成"什么都管"的过载 owner。

数据安全：``store.db`` 含未成年人答题记录，已在 ``.gitignore`` 中排除，不得入库。

并发（本地多学生场景）：

Python 的 ``sqlite3`` 默认 ``busy_timeout=5000`` ms，短时写锁竞争会自动等待而非
立即失败。实测按 app 真实模式（4 名学生 × 60 轮「开关连接 + 2 次写」，耗时 0.2 秒）
**未出现 ``database is locked``**，故**不启用 WAL** —— 没有失败证据就加配置
属于投机性加固。

若将来真的出现 locked 错误，修法是启用 WAL：实测 WAL 下并发读完全不被写锁
阻塞（0.000s），而默认的 ``delete`` 日志模式会阻塞。
"""

from __future__ import annotations

import functools
import sqlite3
from pathlib import Path
from typing import Literal

DEFAULT_DB_PATH = Path("store.db")

#: 基线（无提示） vs 实验（有提示）。
#:
#: 计划 T7 要求 A/B 分半测量基线推进率；若不在存储层区分，
#: T9 就无法同时算出「基线推进率」与「实验推进率」。
#: 这是执行 T2 时发现的计划 schema 缺口，已同步修正计划 §10 T2。
AttemptMode = Literal["baseline", "hint"]

VALID_MODES: tuple[str, ...] = ("baseline", "hint")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id   TEXT    NOT NULL,
    student_code TEXT    NOT NULL,
    mode         TEXT    NOT NULL CHECK (mode IN ('baseline', 'hint')),
    started_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    level_requested INTEGER NOT NULL,
    attempt_text    TEXT    NOT NULL,
    hint_text       TEXT,
    leaked          INTEGER,
    progressed      INTEGER,
    pitfall_index   INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_student ON sessions(student_code);

-- 复习状态：按 (学生, 题目) 记录掌握度与下次复习时间。
-- 由 lifeng/review.py 的 SM-2 调度写入；掌握度由 lifeng/mastery.py 更新。
CREATE TABLE IF NOT EXISTS review_state (
    student_code  TEXT    NOT NULL,
    problem_id    TEXT    NOT NULL,
    mastery       REAL    NOT NULL,
    next_review   TEXT    NOT NULL,          -- ISO 日期 YYYY-MM-DD
    interval_days INTEGER NOT NULL,
    review_count  INTEGER NOT NULL,
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (student_code, problem_id)
);

CREATE INDEX IF NOT EXISTS idx_review_due ON review_state(student_code, next_review);
"""


class StoreError(ValueError):
    """存储层参数非法（调用方传错了参数）。"""


class StorageError(StoreError):
    """底层存储失败（磁盘满、库损坏、并发冲突等）。

    存在的意义是把**后端细节挡在 ``store.py`` 之内**：
    其他模块只认识 ``StoreError``，**不得 import sqlite3**。
    这样将来把 SQLite 换成服务器数据库时，调用方无需改动。
    （服务端排在全部功能**最后**，见 Design Spec ADR-001 优先级决议。）
    """


def _translate_storage_errors(fn):
    """把后端异常翻译成 :class:`StorageError`，防止 sqlite3 泄漏到调用方。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except sqlite3.Error as exc:
            raise StorageError(str(exc)) from exc

    return wrapper


class Store:
    """SQLite 会话日志。

    可作上下文管理器使用::

        with Store("store.db") as store:
            sid = store.create_session("p001", "S1", "hint")
            store.log_attempt(sid, "学生本次尝试……", level_requested=1, hint_text="……")
    """

    @_translate_storage_errors
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._ensure_columns()
        self._conn.commit()

    def _ensure_columns(self) -> None:
        """为已存在的旧库补列。

        ``CREATE TABLE IF NOT EXISTS`` 不会给已存在的表加列，
        因此结构升级后旧库会静默缺列并导致后续读写报错。
        本方法把这种情况转成一次正确迁移。

        T7 设计实验协议时发现：分析「突破发生在第几层提示」
        必须知道每次尝试对应哪个卡点，故新增 ``pitfall_index``。
        """
        existing = {
            row[1] for row in self._conn.execute("PRAGMA table_info(attempts)")
        }
        if existing and "pitfall_index" not in existing:
            self._conn.execute("ALTER TABLE attempts ADD COLUMN pitfall_index INTEGER")

    # -- 生命周期 ---------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- 写入 -------------------------------------------------------------

    @_translate_storage_errors
    def create_session(
        self, problem_id: str, student_code: str, mode: AttemptMode
    ) -> int:
        """开一次作答会话。``mode`` 区分基线与实验，是 T9 分指标的依据。"""
        if not problem_id.strip():
            raise StoreError("problem_id 不能为空")
        if not student_code.strip():
            raise StoreError("student_code 不能为空")
        if mode not in VALID_MODES:
            raise StoreError(f"mode 取值非法：{mode!r}，应为 {VALID_MODES}")

        cur = self._conn.execute(
            "INSERT INTO sessions (problem_id, student_code, mode) VALUES (?, ?, ?)",
            (problem_id.strip(), student_code.strip(), mode),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @_translate_storage_errors
    def log_attempt(
        self,
        session_id: int,
        attempt_text: str,
        *,
        level_requested: int = 0,
        hint_text: str | None = None,
        leaked: bool | None = None,
        pitfall_index: int | None = None,
    ) -> int:
        """记录一次学生尝试。

        - 基线阶段：``level_requested=0``、``hint_text=None``
        - 实验阶段：``level_requested`` ∈ 1..4，``hint_text`` 为系统给出的提示
        - ``leaked``：未经 T4 校验时传 ``None``
        - ``progressed`` / ``pitfall_index``：留空，由实验者事后回填
          （见 :meth:`set_progressed` / :meth:`set_pitfall_index`）
        """
        if not attempt_text.strip():
            raise StoreError("attempt_text 不能为空")
        if level_requested < 0:
            raise StoreError(f"level_requested 不能为负：{level_requested}")
        if pitfall_index is not None and pitfall_index < 0:
            raise StoreError(f"pitfall_index 不能为负：{pitfall_index}")

        cur = self._conn.execute(
            """
            INSERT INTO attempts
                (session_id, level_requested, attempt_text, hint_text, leaked,
                 pitfall_index)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                level_requested,
                attempt_text.strip(),
                hint_text,
                None if leaked is None else int(leaked),
                pitfall_index,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def set_progressed(self, attempt_id: int, progressed: bool) -> None:
        """回填人工判定结果（计划 §3：n=20 时由实验者判定）。"""
        self._update_flag(attempt_id, "progressed", progressed)

    @_translate_storage_errors
    def get_attempt(self, attempt_id: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"attempt id 不存在：{attempt_id}")
        return row

    @_translate_storage_errors
    def latest_attempt_id(self, session_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT id FROM attempts WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return int(row["id"]) if row else None

    @_translate_storage_errors
    def attach_hint(
        self, attempt_id: int, level: int, hint_text: str, *, leaked: bool = False
    ) -> None:
        """把提示挂到某次尝试上，而不是另起一行。

        **一次学生尝试 = 一行**；提示是这次尝试「获得的帮助」，不是独立事件。
        这样 T9 才能按卡点统计「突破依赖第几层提示」（协议 §6.2 M2）。

        若该次尝试已有提示则拒绝 —— 协议 §4 要求每层提示后
        必须先提交一次新尝试，才能请求下一层。
        """
        if level < 1:
            raise StoreError(f"提示层级必须 ≥1，收到 {level}")
        if not hint_text.strip():
            raise StoreError("hint_text 不能为空")

        row = self.get_attempt(attempt_id)
        if row["hint_text"] is not None:
            raise StoreError(
                f"attempt {attempt_id} 已有提示；"
                "协议要求每层提示后先提交一次新尝试"
            )

        self._conn.execute(
            """
            UPDATE attempts
               SET level_requested = ?, hint_text = ?, leaked = ?
             WHERE id = ?
            """,
            (level, hint_text, int(leaked), attempt_id),
        )
        self._conn.commit()

    def set_leaked(self, attempt_id: int, leaked: bool) -> None:
        """回填 T4 校验结果。"""
        self._update_flag(attempt_id, "leaked", leaked)

    @_translate_storage_errors
    def set_pitfall_index(self, attempt_id: int, index: int | None) -> None:
        """回填该次尝试所属的卡点序号（从 0 起，``None`` 表示不适用）。

        供 T9 分析「突破依赖第几层提示」使用 —— 这是判断
        引导是否真在起作用的关键指标（见 docs/experiment-protocol.md §6）。
        """
        if index is not None and index < 0:
            raise StoreError(f"pitfall_index 不能为负：{index}")
        cur = self._conn.execute(
            "UPDATE attempts SET pitfall_index = ? WHERE id = ?", (index, attempt_id)
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"attempt id 不存在：{attempt_id}")

    @_translate_storage_errors
    def _update_flag(self, attempt_id: int, column: str, value: bool) -> None:
        if column not in ("progressed", "leaked"):
            raise StoreError(f"未知字段：{column}")
        cur = self._conn.execute(
            f"UPDATE attempts SET {column} = ? WHERE id = ?",  # noqa: S608 — 列名已白名单校验
            (int(value), attempt_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"attempt id 不存在：{attempt_id}")

    # -- 读取 -------------------------------------------------------------

    @_translate_storage_errors
    def counts(self) -> dict[str, int]:
        """行数概览，供快速自检与 T9 前置检查使用。"""
        return {
            "sessions": self._conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0],
            "attempts": self._conn.execute(
                "SELECT COUNT(*) FROM attempts"
            ).fetchone()[0],
            "progressed_judged": self._conn.execute(
                "SELECT COUNT(*) FROM attempts WHERE progressed IS NOT NULL"
            ).fetchone()[0],
        }

    @_translate_storage_errors
    def elapsed_seconds(self, session_id: int) -> int | None:
        """该会话从开始到现在的秒数。

        计时不需要新字段：``sessions.started_at`` 与当前时间就够。
        """
        row = self._conn.execute(
            "SELECT CAST((julianday('now','localtime') - julianday(started_at)) * 86400 AS INTEGER)"
            " AS secs FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None or row["secs"] is None:
            return None
        return max(0, int(row["secs"]))

    @_translate_storage_errors
    def attempt_count(self, session_id: int) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM attempts WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        )

    @_translate_storage_errors
    def max_hint_level(self, session_id: int) -> int:
        """该会话用过的最高提示层级（0 = 全程无提示）。"""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(level_requested), 0) FROM attempts WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0] or 0)

    # -- 复习状态（纯读写；调度编排在 lifeng/review.py）-------------------

    @_translate_storage_errors
    def get_review_state(self, student_code: str, problem_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM review_state WHERE student_code = ? AND problem_id = ?",
            (student_code, problem_id),
        ).fetchone()

    @_translate_storage_errors
    def upsert_review_state(
        self,
        student_code: str,
        problem_id: str,
        *,
        mastery: float,
        next_review: str,
        interval_days: int,
        review_count: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO review_state
                (student_code, problem_id, mastery, next_review,
                 interval_days, review_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(student_code, problem_id) DO UPDATE SET
                mastery       = excluded.mastery,
                next_review   = excluded.next_review,
                interval_days = excluded.interval_days,
                review_count  = excluded.review_count,
                updated_at    = excluded.updated_at
            """,
            (student_code, problem_id, mastery, next_review, interval_days, review_count),
        )
        self._conn.commit()

    @_translate_storage_errors
    def due_reviews(
        self, student_code: str, *, on_date: str, limit: int
    ) -> list[sqlite3.Row]:
        """到期待复习项，最弱的排前面。``on_date`` 为 ISO 日期串。"""
        return list(
            self._conn.execute(
                """
                SELECT * FROM review_state
                 WHERE student_code = ? AND next_review <= ?
                 ORDER BY mastery ASC, next_review ASC
                 LIMIT ?
                """,
                (student_code, on_date, limit),
            )
        )

    @_translate_storage_errors
    def known_students(self) -> list[str]:
        """出现过的学生代号（按最近活跃排序）。

        界面用它做下拉选择，省得每次重输。**刻意不用"上次填的值预填"** ——
        预填会让人在没注意的情况下用错代号，把作答记到别人名下，
        那比每次重输糟得多。
        """
        rows = self._conn.execute(
            """
            SELECT student_code, MAX(updated_at) AS last_seen
              FROM review_state
             GROUP BY student_code
             ORDER BY last_seen DESC
            """
        ).fetchall()
        return [str(r["student_code"]) for r in rows]

    @_translate_storage_errors
    def student_overview(self, student_code: str) -> list[sqlite3.Row]:
        """该学生全部题目的复习状态，按掌握度升序（最弱在前）。"""
        return list(
            self._conn.execute(
                "SELECT * FROM review_state WHERE student_code = ? ORDER BY mastery ASC",
                (student_code,),
            )
        )

    # -- 读取 -------------------------------------------------------------

    @_translate_storage_errors
    def iter_attempts(self) -> list[sqlite3.Row]:
        """按时间顺序返回带会话上下文的作答记录，供 T9 消费。

        返回列表而非生成器：生成器会把异常推迟到迭代时抛出，
        从而绕过 ``_translate_storage_errors`` 的翻译。
        （方法名保留 ``iter_`` 以兼容既有调用点。）
        """
        return list(
            self._conn.execute(
                """
                SELECT a.id, a.session_id, a.level_requested, a.attempt_text,
                       a.hint_text, a.leaked, a.progressed, a.pitfall_index,
                       a.created_at,
                       s.problem_id, s.student_code, s.mode
                FROM attempts a
                JOIN sessions s ON s.id = a.session_id
                ORDER BY a.id
                """
            )
        )
