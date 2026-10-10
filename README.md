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
  models.py       领域模型 + 骨架 YAML schema 校验
  store.py        SQLite 会话日志 / 复习状态（纯读写）
  hint_engine.py  四层提示生成 + 结构性防泄漏
  leak_check.py   答案关键量黑名单校验
  views.py        四个页面的渲染 + 可离线测试的交互核心
  app.py          多页外壳（导航与状态）
  review.py       间隔复习调度（SM-2，移植自活书 learning-agent）
  mastery.py      掌握度模型（3PL IRT + 默认参数，移植自活书）
  psych.py        心理资源库（只做资源，不做检测）
  generator.py    AI 出题（照搬骨架 YAML 模板，带校验与重试）
  launcher.py     打包后的启动入口
  console.py      控制台编码处理（Windows cp1252 会崩，见文件注释）
problems/         p001.yaml ... 题目与骨架
docs/
  skeleton-format.md   骨架 YAML 规范（标注前必读）
  aegis/               Design Spec 与实施计划
tests/
```

### 架构纪律：存储后端只有 store.py 能碰

> **只有 `lifeng/store.py` 可以 `import sqlite3`。其余模块一律使用 `StoreError`。**

这不是洁癖 —— 本项目的定位是**本地优先，服务端排在全部功能最后**
（见 Design Spec ADR-001）。把 SQLite 关在一个文件里，将来真要换后端时
改动面就被限住了；否则迁移成本会悄悄扩散到界面与业务逻辑。

由 `tests/test_storage_boundary.py` 自动强制，违规即测试失败。
（该纪律已修正过一处违约：`app.py` 曾直接捕获 `sqlite3.Error`。）

---

## 四个页面

| 页面 | 学生来干什么 | 关键机制 |
|---|---|---|
| **训练** | 做题，卡住时逐层取提示 | 分层提示 + 结构性防泄漏；顶部显示用时/已用提示/尝试次数；做完点「我做出来了」进入复习计划 |
| **复习** | 看今天该重做哪些题 | SM-2 间隔调度；14 天复习量柱状图；按掌握度从弱到强排序 |
| **进度** | 看自己哪儿稳、哪儿生 | 掌握度条形图 + 明细表 + **提示层级分布**（这条比"做没做对"更能说明引导是否有效） |
| **心理** | 状态不对时找个具体做法 | 按「我现在什么状态」组织；含求助边界信号与指引 |

### 掌握度与间隔是怎么算的

- **掌握度**：3PL IRT 的更新式 + **默认参数**（不拟合）。
  单人数据无法标定项目参数，所以这里借用 IRT 的*形式*让掌握度随预测误差自适应移动，而不是声称它是被标定的测量值。
- **间隔**：SM-2 的间隔表 `1,3,7,14,30,60,120` 天。
- **判分不看对错，看「用了第几层提示」**：

  | 情况 | outcome | 下次间隔 |
  |---|---|---|
  | 无提示或第 1~2 层突破 | 1.0 | 前进一档 |
  | 第 3 层才突破 | 0.5 | 退一档 |
  | 看了答案 / 没解出 | 0.0 | 回炉到 1 天 |

  > 本项目里学生几乎总能改对，区别只在**需要多少帮助** —— 那才是掌握度的信号。

代码来源与改动说明见 [NOTICE.md](NOTICE.md)。

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

## 让 AI 出题（不必先手工标注）

不想先手标 20 题时，可以让 AI 出题 —— 它会照搬上面的 YAML 模板：

```bash
export DEEPSEEK_API_KEY=sk-...
.venv/bin/python -m lifeng.generator --count 5 --domain organic
```

生成过程带**三道校验**，不合格会带反馈重试（最多 3 次）：

1. **schema 校验** —— 字段完整性、枚举取值
2. **骨架不得含答案关键量** —— 骨架会被注入生成提示的 prompt，
   一旦写进答案，「模型看不到答案就说不出答案」这条结构性防线直接失效
3. **必须是推理型** —— 记忆型题不进引导流程（C2）

> ⚠️ **AI 出的骨架是另一个命题。** Design Spec 决策 4 是
> 「**人工**标注骨架 + LLM 措辞」，实验验证的是这个组合。
> AI 生成的题会标记 `skeleton_source: ai`，将来真做实验时才能区分。
>
> 折中做法：用生成器出**草稿**，再人工复核一遍 —— 比从零手写省时，
> 又保住了「人在环路里」这个前提。

---

## 数据安全

`store.db` 含未成年人答题记录：

- 已在 `.gitignore` 中排除，**不得提交入库**
- 只存答题文本与提示记录，不采集身份信息、不采集情绪/心理数据
- 实验结束后按约定删除

执行实验前请先落实 `docs/aegis/plans/` 中的 §14 合规前置项。

---

## 打包成可执行文件

用 PyInstaller（onedir 模式，非 onefile —— 本应用依赖体积大，
onefile 每次启动都要解压数百 MB 到临时目录）。

```bash
.venv/bin/pip install pyinstaller
.venv/bin/python -m PyInstaller lifeng.spec --noconfirm --clean
cd dist/lifeng && ./lifeng        # Windows 上是 lifeng.exe
```

打包版也带 **AI 出题**子命令 —— 否则用户还得另装 Python 才能出题：

```bash
./lifeng generate --count 5 --domain organic    # Windows: lifeng.exe generate ...
```

产物是 `dist/lifeng/` 整个目录（约 320MB，因为 Streamlit 硬依赖
pyarrow/pandas/numpy）。**分发时要打包整个目录**，不能只拷可执行文件。

### 平台支持

| 平台 | 支持 | 说明 |
|---|---|---|
| Windows / Linux / macOS | ✅ | 由 `.github/workflows/build.yml` 在三个平台原生构建；推 `v*` tag 自动发 Release |
| **Android** | ❌ **不打包，也不需要** | Streamlit 是**服务端 Web 框架**，界面由浏览器渲染、Python 服务端通过 websocket 驱动。`python-for-android`/`buildozer` 打的是 Kivy（另一套 GUI 工具包），无法打包 Streamlit。所谓"Streamlit 安卓方案"都是 **WebView 壳指向一个已运行的服务器**，并没有把应用装进去。<br>**正确用法**：学生在安卓浏览器里打开你的局域网地址即可 —— 实测 Streamlit 默认绑定所有网卡。 |

> PyInstaller 产物**不做跨平台交叉编译**：Windows 的 exe 必须在 Windows 上构建。
> 这正是 CI 用三平台矩阵的原因。

---

## 已知局限

`leak_check` 是字符串黑名单，**拦不住改述型泄漏**
（把"频哪酮"说成"那个叔丁基酮"）。
阶段一（20 题）的应对是实验者逐条人工复核全部提示。
阶段二若扩大规模，此处是明确的技术债。
