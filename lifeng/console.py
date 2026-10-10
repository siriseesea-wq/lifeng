"""控制台输出编码处理。

**问题**：Windows 控制台与重定向输出的默认编码是 cp1252（英文系统）或
cp936（中文系统），无法编码全部中文。直接 ``print`` 中文会抛
``UnicodeEncodeError`` 并让程序整体崩溃。

**已证实**：
- GitHub Actions ``windows-latest``（CI run 38060571646）上
  ``python -m lifeng.models problems/`` 崩溃 —— 真实发生
- 本地用 ``PYTHONIOENCODING=ascii`` 可确定性复现
- ``launcher`` 的 banner 含中文且是同样的裸 ``print`` 模式，
  在源码模式下同样会崩（已复现）

**未证实（不要当成结论）**：
- **打包产物是否也受害，目前没有证据。** 实测 PyInstaller 的 bootloader
  会重置 IO 编码 —— 给冻结产物设 ``PYTHONIOENCODING=no-such-codec``
  它照常运行（源码模式则会报错），因此用该变量做的验证
  **根本没有走到冻结产物的编码路径**。本机是 Linux，无法在 Windows 上实测。

**修法**：把 ``stdout``/``stderr`` 的错误处理改为 ``replace``。
编码保持不变（中文 Windows 仍正确显示中文），只在无法编码时
输出替代字符，而不是让整个程序挂掉。对已证实的源码路径是必需修复；
对冻结产物是无害的防御。

> 注：单元测试在 Windows 上是**通过**的 —— 因为测试不往控制台打中文。
> 这个坑只在真实 CLI 调用时暴露，正是需要三平台 CI 的原因。
"""

from __future__ import annotations

import sys


def make_output_safe() -> None:
    """让控制台输出在无法编码某些字符时降级，而不是抛出异常。

    幂等；对不支持 ``reconfigure`` 的流（如已被重定向到某些对象）静默跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError, AttributeError):
            # 例如流已关闭，或实现不支持该参数
            continue
