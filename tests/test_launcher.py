"""打包入口（launcher）测试。

重点是 :func:`lifeng.launcher._ensure_streamlit_credentials` ——
缺这个文件时 Streamlit 会阻塞在首次运行的邮箱询问上，
打包成可执行文件后表现为**双击没反应**（窗口空白，实际在等 stdin）。
这个坑已在真实打包产物上复现过，因此用测试钉住。
"""

from __future__ import annotations

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
