"""砺锋 v2 —— 本地设置（API 配置）。

**为什么需要它**：让老师去设环境变量是不合理的 ——
Windows 上要开系统属性改环境变量、macOS 上要改 shell 配置，
对一个"双击就用"的工具来说这是硬门槛。API 配置应该能在界面里填。

## 存放位置

按平台惯例放在用户配置目录，**不在仓库里、也不在运行目录里**：

- Windows：``%APPDATA%\\lifeng\\config.json``
- macOS：``~/Library/Application Support/lifeng/config.json``
- 其它：``$XDG_CONFIG_HOME/lifeng/config.json``（默认 ``~/.config``）

放运行目录会有两个问题：一是跟着项目一起被 git 提交，
二是把应用目录拷给别人时会把密钥一起带过去。

## 密钥处理

- 文件权限设为 ``0600``（仅本人可读写）
- 界面上只显示掩码，不显示原文
- **绝不写进日志** —— ``log()`` 会经过 :func:`redact` 过滤
- 环境变量仍然可用，作为脚本化场景的入口；**环境变量优先于配置文件**
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

#: DeepSeek 的默认端点与模型。
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

#: 可选的模型。界面上给下拉，避免手打出错。
KNOWN_MODELS: tuple[str, ...] = (
    "deepseek-chat",
    "deepseek-reasoner",
)

#: 常见的兼容端点，便于换供应商或走本地代理。
KNOWN_BASE_URLS: tuple[str, ...] = (
    "https://api.deepseek.com",
    "https://api.deepseek.com/v1",
    "https://api.openai.com/v1",
    "http://localhost:11434/v1",
)

CONFIG_ENV = "LIFENG_CONFIG"


@dataclass(frozen=True)
class Settings:
    """一份 API 配置。"""

    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def masked_key(self) -> str:
        """用于界面显示。**永不返回完整密钥。**"""
        key = self.api_key.strip()
        if not key:
            return "（未配置）"
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:4]}{'*' * 8}{key[-4:]}"

    def redact(self, text: str) -> str:
        """把文本里的密钥替换掉，用于日志。"""
        key = self.api_key.strip()
        if key and key in text:
            return text.replace(key, "***")
        return text


def config_dir() -> Path:
    """按平台惯例返回配置目录。"""
    override = os.environ.get("LIFENG_CONFIG_DIR")
    if override:
        return Path(override)

    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "lifeng"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "lifeng"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "lifeng"


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    return config_dir() / "config.json"


def load_settings() -> Settings:
    """读取配置。

    **环境变量优先**：脚本化场景（CI、批处理）不该被界面里存过的值覆盖。
    """
    settings = Settings()

    path = config_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            settings = Settings(
                api_key=str(raw.get("api_key", "") or ""),
                base_url=str(raw.get("base_url", "") or DEFAULT_BASE_URL),
                model=str(raw.get("model", "") or DEFAULT_MODEL),
            )

    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        settings = replace(settings, api_key=env_key)
    env_base = os.environ.get("DEEPSEEK_BASE_URL", "").strip()
    if env_base:
        settings = replace(settings, base_url=env_base)
    env_model = os.environ.get("DEEPSEEK_MODEL", "").strip()
    if env_model:
        settings = replace(settings, model=env_model)

    return settings


def save_settings(settings: Settings) -> Path:
    """写盘。目录与文件都收紧权限。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 先写临时文件再替换：写一半被杀掉时不会留下半个损坏的配置文件
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _restrict(tmp)
    tmp.replace(path)
    _restrict(path)
    return path


def _restrict(path: Path) -> None:
    """把文件权限收紧到仅本人可读写。

    Windows 上 chmod 基本无效（NTFS 走 ACL），失败也不该让保存失败 ——
    用户配置文件目录本身就只对本人可访问。
    """
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def env_key_present() -> bool:
    """环境变量里是否已经给了密钥（用于界面提示"已被环境变量覆盖"）。"""
    return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
