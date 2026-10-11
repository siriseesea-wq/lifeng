"""控制台编码回归测试。

**背景**：Windows 控制台与重定向输出的默认编码是 cp1252，
打印中文会抛 ``UnicodeEncodeError`` 并让程序整体崩溃。

实测复现：
- GitHub Actions ``windows-latest``（CI run 38060571646）
  ``python -m lifeng.models problems/`` 崩溃
- ``launcher`` 的启动 banner 同样会崩 —— 意味着非中文 Windows 上
  **打包版会在启动瞬间退出**

这里用 ``PYTHONIOENCODING=ascii`` 在 Linux 上**确定性复现**该场景，
不需要真的有 Windows 机器。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(encoding: str) -> dict[str, str]:
    return {**os.environ, "PYTHONIOENCODING": encoding}


def _run(args: list[str], encoding: str = "ascii") -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, env=_env(encoding), cwd=ROOT
    )


class TestModelsCli:
    def test_survives_non_utf8_console(self) -> None:
        """回归：CLI 打印中文时不得崩溃（这正是 CI 抓到的失败）。"""
        result = _run([sys.executable, "-m", "lifeng.models", "problems/"])
        assert result.returncode == 0, (
            f"CLI 在非 UTF-8 控制台上崩溃：\n{result.stderr[-800:]}"
        )

    def test_still_reports_schema_failure(self) -> None:
        """降级输出不能把「校验失败」也吞掉 —— 仍须返回非零。"""
        result = _run([sys.executable, "-m", "lifeng.models", "/nonexistent-dir"])
        assert result.returncode == 1

    def test_still_prints_content(self) -> None:
        """降级不等于静默：仍应产出输出。"""
        result = _run([sys.executable, "-m", "lifeng.models", "problems/"])
        assert result.stdout.strip(), "降级后不应完全无输出"


class TestLauncherBanner:
    def test_launcher_starts_without_unicode_crash(self, tmp_path: Path) -> None:
        """启动 banner 含中文；未处理时会让打包版在 Windows 上启动即崩。

        **必须等到服务真的起来**才算通过 —— 早先版本的测试只读 3 行，
        而 ``UnicodeEncodeError`` 出现在 traceback 末尾，导致崩溃也判成通过。
        这种「看着在守、实际守不住」的测试比没有更危险。
        """
        # cwd 必须是项目根，否则 `import lifeng` 失败、进程会以
        # ModuleNotFoundError 瞬间退出 —— 那会让本测试因错误的原因通过或失败。
        env = {
            **_env("ascii"),
            "LIFENG_DB": str(tmp_path / "store.db"),
            # 这是启动冒烟测试，不要真的拉起浏览器
            "LIFENG_NO_BROWSER": "1",
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "lifeng.launcher"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=ROOT,
        )
        out = ""
        started = False
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break  # EOF：进程已结束
                out += line
                if "UnicodeEncodeError" in out:
                    break
                if "Uvicorn" in out or "Local URL" in out:
                    started = True
                    break
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

        assert "UnicodeEncodeError" not in out, f"banner 崩溃：\n{out[-800:]}"
        assert started, f"启动器未能把服务跑起来：\n{out[-800:]}"
