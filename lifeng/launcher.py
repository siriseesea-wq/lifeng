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
import threading
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


def _cwd_or_bundled(root: Path, name: str) -> Path:
    """与题库同样的规则：运行目录下的优先，否则用打包内置的。

    知识点图和资料库都要走这条路径 —— 少了它们，「图谱」「画像」
    两个页面和训练页的「查资料」在打包版里会失效。
    """
    cwd_path = Path.cwd() / name
    if cwd_path.exists():
        return cwd_path
    return root / name


# --------------------------------------------------------------------------
# 日志与致命错误提示
#
# 打包成**窗口模式**（console=False）后双击不再弹黑框 —— 那正是「图形化」
# 的第一步。但代价是 stdout 没了，原来的诊断信息全部消失。
# 所以这两件事必须一起做：**去掉控制台，同时把诊断接到日志文件与弹窗上**。
# 只去掉控制台而不补日志，等于把问题藏起来。
# --------------------------------------------------------------------------


def log_path() -> Path:
    """日志文件位置：运行目录下，名字固定，方便让老师发过来。"""
    return Path.cwd() / "lifeng.log"


def log(message: str) -> None:
    """写日志。

    有 stdout 时也打印 —— 从源码运行时仍能直接在终端看到。
    窗口模式下 ``sys.stdout`` 是 ``None`` 或不可写，**直接 ``print`` 会抛异常**，
    这是打包成无控制台后最常见的崩溃来源。
    """
    line = message if message.endswith("\n") else message + "\n"
    try:
        with log_path().open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass

    stream = sys.stdout
    if stream is not None:
        try:
            stream.write(line)
            stream.flush()
        except (OSError, ValueError, AttributeError):
            pass


def ensure_standard_streams() -> None:
    """保证 ``sys.stdout`` / ``sys.stderr`` 可用。

    **这必须在任何别的库之前调用。** Windows 上的窗口模式
    （``console=False``）会让这两个流变成 ``None`` —— 任何
    ``print`` 或日志写入都会抛 ``AttributeError``。

    Linux 上 ``console=False`` 不改变流（可执行文件本身相同），
    所以这个问题**只会在 Windows 真机上出现**，本机测不出来。
    把流接到日志文件上，两个平台就都安全了。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return

    try:
        sink = log_path().open("a", encoding="utf-8", errors="replace")
    except OSError:
        # 连日志都开不了就退回黑洞，至少不要让库崩
        class _Null:
            def write(self, _data: str) -> int:
                return 0

            def flush(self) -> None:
                pass

            def isatty(self) -> bool:
                return False

        sink = _Null()  # type: ignore[assignment]

    if sys.stdout is None:
        sys.stdout = sink  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = sink  # type: ignore[assignment]


def dialogs_enabled() -> bool:
    """当前环境是否适合弹**模态**对话框。

    模态对话框会一直阻塞到有人点掉。在没有人的环境里弹，就是永久挂起 ——
    实测：Windows 的 MessageBoxW 与 macOS 的 tkinter showerror
    把整条 CI 流水线挂住了 17 分钟以上，直到手工取消。

    所以用**显式信号**判断，而不是「有没有 tty」：
    真正的图形化双击场景同样没有 tty，但那时正需要弹窗。
    """
    if os.environ.get("LIFENG_NO_DIALOG"):
        return False
    if os.environ.get("CI"):
        # GitHub Actions 与绝大多数 CI 都会设置这个
        return False
    return True


def show_dialog(message: str) -> bool:
    """弹一个原生错误对话框。返回是否成功弹出。

    **这是模态的** —— 会阻塞到有人点掉。只应由 :func:`fatal` 在
    确认环境里有人时调用。
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                f"{message}\n\n详细信息见：\n{log_path()}",
                "砺锋 · 启动失败",
                0x10,  # MB_ICONERROR
            )
            return True
        except Exception:  # noqa: BLE001
            pass

    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(
            "砺锋 · 启动失败", f"{message}\n\n详细信息见：{log_path()}"
        )
        root.destroy()
        return True
    except Exception:  # noqa: BLE001
        return False


def fatal(message: str, *, dialog: bool | None = None) -> None:
    """报告启动期的致命错误。

    窗口模式下没有控制台能显示它 —— 用户只会看到「双击没反应」，
    所以额外弹一个原生对话框。

    ``dialog=None`` 时按 :func:`dialogs_enabled` 自动判断。
    **无论弹不弹，日志里一定留下记录。**
    """
    log(f"[砺锋] 错误：{message}")

    if dialog is None:
        dialog = dialogs_enabled()
    if dialog:
        show_dialog(message)


# --------------------------------------------------------------------------
# 以「独立窗口」打开，而不是浏览器标签页
# --------------------------------------------------------------------------

#: 各平台上 Chromium 系浏览器的常见位置与可执行名。
#:
#: 为什么要找这类浏览器：只有它们支持 ``--app=`` —— 打开的是**没有地址栏、
#: 没有标签页**的独立窗口，看起来就是一个桌面程序。默认浏览器
#: （Firefox / Safari）没有等价能力，退化为普通标签页。
_CHROMIUM_CANDIDATES: dict[str, tuple[str, ...]] = {
    "win32": (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "msedge",
        "chrome",
    ),
    "darwin": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ),
    "linux": (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "brave-browser",
    ),
}


def _find_chromium() -> str | None:
    """找一个 Chromium 系浏览器；找不到返回 ``None``。"""
    import shutil

    key = "win32" if sys.platform.startswith("win") else sys.platform
    if key.startswith("linux"):
        key = "linux"
    elif key.startswith("darwin"):
        key = "darwin"

    for candidate in _CHROMIUM_CANDIDATES.get(key, ()):
        if os.path.isabs(candidate):
            if Path(candidate).is_file():
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def open_browser_window(url: str) -> str:
    """打开界面，优先用**独立窗口**。

    返回实际采用的方式，供日志显示。

    刻意不抛异常：打不开窗口不该让整个程序挂掉 —— 服务已经起来了，
    用户至少还能手动打开网址。
    """
    import subprocess
    import webbrowser

    if os.environ.get("LIFENG_NO_BROWSER"):
        # 无头部署、以及在 CI 里跑「启动冒烟测试」时需要关掉 ——
        # 否则会凭空拉起一个浏览器进程（CI runner 上确实装着 chrome）。
        return "未打开（LIFENG_NO_BROWSER 已设置）"

    browser = _find_chromium()
    if browser:
        try:
            creationflags = 0
            if sys.platform.startswith("win"):
                # Windows 上不加这个会连带弹出一个控制台窗口
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                [
                    browser,
                    f"--app={url}",
                    "--window-size=1280,900",
                    # 用独立的用户数据目录，避免和用户日常浏览器抢配置文件，
                    # 也避免"已有一个实例在运行"时 --app 被当成新标签页处理
                    f"--user-data-dir={Path.home() / '.lifeng-browser'}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            return f"独立窗口（{Path(browser).name}）"
        except OSError:
            pass  # 落到下面的默认浏览器

    try:
        if webbrowser.open(url):
            return "浏览器标签页（未找到 Chromium 系浏览器，退化为标签页）"
    except Exception:  # noqa: BLE001
        pass
    return "未能自动打开，请手动访问上面的网址"


def _wait_and_open(url: str, health_url: str, timeout: float = 60.0) -> None:
    """等健康检查通过再开窗口 —— 否则会先看到一个"无法连接"的页面。"""
    import time
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as response:
                if response.status == 200:
                    break
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.4)
    else:
        return  # 超时就不开了，Streamlit 自己会打印网址

    how = open_browser_window(url)
    log(f"[砺锋] 已在{how}中打开。")


def _free_port() -> int:
    """要一个空闲端口。

    固定用 8501 会在端口被占时让 Streamlit 换成别的端口，
    而我们拼的 URL 还停在 8501 —— 窗口打开就是「无法连接」。
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
    # 必须在导入 streamlit 之前 —— 它一被导入就会配置日志输出
    ensure_standard_streams()

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
        fatal(f"找不到界面文件：{script}")
        return 1

    problems = _problems_dir(root)
    os.environ.setdefault("LIFENG_PROBLEMS", str(problems))
    # 知识点图与资料库同样要指向可用位置，否则打包版里
    # 「图谱」「画像」和「查资料」会静默失效
    os.environ.setdefault("LIFENG_CONCEPTS", str(_cwd_or_bundled(root, "concepts.yaml")))
    os.environ.setdefault("LIFENG_MATERIALS", str(_cwd_or_bundled(root, "materials")))
    # 数据落在**运行目录**，而不是解包临时目录 —— 否则每次启动都丢记录
    os.environ.setdefault("LIFENG_DB", str(Path.cwd() / "store.db"))

    log(f"[砺锋] 题库目录：{problems}")
    log(f"[砺锋] 数据文件：{os.environ['LIFENG_DB']}")

    # 端口自己选并固定下来。
    #
    # 用默认 8501 时，若端口被占 Streamlit 会悄悄换一个，
    # 而我们拼的 URL 还停在 8501 —— 打开窗口看到的是「无法连接」。
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    health = f"{url}/_stcore/health"

    args = [
        "streamlit",
        "run",
        str(script),
        "--global.developmentMode=false",
        "--browser.gatherUsageStats=false",
        # 始终 headless：浏览器由我们自己开。
        #
        # Streamlit 自己开的是**标签页**（地址栏、标签栏一应俱全），
        # 而现在要的是没有这些的独立窗口 —— 让它同时也开一个，
        # 用户会看到两个界面。
        "--server.headless=true",
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        # 主题必须在这里传，不能只靠仓库里的 .streamlit/config.toml：
        # Streamlit 从**当前工作目录**找那个文件，而打包版是在用户
        # 自己的目录里运行的，那里没有这个文件 —— 不传的话，
        # 打包版的选中态/焦点环会退回 Streamlit 默认的红色，
        # 和蓝色品牌打架。配色与 lifeng/theme.py 保持一致。
        "--theme.primaryColor=#2456E6",
        "--theme.backgroundColor=#FFFFFF",
        "--theme.secondaryBackgroundColor=#F6F8FC",
        "--theme.textColor=#0F172A",
    ]

    if not _ensure_streamlit_credentials():
        # 凭据写不进去时，headless 之外的路径都不通；此时仍然只能靠用户
        # 自己打开网址，所以把网址打印得显眼一些。
        log("[砺锋] 无法写入 Streamlit 凭据，请手动打开下面的网址。")

    log(f"[砺锋] 界面地址：{url}")
    log("[砺锋] 正在启动，就绪后会自动打开窗口。")

    # 等健康检查通过再开窗口 —— 不等的话先看到一个"无法连接"的页面，
    # 用户会以为程序坏了。放在后台线程里，因为 stcli.main() 是阻塞的。
    threading.Thread(
        target=_wait_and_open, args=(url, health), daemon=True
    ).start()

    sys.argv = args
    return stcli.main()


if __name__ == "__main__":
    raise SystemExit(main())
