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
        self._conn.commit()

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
    ) -> int:
        """记录一次学生尝试。

        - 基线阶段：``level_requested=0``、``hint_text=None``
        - 实验阶段：``level_requested`` ∈ 1..4，``hint_text`` 为系统给出的提示
        - ``leaked``：未经 T4 校验时传 ``None``
        - ``progressed``：留空，由实验者事后用 :meth:`set_progressed` 回填
        """
        if not attempt_text.strip():
            raise StoreError("attempt_text 不能为空")
        if level_requested < 0:
            raise StoreError(f"level_requested 不能为负：{level_requested}")

        cur = self._conn.execute(
            """
            INSERT INTO attempts
                (session_id, level_requested, attempt_text, hint_text, leaked)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                level_requested,
                attempt_text.strip(),
                hint_text,
                None if leaked is None else int(leaked),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def set_progressed(self, attempt_id: int, progressed: bool) -> None:
        """回填人工判定结果（计划 §3：n=20 时由实验者判定）。"""
        self._update_flag(attempt_id, "progressed", progressed)

    def set_leaked(self, attempt_id: int, leaked: bool) -> None:
        """回填 T4 校验结果。"""
        self._update_flag(attempt_id, "leaked", leaked)

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
                   a.hint_text, a.leaked, a.progressed, a.created_at,
                   s.problem_id, s.student_code, s.mode
            FROM attempts a
            JOIN sessions s ON s.id = a.session_id
            ORDER BY a.id
            """
        )
