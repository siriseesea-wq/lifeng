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
  | `lifeng/concepts.py` | `learning_agent/core/graph.py` | 取其**核心形状**（知识点 + 前置依赖 + 相关概念 + 加载时校验）。原版那份是 682 行的完整教材图谱（章节簇、教材锚点、跨项目引用、JSON 导入导出），本项目只保留题库场景需要的部分，并改为 YAML 以与本项目其余 schema 一致。**新增了前置依赖成环检测** —— 成环意味着「学 A 要先学 B，学 B 要先学 A」，学生永远无法开始 |
  | `lifeng/materials.py` | `learning_agent/rag/` （思路）| **未复制代码**。原版用 chromadb + ollama 做向量检索；本项目改用纯标准库的 BM25。理由见下方说明 |

### 移植时修正的缺陷

原 `scheduler.py` 用**复习次数**定位间隔档位：

```python
idx = min(review_count + 1, len(DEFAULT_INTERVALS) - 1)
```

后果是：学生答错后间隔回炉到 1 天，但只要再答对一次，档位仍按**累计次数**
往前冲 —— 累计 6 次的话直接跳到 120 天，等于把回炉白做了。

本项目改为由**上次间隔**推导档位，使回炉同时复位档位。
回归测试见 `tests/test_review.py::TestNextInterval::test_ladder_restarts_after_failure`。

### 为什么检索不用向量库

活书的 `rag/` 用 chromadb + ollama。本项目改用纯标准库的 BM25，理由是：

- **规模不匹配**：题库是几十道题、资料是几篇笔记。这个量级下
  向量检索的收益抵不过它的代价（chromadb 会显著撑大已经 300MB+ 的打包体积，
  ollama 还要求用户额外装一个模型运行时）
- **要能离线**：本项目的定位是「我和我带的几个学生本地用」，
  用户不该为了查一段讲义再装一个 embedding 服务
- **BM25 够用**：实测 10 道题全部检索到正确段落，且正确段落
  领先第二名一倍以上。真到了几千篇的规模再换，那时换才有意义

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
