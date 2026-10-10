# 砺锋 v2

化学竞赛生的**苏格拉底式分层引导陪练** —— 阶段一验证实验工具。

> 本仓库当前只服务于一个命题的验证：
> **「人工标注骨架 + LLM 措辞」能否稳定产出不泄答案、且学生确实推进的提示？**
> 命题不成立则整个方向重估。相关决策见 `docs/aegis/`。

---

## 这是什么（以及不是什么）

**是**：一个能让学生看题、写下尝试、逐层取提示的最小工具，并把每次交互落库。

**不是**（阶段一刻意不做）：账号体系、多租户、付费、题库后台、
自动卡点判定、生产级部署、任何心理/情绪模块。

---

## 快速开始

```bash
cd ~/ZSH/lifeng

# 1. 建环境
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 配置模型
export DEEPSEEK_API_KEY=sk-...

# 3. 校验题库
.venv/bin/python -m lifeng.models problems/

# 4. 跑测试
.venv/bin/python -m pytest tests/ -q

# 5. 启动界面
.venv/bin/streamlit run lifeng/app.py
```

界面打开后：左侧填**学生代号**、选**模式**（实验组/基线组）与**题号**，
主区域写尝试、按需请求提示。

---

## 目录结构

```
lifeng/
  models.py       领域模型 + 骨架 YAML schema 校验（T1）
  store.py        SQLite 会话日志（T2）
  hint_engine.py  四层提示生成 + 结构性防泄漏（T3）
  leak_check.py   答案关键量黑名单校验（T4）
  app.py          Streamlit 学生界面（T5）
problems/         p001.yaml ... 题目与骨架
docs/
  skeleton-format.md   骨架 YAML 规范（标注前必读）
  aegis/               Design Spec 与实施计划
tests/
```

### 架构纪律：存储后端只有 store.py 能碰

> **只有 `lifeng/store.py` 可以 `import sqlite3`。其余模块一律使用 `StoreError`。**

这不是洁癖 —— 本项目的定位是**先本地、之后再考虑服务器**（见 Design Spec ADR-001）。
把 SQLite 关在一个文件里，将来换后端时改动面就被限住了；
否则迁移成本会悄悄扩散到界面与业务逻辑。

由 `tests/test_storage_boundary.py` 自动强制，违规即测试失败。
（该纪律已修正过一处违约：`app.py` 曾直接捕获 `sqlite3.Error`。）

---

## 四条硬约束

| 约束 | 含义 | 实现位置 |
|---|---|---|
| **C1** | 第 1~3 层提示不得出现答案关键量 | `hint_engine`（结构性：prompt 里根本不含答案）+ `leak_check`（黑名单兜底） |
| **C2** | 记忆型题目不进引导流程 | `models.Problem.hintable` + `hint_engine` 强制拒绝 |
| **C3** | 核心指标是**推进率**，不是"引导得像不像" | 由实验者人工回填 `attempts.progressed` |
| **C4** | 按未成年人标准处理数据 | 只存答题文本；`store.db` 已 gitignore |

---

## 标注新题

读 `docs/skeleton-format.md`，然后：

```bash
cp problems/p001.yaml problems/p002.yaml   # 照着改
.venv/bin/python -m lifeng.models problems/   # 校验
```

标注完成后**务必**检查骨架卫生 —— 骨架里若写进了答案，
结构性防泄漏就失效了：

```python
from lifeng.models import load_problems
from lifeng.leak_check import skeleton_leaks_answer
for p in load_problems("problems"):
    if leaks := skeleton_leaks_answer(p):
        print(p.id, "骨架泄漏:", leaks)
```

---

## 数据安全

`store.db` 含未成年人答题记录：

- 已在 `.gitignore` 中排除，**不得提交入库**
- 只存答题文本与提示记录，不采集身份信息、不采集情绪/心理数据
- 实验结束后按约定删除

执行实验前请先落实 `docs/aegis/plans/` 中的 §14 合规前置项。

---

## 已知局限

`leak_check` 是字符串黑名单，**拦不住改述型泄漏**
（把"频哪酮"说成"那个叔丁基酮"）。
阶段一（20 题）的应对是实验者逐条人工复核全部提示。
阶段二若扩大规模，此处是明确的技术债。
