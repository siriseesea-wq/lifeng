"""砺锋 v2 —— 打包后的启动入口（PyInstaller）。

为什么需要这个文件：Streamlit 的**静态前端与包元数据**必须被显式收集，
冻结后不能直接依赖 ``streamlit run`` 的默认路径查找。本模块负责
解析资源位置并调用 Streamlit 的 CLI。

源码运行时：``python -m lifeng.launcher``
打包后：由 PyInstaller 生成的 ``lifeng`` 可执行文件调用 :func:`main`。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _resource_root() -> Path:
    """冻结后为 PyInstaller 的解包目录；源码运行时为项目根。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def _app_script(root: Path) -> Path:
    """界面脚本位置。

    打包时 ``lifeng/app.py`` 会额外以**数据文件**形式放在 ``app/`` 下
    （Streamlit 需要一个文件路径，而包内模块没有可用的真实路径）。
    """
    if getattr(sys, "frozen", False):
        return root / "app" / "app.py"
    return root / "lifeng" / "app.py"


def _problems_dir(root: Path) -> Path:
    """题库目录：**优先用当前工作目录下的**，便于老师自己加题。

    打包内置的那份只是样例，不应覆盖用户自己的题库。
    """
    cwd_problems = Path.cwd() / "problems"
    if cwd_problems.is_dir():
        return cwd_problems
    return root / "problems"


def _ensure_streamlit_credentials() -> bool:
    """跳过 Streamlit 首次运行的邮箱询问。

    不写这个文件时，首次启动会在 stdin 上等待用户输入邮箱。
    打包成可执行文件后，这表现为**双击没反应**（窗口一片空白，实际在等输入）。
    写入空邮箱等价于用户直接回车。

    返回是否可用；写不进去时调用方会退化为 ``--server.headless=true``。
    """
    cred = Path.home() / ".streamlit" / "credentials.toml"
    if cred.exists():
        return True
    try:
        cred.parent.mkdir(parents=True, exist_ok=True)
        cred.write_text('[general]\nemail = ""\n', encoding="utf-8")
        return True
    except OSError:
        return False


def main() -> int:
    # 必须先做：本函数的 banner 含中文，Windows 非中文控制台上会直接崩溃
    from lifeng.console import make_output_safe

    make_output_safe()

    # 显式 import，确保 PyInstaller 把整包收进去
    import lifeng  # noqa: F401
    from streamlit.web import cli as stcli

    root = _resource_root()

    # 子命令分发：打包后没有 `python -m lifeng.generator` 可用，
    # 若不分发，用户就得另装 Python 才能出题 —— 那 MVP 就缺了一块。
    #   lifeng generate --count 5 --domain organic
    if len(sys.argv) > 1 and sys.argv[1] in ("generate", "gen"):
        from lifeng.generator import main as generator_main

        os.environ.setdefault("LIFENG_PROBLEMS", str(_problems_dir(root)))
        return generator_main(["lifeng-generate", *sys.argv[2:]])

    script = _app_script(root)

    if not script.is_file():
        print(f"[砺锋] 找不到界面文件：{script}", file=sys.stderr)
        return 1

    problems = _problems_dir(root)
    os.environ.setdefault("LIFENG_PROBLEMS", str(problems))
    # 数据落在**运行目录**，而不是解包临时目录 —— 否则每次启动都丢记录
    os.environ.setdefault("LIFENG_DB", str(Path.cwd() / "store.db"))

    print(f"[砺锋] 题库目录：{problems}")
    print(f"[砺锋] 数据文件：{os.environ['LIFENG_DB']}")
    print("[砺锋] 启动界面中……浏览器会自动打开。按 Ctrl+C 结束。")

    args = [
        "streamlit",
        "run",
        str(script),
        "--global.developmentMode=false",
        "--browser.gatherUsageStats=false",
    ]
    if not _ensure_streamlit_credentials():
        # 写不进凭据文件（只读 home 等）时，headless 同样能跳过邮箱询问，
        # 代价是不会自动开浏览器。
        print("[砺锋] 无法写入 Streamlit 凭据，改用 headless 模式，请手动打开提示的网址。")
        args.append("--server.headless=true")

    sys.argv = args
    return stcli.main()


if __name__ == "__main__":
    raise SystemExit(main())
