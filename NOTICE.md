# 第三方代码归属声明

本项目的部分代码移植自其它开源项目。按各自许可证要求，列出出处与原始版权。

---

## 活书 learning-agent

- **出处**：PyPI 包 `learning-agent`（活书 huoshu）
- **原始版权**：Copyright (c) 2026 KK
- **许可证**：MIT
- **移植内容**：

  | 本项目文件 | 来源文件 | 改动 |
  |---|---|---|
  | `lifeng/review.py` | `learning_agent/core/scheduler.py` | 复习单元由「知识点」改为「题目」；**修正了原实现的一处缺陷**（见下）；掌握度更新改由 `mastery.py` 统一负责 |
  | `lifeng/mastery.py` | `learning_agent/core/mastery.py` | 去掉与本项目无关的知识点类型/难度表；保留 3PL 更新式与调整因子 |

### 移植时修正的缺陷

原 `scheduler.py` 用**复习次数**定位间隔档位：

```python
idx = min(review_count + 1, len(DEFAULT_INTERVALS) - 1)
```

后果是：学生答错后间隔回炉到 1 天，但只要再答对一次，档位仍按**累计次数**
往前冲 —— 累计 6 次的话直接跳到 120 天，等于把回炉白做了。

本项目改为由**上次间隔**推导档位，使回炉同时复位档位。
回归测试见 `tests/test_review.py::TestNextInterval::test_ladder_restarts_after_failure`。

### MIT 许可证原文

```
MIT License

Copyright (c) 2026 KK

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 参考但未移植代码的项目

以下项目在设计阶段被查阅，但**没有复制其代码**，因此不含许可证义务。
列出以说明来源：

| 项目 | 参考了什么 |
|---|---|
| DeepTutor（HKUDS，Apache-2.0） | 多 Agent / 长期记忆 / RAG 的架构思路。本项目**未采用**其多 Agent 编排 —— 理由见 `docs/aegis/specs/` |
| OI.SKILL（FlashingChen，MIT） | 苏格拉底式提示的分层措辞思路 |

> **说明**：本项目的骨架标注体系、分层提示引擎、答案泄漏校验、
> 进度可视化均为自行实现，未从上述项目复制代码。
