# 砺锋 v2

化学竞赛生的**苏格拉底式分层引导陪练** —— 阶段一验证实验工具。

> 本仓库当前只服务于一个命题的验证：
> **「人工标注骨架 + LLM 措辞」能否稳定产出不泄答案、且学生确实推进的提示？**
> 命题不成立则整个方向重估。相关决策见 `docs/aegis/`。

---

## 这是什么（以及不是什么）

**砺锋**是一个本地运行的工具，陪化学竞赛学生做题：学生卡住时，
它用**分层提问**把学生引到正确的思路上，而不是直接给答案。

**没有预置题库。** 题目由 AI 生成，存在你本机的 `problems/` 目录里。
首次启动时打开的是「出题」页 —— 生成几道，然后就可以开始训练。

**它不是什么**：不是在线服务，不是题库，也不是课堂管理系统。
数据全部留在本机。

**是**：一个能让学生看题、写下尝试、逐层取提示的工具，
并把每次交互落库，据此安排复习与生成画像。

**不是**（刻意不做）：账号体系、多租户、付费、生产级部署、
**情绪检测或心理判断**（心理页只提供自助资料与求助指引，不做检测）。

---

## 提示词就是产品

本项目不预置题库：**题目由 AI 生成，引导提示也由 AI 生成**。
也就是说 prompt 的质量直接等于产品的质量。所以它们单独成模块：
[`lifeng/prompts.py`](lifeng/prompts.py) —— 改一句话就改行为，
不需要在几个文件之间跳。

### 出题提示词守住的东西

写在提示词里的每一条约束，都对应一种**实际会出的坏结果**：

| 约束 | 不守会怎样 |
|---|---|
| 答案必须唯一 | 学生给出化学上成立的另一个答案，系统判他错；自动判分与复习调度全部失效 |
| 条件必须自足 | 出现「已知 Ka 如下」时模型会编造数值，学生无从核对 |
| 必须是推理题 | 记忆型题做苏格拉底式提问是浪费时间 |
| 卡点互斥且可观察 | 无法从学生文字判断他卡在哪，统计失去意义 |
| `next_step` 只写动作 | 写出结论就等于把答案给了 |
| 骨架不含答案关键量 | 骨架会注入提示生成的 prompt，防线直接失效 |
| `concepts` 必须真实存在 | 写错的 id 不报错，只让图谱少一条连线 —— 静默失效 |

另外还有**难度档**（初赛 / 复赛 / 决赛）与**考点轮转**（不说的话模型会反复出
它最熟的那一类）。给模型的还有一份完整参考样例 ——
「必须可观察」是抽象的，一个真实例子是具体的。

### 提示生成提示词守住的东西

- **第 1~3 层的 prompt 里根本没有 `answer` 这个字符串** ——
  不是靠嘱咐模型"别说"，而是它想泄露也没有材料。
- **卡点按层渐进披露**：第 1 层只给「学生卡住时的表现」，
  第 3 层才给「越过卡点所需的动作」。披露得越少，能泄露的越少。
- **把已给过的提示带回去**，否则模型会原样重复，学生看到的是"又说了一遍"。

`tests/test_prompts.py` 把这些约束逐条钉住了。

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
  views.py        七个页面的渲染 + 可离线测试的交互核心
  app.py          多页外壳（导航与状态）
  planner.py      今日任务（系统主导排程）
  review.py       间隔复习调度（SM-2，移植自活书 learning-agent）
  mastery.py      掌握度模型（3PL IRT + 默认参数，移植自活书）
  concepts.py     知识点图谱（形状移植自活书 core/graph.py）
  profile.py      学生画像 + 卡点诊断（L3 综合判断）
  materials.py    资料库检索（BM25，覆盖 RAG 能力但不引向量库）
  psych.py        心理资源库（只做资源，不做检测）
  generator.py    AI 出题（照搬骨架 YAML 模板，带校验与重试）
  launcher.py     打包后的启动入口
  console.py      控制台编码处理（Windows cp1252 会崩，见文件注释）
problems/         p001.yaml ... 题目与骨架
concepts.yaml     知识点图（前置依赖 / 相关概念）
materials/        讲义或笔记（.md / .txt，自动索引）
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

## 界面

### 设计系统

配色与组件集中在 `lifeng/theme.py`，原生控件配色在 `.streamlit/config.toml`。

**为什么分成两处**：`config.toml` 管的是 Streamlit **原生控件**的颜色
（单选、焦点环、滑块）—— 这些由 Streamlit 渲染时注入，CSS 覆盖既脆弱又容易漏。
品牌自建组件（英雄区、指标卡、掌握度条）则由 `theme.py` 生成 HTML，
完全在我们控制之下。**两处的品牌色必须一致**，有测试钉住这一点。

**为什么自建组件用 HTML 而不是 CSS 覆盖 Streamlit**：Streamlit 内部 DOM
的类名跨版本会变（它不承诺公开）。把所有样式压在那些类名上，是
「今天好看、升级就崩」的做法。

**一个必须记住的坑**：`st.markdown` 走 CommonMark 解析，**遇到空行就终止
HTML 块**，之后的缩进行会被当成代码块原样显示 —— 症状是页面上直接出现
`<div class="lf-stat">` 这样的源码。所以组件产出的 HTML 必须过
`theme._compact()`。`tests/test_theme.py` 里有回归测试。

### 图表

用 **Altair**（Streamlit 自带，不增加依赖），比 `st.bar_chart` 能控制得多：
圆角、配色、百分比轴、tooltip。

知识点图谱用 Streamlit 内置的 `st.graphviz_chart`，**不需要 graphviz 包**。

**界面上没有 emoji**：Linux 环境常常没有 emoji 字体，会显示成豆腐块。
装饰性符号不值得让界面依赖一个可选字体。

### 七个页面

| 页面 | 学生来干什么 | 关键机制 |
|---|---|---|
| **今日** | **不用想今天做什么，直接开做** | 系统排任务并说清理由：到期复习 → 新题 → 补最弱。默认落地页 |
| **训练** | 做题，卡住时逐层取提示 | 分层提示 + 结构性防泄漏；用时/已用提示/尝试次数；可主动「查资料」检索讲义 |
| **复习** | 看今天该重做哪些题 | SM-2 间隔调度；14 天复习量柱状图；按掌握度从弱到强排序 |
| **进度** | 看自己哪儿稳、哪儿生 | 掌握度条形图 + 明细表 + **提示层级分布** |
| **图谱** | 看知识点之间的依赖关系 | Graphviz 渲染的知识点图：绿=已稳、红=薄弱、灰=未触及；实线=前置、虚线=相关 |
| **画像** | 看「我到底缺什么、下一步动哪块」 | L3 综合判断；概念级掌握度；**卡点诊断**（总卡在哪一类） |
| **心理** | 状态不对时找个具体做法 | 按「我现在什么状态」组织；含求助边界信号与指引 |

### 三层记忆的落点

| 层 | 落在哪 |
|---|---|
| L1 短期（当前会话） | Streamlit `session_state` |
| L2 长期事实（跨会话） | `store.db` 的 attempts / review_state |
| **L3 综合判断** | **`profile.py`** —— 从 L2 推导出的画像 |

### 知识点图谱

`concepts.yaml` 定义知识点与两种关系：

- **prerequisites** 学习顺序约束 —— 没掌握前置就学这个，会很吃力
- **related** 横向联系（类比、对比）—— 不构成顺序约束

每道题用 `concepts:` 声明它考到哪些知识点。校验：

```bash
python -m lifeng.concepts concepts.yaml
```

会检查悬空引用、自引用、以及**前置成环** —— 成环意味着「学 A 要先学 B，
学 B 要先学 A」，学生永远无法开始。这不是理论问题，是标注时容易犯的错。

图谱还会反过来告诉你**题库的空白**：有知识点但没题，那是该补题的地方。

### 资料库检索（RAG）

把讲义或笔记放成 `.md` / `.txt` 到 `materials/`，训练页的「📖 查资料」
就能检索相关段落。

**刻意不用向量库**：题库几十道、资料几篇，这个量级下 BM25 够用，
而且不必让用户为了查一段讲义再装 chromadb 或 embedding 服务。
对比与理由见 [NOTICE.md](NOTICE.md)。

**刻意不自动展示**：学生一卡住就看到讲义，等于跳过了那段费力思考 ——
而那段思考正是这个工具存在的理由。要查得自己按。

**刻意不喂给提示模型**：提示链路有 C1 硬约束与掩码兜底，
往里塞未经审查的教材文本等于绕过那套校验。所以这里的用法是把段落
显示给学生，由学生自己用。

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

| 平台 | 产物 | 说明 |
|---|---|---|
| Windows / Linux / macOS | `lifeng-*` 可执行文件 | 由 `.github/workflows/build.yml` 三平台原生构建 |
| **Android** | `lifeng-android.apk` | 安卓客户端，见下节 |

> PyInstaller 产物**不做跨平台交叉编译**：Windows 的 exe 必须在 Windows 上构建。
> 这正是 CI 用三平台矩阵的原因。

---

## 安卓端

### 先说清它是什么

**是一个安卓客户端 App，不是把砺锋装进手机。**

Streamlit 是**服务端 Web 框架**：界面由浏览器渲染，Python 服务端通过 websocket 驱动。
`python-for-android` / `buildozer` 打的是 Kivy（另一套 GUI 工具包），
**无法把 Streamlit 打进 APK** —— 这一点没有绕过去的办法。

所以 APK 里是一个 WebView + 一个记住地址的设置页。它解决的是**体验问题**：

| 不用 APK | 用 APK |
|---|---|
| 学生每次要输 `http://192.168.1.10:8501` | 装一次，点桌面图标就用 |
| 容易抄错地址 | 地址只问一次，存在本地 |
| 连不上只看到白屏 | 给出可操作的排查提示 |

**手机与老师电脑必须在同一网络内** —— 服务始终在电脑上跑。

### 构建

```bash
# 本地（需要 Android SDK + JDK 17）
cd android && gradle test && gradle assembleDebug
# 产物：android/app/build/outputs/apk/debug/app-debug.apk
```

CI 会自动构建并随 Release 发布，无需本地环境。

### 为什么发的是 debug 包

debug 包由构建机**自动签名**，可以直接侧载安装。
release 包需要自备签名证书并保管密钥 —— 对课堂分发反而多一道门槛，
而且一旦密钥丢失就无法覆盖安装。课堂场景下 debug 包更合适。

### 一个必须保留的配置

`AndroidManifest.xml` 里的 `android:usesCleartextTraffic="true"` **不是可选项**：

Android 9（API 28）起默认禁止明文 HTTP，而本应用连的是局域网 `http://`。
关掉这一项，WebView 会**直接拒绝加载且没有任何提示** —— 表现为白屏。

服务在局域网内、不经过公网，明文传输的暴露面是同一 Wi-Fi 下的人，课堂场景可接受。

---


## 已知局限

`leak_check` 是字符串黑名单，**拦不住改述型泄漏**
（把"频哪酮"说成"那个叔丁基酮"）。
阶段一（20 题）的应对是实验者逐条人工复核全部提示。
阶段二若扩大规模，此处是明确的技术债。
