"""砺锋 v2 —— 会话日志与存储（T2）。

职责边界：只做持久化与基础读取。指标计算属于 T9（``lifeng/analyze.py``），
不放在这里 —— 否则本模块会变成"什么都管"的过载 owner。

数据安全：``store.db`` 含未成年人答题记录，已在 ``.gitignore`` 中排除，不得入库。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator, Literal

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
"""


class StoreError(ValueError):
    """存储层参数非法。"""


class Store:
    """SQLite 会话日志。

    可作上下文管理器使用::

        with Store("store.db") as store:
            sid = store.create_session("p001", "S1", "hint")
            store.log_attempt(sid, "学生本次尝试……", level_requested=1, hint_text="……")
    """

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

    def get_attempt(self, attempt_id: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"attempt id 不存在：{attempt_id}")
        return row

    def latest_attempt_id(self, session_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT id FROM attempts WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return int(row["id"]) if row else None

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

    def iter_attempts(self) -> Iterator[sqlite3.Row]:
        """按时间顺序产出带会话上下文的作答记录，供 T9 消费。"""
        yield from self._conn.execute(
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
