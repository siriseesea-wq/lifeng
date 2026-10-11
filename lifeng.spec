# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（砺锋 v2）。

要点：
1. ``collect_all("streamlit")`` —— Streamlit 的静态前端与包元数据
   不会被自动发现，必须显式收集，否则冻结后界面白屏或启动即报错。
2. ``lifeng/app.py`` 额外以**数据文件**放进 ``app/`` ——
   Streamlit 需要一个真实文件路径，包内模块没有。
3. ``problems/`` 打进包里作为样例题库；用户自己放在运行目录下的
   ``problems/`` 优先级更高（见 launcher.py）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# SPECPATH 本身就是本 spec 文件所在目录（即项目根），不要再 .parent
ROOT = Path(SPECPATH).resolve()

# Streamlit 及其依赖的资源、元数据、隐藏导入
st_datas, st_binaries, st_hiddenimports = collect_all("streamlit")

datas = [
    *st_datas,
    (str(ROOT / "lifeng" / "app.py"), "app"),
    (str(ROOT / "problems"), "problems"),
    # 知识点图与示例资料也必须打进包 —— 少了它们，「图谱」「画像」
    # 两个页面和训练页的「查资料」在打包版里会直接失效。
    (str(ROOT / "concepts.yaml"), "."),
    (str(ROOT / "materials"), "materials"),
]

hiddenimports = [
    *st_hiddenimports,
    "streamlit.web.cli",
    "lifeng",
    "lifeng.models",
    "lifeng.store",
    "lifeng.hint_engine",
    "lifeng.leak_check",
    "lifeng.review",
    "lifeng.mastery",
    "lifeng.concepts",
    "lifeng.prompts",
    "lifeng.settings",
    "lifeng.profile",
    "lifeng.materials",
    "lifeng.planner",
    "lifeng.views",
    "lifeng.app",
]

a = Analysis(
    [str(ROOT / "lifeng" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[*st_binaries],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lifeng",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=False —— 双击时**不再弹出黑色命令行窗口**。
    #
    # 这是「图形化」的第一步，而且是最关键的一步：之前双击 lifeng.exe
    # 会先弹一个黑框再开窗口，在普通人眼里那就是一个命令行程序，
    # 界面做得再漂亮也没用。
    #
    # 代价是 stdout 没了，所以诊断信息改由 launcher 的 log() 写进
    # lifeng.log，启动失败则用 fatal() 弹原生对话框 ——
    # 去控制台与补诊断必须一起做，只做前者等于把问题藏起来。
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# 命令行入口单独一个可执行文件。
#
# `lifeng generate --count 5` 需要把进度打到终端上，而窗口模式的 exe
# 没有 stdout，print 会直接抛异常。所以保留一个带控制台的版本。
# 两个 exe 共用同一份依赖，不会让产物变大多少。
exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lifeng-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="lifeng",
)
