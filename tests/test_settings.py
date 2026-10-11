"""本地 API 配置测试。

这一块的风险集中在**密钥处理**上 —— 泄露一次就没法收回，
而这些错误不会报错、只会安静地发生。
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from lifeng import settings as S


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """把配置目录指到临时目录，避免污染真实用户配置。"""
    monkeypatch.setenv("LIFENG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("LIFENG_CONFIG", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    return tmp_path


class TestPath:
    def test_uses_platform_config_dir(self, monkeypatch):
        monkeypatch.delenv("LIFENG_CONFIG_DIR", raising=False)
        path = S.config_dir()
        assert "lifeng" in str(path)

    def test_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LIFENG_CONFIG_DIR", str(tmp_path / "x"))
        assert S.config_dir() == tmp_path / "x"

    def test_config_file_env_wins_over_dir(self, monkeypatch, tmp_path):
        target = tmp_path / "custom.json"
        monkeypatch.setenv("LIFENG_CONFIG", str(target))
        assert S.config_path() == target

    def test_not_in_repo(self):
        """配置不能落在仓库里 —— 否则会被 git 提交，或拷给别人时带走密钥。"""
        path = S.config_path()
        assert Path.cwd() not in path.parents, f"配置落在了当前目录下：{path}"


class TestMasking:
    def test_masks_middle(self):
        s = S.Settings(api_key="sk-1234567890abcdef")
        masked = s.masked_key
        assert "sk-1" in masked
        assert "cdef" in masked
        assert "4567890abcde" not in masked, "掩码里泄露了中间部分"

    def test_short_key_fully_masked(self):
        assert S.Settings(api_key="short").masked_key == "*" * 5

    def test_empty(self):
        assert S.Settings().masked_key == "（未配置）"

    def test_never_returns_full_key(self):
        key = "sk-abcdefghijklmnop"
        assert key not in S.Settings(api_key=key).masked_key

    def test_redact_removes_key_from_text(self):
        s = S.Settings(api_key="sk-secret123")
        assert "sk-secret123" not in s.redact("请求头里有 sk-secret123 这个值")

    def test_redact_leaves_other_text_alone(self):
        s = S.Settings(api_key="sk-secret")
        assert s.redact("普通错误信息") == "普通错误信息"

    def test_redact_without_key(self):
        assert S.Settings().redact("原文") == "原文"


class TestRoundTrip:
    def test_save_then_load(self, cfg):
        S.save_settings(S.Settings(api_key="sk-x", base_url="http://local/v1", model="m"))
        loaded = S.load_settings()
        assert loaded.api_key == "sk-x"
        assert loaded.base_url == "http://local/v1"
        assert loaded.model == "m"

    def test_defaults_when_absent(self, cfg):
        loaded = S.load_settings()
        assert loaded.api_key == ""
        assert loaded.base_url == S.DEFAULT_BASE_URL
        assert loaded.model == S.DEFAULT_MODEL

    def test_corrupt_json_degrades(self, cfg):
        """配置文件损坏不该让应用起不来。"""
        S.config_path().write_text("{ 这不是 json", encoding="utf-8")
        loaded = S.load_settings()
        assert loaded.api_key == ""
        assert loaded.base_url == S.DEFAULT_BASE_URL

    def test_non_dict_json_degrades(self, cfg):
        S.config_path().write_text("[1,2,3]", encoding="utf-8")
        assert S.load_settings().api_key == ""


class TestPermissions:
    @pytest.mark.skipif(
        sys.platform.startswith("win"), reason="Windows 走 ACL，chmod 基本无效"
    )
    def test_file_is_owner_only(self, cfg):
        path = S.save_settings(S.Settings(api_key="sk-secret"))
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"权限是 {oct(mode)}，密钥文件应仅本人可读写"

    @pytest.mark.skipif(
        sys.platform.startswith("win"), reason="Windows 走 ACL，chmod 基本无效"
    )
    def test_rewrite_keeps_permission(self, cfg):
        S.save_settings(S.Settings(api_key="a"))
        path = S.save_settings(S.Settings(api_key="b"))
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_no_leftover_temp_file(self, cfg):
        S.save_settings(S.Settings(api_key="a"))
        assert not list(cfg.glob("*.tmp")), "临时文件没有清理掉"


class TestEnvPriority:
    def test_env_key_overrides_file(self, cfg, monkeypatch):
        """脚本化场景（CI、批处理）不该被界面里存过的值盖掉。"""
        S.save_settings(S.Settings(api_key="from-file"))
        monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
        assert S.load_settings().api_key == "from-env"

    def test_env_base_and_model(self, cfg, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://proxy/v1")
        monkeypatch.setenv("DEEPSEEK_MODEL", "my-model")
        loaded = S.load_settings()
        assert loaded.base_url == "http://proxy/v1"
        assert loaded.model == "my-model"

    def test_env_key_present(self, cfg, monkeypatch):
        assert not S.env_key_present()
        monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
        assert S.env_key_present()

    def test_blank_env_is_ignored(self, cfg, monkeypatch):
        S.save_settings(S.Settings(api_key="from-file"))
        monkeypatch.setenv("DEEPSEEK_API_KEY", "   ")
        assert S.load_settings().api_key == "from-file"


class TestConfiguredFlag:
    def test_configured(self):
        assert S.Settings(api_key="sk-x").configured
        assert not S.Settings().configured
        assert not S.Settings(api_key="   ").configured


class TestClientUsesSettings:
    def test_client_reads_saved_settings(self, cfg, monkeypatch):
        """核心诉求：界面里配好就能用，不必设环境变量。"""
        from lifeng.hint_engine import DeepSeekClient

        S.save_settings(S.Settings(api_key="sk-from-ui", base_url="http://x/v1"))
        client = DeepSeekClient()
        assert client.base_url == "http://x/v1"
        assert client.redact("sk-from-ui") == "***"

    def test_client_error_points_to_the_ui(self, cfg):
        from lifeng.hint_engine import DeepSeekClient, HintError

        with pytest.raises(HintError) as excinfo:
            DeepSeekClient()
        assert "模型设置" in str(excinfo.value), (
            "报错要告诉用户去哪儿配，而不是只说他没配"
        )

    def test_explicit_args_beat_saved(self, cfg):
        from lifeng.hint_engine import DeepSeekClient

        S.save_settings(S.Settings(api_key="saved", base_url="http://saved"))
        client = DeepSeekClient(api_key="explicit", base_url="http://explicit")
        assert client.base_url == "http://explicit"

    def test_redact_is_safe_without_key(self, cfg):
        from lifeng.hint_engine import DeepSeekClient

        S.save_settings(S.Settings(api_key="sk-x"))
        assert DeepSeekClient().redact("无关文本") == "无关文本"


class TestTestConnection:
    """测试连接：必须给出「能/不能」的明确结论，且不回显密钥。"""

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = TestTestConnection._Message(content)

    class _Response:
        def __init__(self, content):
            self.choices = [TestTestConnection._Choice(content)]

    class _Completions:
        def __init__(self, reply, boom):
            self._reply = reply
            self._boom = boom

        def create(self, **_kwargs):
            if self._boom:
                raise self._boom
            return TestTestConnection._Response(self._reply)

    def _client(self, cfg, reply=None, boom=None):
        from lifeng.hint_engine import DeepSeekClient

        S.save_settings(S.Settings(api_key="sk-ui"))
        client = DeepSeekClient()
        completions = self._Completions(reply, boom)
        client._client = type(
            "Fake",
            (),
            {"chat": type("Ch", (), {"completions": completions})()},
        )()
        return client

    def test_success(self, cfg):
        ok, message = self._client(cfg, reply="可用").test_connection()
        assert ok, message
        assert "可用" in message

    def test_empty_reply_still_succeeds(self, cfg):
        ok, message = self._client(cfg, reply=None).test_connection()
        assert ok
        assert "空回复" in message

    def test_failure_is_reported_not_raised(self, cfg):
        ok, message = self._client(cfg, boom=RuntimeError("401 Unauthorized")).test_connection()
        assert not ok and "401" in message

    def test_failure_message_is_redacted(self, cfg):
        """有些 SDK 会把请求头带进异常文本 —— 那里面就是密钥。"""
        ok, message = self._client(
            cfg, boom=RuntimeError("bad key sk-ui")
        ).test_connection()
        assert not ok
        assert "sk-ui" not in message, "错误信息里泄露了密钥"

    def test_never_echoes_the_key_on_success(self, cfg):
        ok, message = self._client(cfg, reply="好的").test_connection()
        assert ok and "sk-ui" not in message
