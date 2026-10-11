"""打包入口（launcher）测试。

重点是 :func:`lifeng.launcher._ensure_streamlit_credentials` ——
缺这个文件时 Streamlit 会阻塞在首次运行的邮箱询问上，
打包成可执行文件后表现为**双击没反应**（窗口空白，实际在等 stdin）。
这个坑已在真实打包产物上复现过，因此用测试钉住。
"""

from __future__ import annotations

import sys
from pathlib import Path

import lifeng.launcher as launcher


class TestResourcePaths:
    def test_resource_root_is_project_root(self) -> None:
        root = launcher._resource_root()
        assert (root / "lifeng" / "launcher.py").is_file()

    def test_app_script_exists_in_source_mode(self) -> None:
        assert launcher._app_script(launcher._resource_root()).is_file()

    def test_problems_dir_exists(self) -> None:
        assert launcher._problems_dir(launcher._resource_root()).is_dir()

    def test_cwd_problems_wins(self, tmp_path, monkeypatch) -> None:
        """老师自己放在运行目录的题库必须优先于内置样例。"""
        custom = tmp_path / "problems"
        custom.mkdir()
        monkeypatch.chdir(tmp_path)
        assert launcher._problems_dir(launcher._resource_root()) == custom


class TestStreamlitCredentials:
    def test_creates_when_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(launcher.Path, "home", staticmethod(lambda: tmp_path))
        cred = tmp_path / ".streamlit" / "credentials.toml"
        assert not cred.exists()

        assert launcher._ensure_streamlit_credentials() is True

        assert cred.is_file()
        assert cred.read_text(encoding="utf-8").startswith("[general]")

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch) -> None:
        cred = tmp_path / ".streamlit" / "credentials.toml"
        cred.parent.mkdir(parents=True)
        cred.write_text('[general]\nemail = "someone@example.com"\n', encoding="utf-8")

        monkeypatch.setattr(launcher.Path, "home", staticmethod(lambda: tmp_path))
        assert launcher._ensure_streamlit_credentials() is True
        # 不覆盖用户已有设置
        assert "someone@example.com" in cred.read_text(encoding="utf-8")

    def test_returns_false_when_unwritable(self, tmp_path, monkeypatch) -> None:
        """写不进去时必须返回 False，让调用方退化为 headless，而不是崩掉。"""
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")   # 把「home」指向一个文件
        monkeypatch.setattr(launcher.Path, "home", staticmethod(lambda: blocker))

        assert launcher._ensure_streamlit_credentials() is False


class TestGenerateSubcommand:
    """打包后没有 ``python -m lifeng.generator`` 可用。

    若 launcher 不分发 ``generate`` 子命令，用户就得另装 Python 才能出题 ——
    AI 出题这个功能在打包版里等于不存在。
    """

    def test_dispatches_to_generator(self) -> None:
        import os
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        proc = subprocess.run(
            [sys.executable, "-m", "lifeng.launcher", "generate", "--count", "1"],
            capture_output=True,
            text=True,
            cwd=root,
            env={**os.environ, "DEEPSEEK_API_KEY": ""},
            timeout=90,
        )
        combined = proc.stdout + proc.stderr
        # 没有 key 时应由生成器友好失败，而不是跑去启动 Streamlit
        assert "API key" in combined, combined[-500:]
        assert "启动界面中" not in combined



# --------------------------------------------------------------------------
# 独立窗口启动
# --------------------------------------------------------------------------


class TestOpenBrowserWindow:
    """以独立窗口打开，而不是浏览器标签页。

    只有 Chromium 系浏览器支持 ``--app=``（无地址栏、无标签页）。
    找不到时必须**退化而不是失败** —— 服务已经起来了，
    打不开窗口不该让用户什么都用不了。
    """

    def test_uses_app_mode_when_chromium_found(self, monkeypatch):
        import subprocess

        from lifeng import launcher

        launched: list[list[str]] = []
        monkeypatch.setattr(launcher, "_find_chromium", lambda: "/usr/bin/chromium")
        monkeypatch.setattr(
            subprocess, "Popen", lambda argv, **kw: launched.append(argv)
        )

        how = launcher.open_browser_window("http://127.0.0.1:9999")

        assert launched, "没有启动浏览器"
        argv = launched[0]
        assert "--app=http://127.0.0.1:9999" in argv, "没有用 --app 模式，会开出带地址栏的标签页"
        assert any(a.startswith("--window-size=") for a in argv)
        assert any(a.startswith("--user-data-dir=") for a in argv), (
            "没有独立用户数据目录时，若用户已开着浏览器，"
            "--app 可能被当成新标签页处理"
        )
        assert "独立窗口" in how

    def test_falls_back_to_default_browser(self, monkeypatch):
        import subprocess
        import webbrowser

        from lifeng import launcher

        monkeypatch.setattr(launcher, "_find_chromium", lambda: None)
        monkeypatch.setattr(webbrowser, "open", lambda url: True)

        how = launcher.open_browser_window("http://127.0.0.1:9999")
        assert "标签页" in how

    def test_never_raises_when_everything_fails(self, monkeypatch):
        """打不开窗口不该让程序挂掉 —— 服务已经起来了。"""
        import webbrowser

        from lifeng import launcher

        monkeypatch.setattr(launcher, "_find_chromium", lambda: None)

        def boom(url):
            raise OSError("没有可用浏览器")

        monkeypatch.setattr(webbrowser, "open", boom)
        how = launcher.open_browser_window("http://127.0.0.1:9999")
        assert "手动" in how

    def test_popen_failure_falls_through(self, monkeypatch):
        import subprocess
        import webbrowser

        from lifeng import launcher

        monkeypatch.setattr(launcher, "_find_chromium", lambda: "/usr/bin/chromium")

        def boom(argv, **kw):
            raise OSError("权限不足")

        monkeypatch.setattr(subprocess, "Popen", boom)
        monkeypatch.setattr(webbrowser, "open", lambda url: True)
        assert "标签页" in launcher.open_browser_window("http://x")


class TestFreePort:
    def test_returns_a_bindable_port(self):
        import socket

        from lifeng.launcher import _free_port

        port = _free_port()
        assert 1024 < port < 65536
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))  # 应当可绑定（刚刚才释放）

    def test_avoids_the_default_port_trap(self):
        """固定 8501 会在被占时让 Streamlit 换端口，而我们拼的 URL 不变。

        结果就是打开窗口看到「无法连接」。
        """
        from pathlib import Path

        from lifeng import launcher

        src = Path(launcher.__file__).read_text(encoding="utf-8")
        assert "_free_port()" in src
        assert "--server.port=" in src


class TestWaitAndOpen:
    def test_waits_for_health_before_opening(self, monkeypatch):
        """不等就绪会先看到「无法连接」的页面，用户会以为程序坏了。"""
        from lifeng import launcher

        import urllib.request

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        calls: list[str] = []
        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: FakeResponse())
        monkeypatch.setattr(
            launcher, "open_browser_window", lambda url: calls.append(url) or "独立窗口"
        )

        launcher._wait_and_open("http://x", "http://x/_stcore/health")
        assert calls == ["http://x"]

    def test_gives_up_quietly_on_timeout(self, monkeypatch):
        from lifeng import launcher

        import urllib.request

        def boom(url, timeout):
            raise OSError("not up")

        opened: list[str] = []
        monkeypatch.setattr(urllib.request, "urlopen", boom)
        monkeypatch.setattr(
            launcher, "open_browser_window", lambda url: opened.append(url)
        )

        launcher._wait_and_open("http://x", "http://x/_stcore/health", timeout=0.1)
        assert opened == [], "服务没起来就不该开窗口"


# --------------------------------------------------------------------------
# 窗口模式下的诊断（去掉黑框的代价）
# --------------------------------------------------------------------------


class TestWindowedModeDiagnostics:
    """打包成 console=False 后 stdout 没了。

    去掉黑框与补上诊断**必须一起做**：只去黑框等于把报错藏起来，
    用户看到的是「双击没反应」，比有黑框更糟。
    """

    def test_log_never_raises_without_stdout(self, monkeypatch, tmp_path):
        """窗口模式下 sys.stdout 是 None，直接 print 会抛异常。"""
        from lifeng import launcher

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "stdout", None)

        launcher.log("测试消息")  # 不应抛异常

        assert (tmp_path / "lifeng.log").exists(), "没有写日志文件"
        assert "测试消息" in (tmp_path / "lifeng.log").read_text(encoding="utf-8")

    def test_log_never_raises_when_stdout_is_broken(self, monkeypatch, tmp_path):
        """有些环境下 stdout 存在但不可写（管道关闭等）。"""
        from lifeng import launcher

        monkeypatch.chdir(tmp_path)

        class Broken:
            def write(self, *_args):
                raise ValueError("closed")

            def flush(self):
                raise ValueError("closed")

        monkeypatch.setattr(sys, "stdout", Broken())
        launcher.log("仍然要写进日志")
        assert "仍然要写进日志" in (tmp_path / "lifeng.log").read_text(
            encoding="utf-8"
        )

    def test_log_appends(self, monkeypatch, tmp_path):
        from lifeng import launcher

        monkeypatch.chdir(tmp_path)
        launcher.log("第一行")
        launcher.log("第二行")
        lines = (tmp_path / "lifeng.log").read_text(encoding="utf-8").splitlines()
        assert lines == ["第一行", "第二行"]

    def test_log_survives_unwritable_directory(self, monkeypatch, tmp_path):
        """日志写不进去也不该让程序挂掉。"""
        from lifeng import launcher

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            launcher.Path, "cwd", staticmethod(lambda: Path("/proc/nonexistent"))
        )
        launcher.log("写不进去就算了")  # 不应抛异常

    def test_fatal_records_to_log(self, monkeypatch, tmp_path):
        """弹窗可能失败（无桌面环境），日志必须留下痕迹。"""
        from lifeng import launcher

        monkeypatch.chdir(tmp_path)

        # **必须关掉弹窗。** 真的去弹的话，Windows 的 MessageBoxW 与
        # macOS 的 tkinter showerror 都是模态对话框，会一直阻塞到有人点掉 ——
        # 在 CI 上就是任务永久挂起。实测挂住了整条流水线 17 分钟。
        launcher.fatal("界面文件丢了", dialog=False)

        assert "界面文件丢了" in (tmp_path / "lifeng.log").read_text(encoding="utf-8")


class TestDialogsEnabled:
    """模态对话框绝不能在没有人的环境里弹。"""

    def test_ci_disables_dialogs(self, monkeypatch):
        from lifeng import launcher

        monkeypatch.delenv("LIFENG_NO_DIALOG", raising=False)
        monkeypatch.setenv("CI", "true")
        assert not launcher.dialogs_enabled()

    def test_explicit_flag_disables(self, monkeypatch):
        from lifeng import launcher

        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("LIFENG_NO_DIALOG", "1")
        assert not launcher.dialogs_enabled()

    def test_interactive_default_is_enabled(self, monkeypatch):
        """图形化双击场景没有 tty，但正需要弹窗 —— 所以不能靠 tty 判断。"""
        from lifeng import launcher

        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("LIFENG_NO_DIALOG", raising=False)
        assert launcher.dialogs_enabled()

    def test_fatal_skips_dialog_when_disabled(self, monkeypatch, tmp_path):
        from lifeng import launcher

        monkeypatch.chdir(tmp_path)
        shown: list[str] = []
        monkeypatch.setattr(launcher, "show_dialog", lambda m: shown.append(m))

        launcher.fatal("测试", dialog=False)

        assert shown == [], "dialog=False 时不该弹窗"
        assert "测试" in (tmp_path / "lifeng.log").read_text(encoding="utf-8")

    def test_fatal_shows_dialog_when_enabled(self, monkeypatch, tmp_path):
        from lifeng import launcher

        monkeypatch.chdir(tmp_path)
        shown: list[str] = []
        monkeypatch.setattr(launcher, "show_dialog", lambda m: shown.append(m))
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("LIFENG_NO_DIALOG", raising=False)

        launcher.fatal("启动失败")

        assert shown == ["启动失败"]

    def test_fatal_in_ci_does_not_show_dialog(self, monkeypatch, tmp_path):
        """回归：CI 上弹模态对话框会让任务挂到超时。

        实测挂住了整条流水线 17 分钟（Windows MessageBoxW / macOS tkinter）。
        """
        from lifeng import launcher

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CI", "true")
        shown: list[str] = []
        monkeypatch.setattr(launcher, "show_dialog", lambda m: shown.append(m))

        launcher.fatal("启动失败")

        assert shown == [], "CI 里弹了模态对话框 —— 会永久挂起"


class TestSpecIsWindowed:
    """打包配置层面的约束 —— 这些改错了不会报错，只会让用户看到黑框。"""

    def test_gui_exe_has_no_console(self):
        src = Path("lifeng.spec").read_text(encoding="utf-8")
        # 找到 name="lifeng" 那个 EXE 块
        gui = src[src.find('name="lifeng"') :]
        gui_block = gui[: gui.find(")")]
        assert "console=False" in gui_block, (
            "GUI 版必须是 console=False，否则双击会弹出黑色命令行窗口"
        )

    def test_cli_exe_keeps_console(self):
        src = Path("lifeng.spec").read_text(encoding="utf-8")
        cli = src[src.find('name="lifeng-cli"') :]
        cli_block = cli[: cli.find(")")]
        assert "console=True" in cli_block, (
            "命令行版必须保留控制台，否则 generate 子命令的输出没有地方去"
        )

    def test_both_exes_are_collected(self):
        src = Path("lifeng.spec").read_text(encoding="utf-8")
        coll = src[src.find("coll = COLLECT") :]
        assert "exe," in coll and "exe_cli," in coll, "COLLECT 里两个 exe 都要有"

    def test_no_bare_print_in_launcher(self):
        """窗口模式下裸 print 会抛异常 —— 必须走 log()。"""
        import re

        from lifeng import launcher

        src = Path(launcher.__file__).read_text(encoding="utf-8")
        # 去掉 log() 函数自身内部那一处
        body = src.replace('stream.write(line)', "")
        bare = [
            line
            for line in body.splitlines()
            if re.search(r"(?<![\w.])print\(", line) and "def log" not in line
        ]
        assert bare == [], f"launcher 里还有裸 print：{bare}"


class TestNoBrowserSwitch:
    def test_switch_suppresses_launch(self, monkeypatch):
        """无头部署与启动冒烟测试不该凭空拉起浏览器。"""
        from lifeng import launcher

        monkeypatch.setenv("LIFENG_NO_BROWSER", "1")
        called: list[str] = []
        monkeypatch.setattr(launcher, "_find_chromium", lambda: called.append("x") or "/usr/bin/chrome")

        how = launcher.open_browser_window("http://127.0.0.1:1")
        assert called == [], "设置了开关还是去找浏览器了"
        assert "未打开" in how
