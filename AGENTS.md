# booksoul / 书魂

把书解析成模型读得懂的 Markdown，再让模型扮演它。

## 这个项目是什么

两件事，中间隔着磁盘：

1. **解析**：`epub / mobi / azw3 / pdf / docx / txt / html / md` → 结构化 Markdown，
   落在 `library/` 下。
2. **对话**：基于解析产物，以三种人格之一回答读者——研读者、作者本人、书本身。

对话层有两条路，都可用：

- **Claude Code 原生**：在这个目录里直接问，`.claude/skills/booksoul/` 那个 skill
  会让我去读 `library/` 和 `prompts/`。零配置，不花 API 钱。
- **独立 CLI**：`booksoul ask` / `booksoul chat`，可接任意模型后端（见下）。

## 代码结构

```
src/booksoul/
  model.py       Book / Chapter / BookMeta —— 所有解析器的统一产出
  parsers/       各格式解析，__init__.py 按魔数探测格式并分发
  html2md.py     自写的 (X)HTML → Markdown（中文排版有坑，见文件头注释）
  xmlutil.py     XML 剥命名空间（epub 的命名空间是雷区）
  clean.py       正文清洗：缩进、水印、硬换行、PDF 页眉页脚
  structure.py   没有目录时从正文推断章节
  footnotes.py   脚注整理成标准 Markdown 脚注
  writer.py      写出书库目录
  library.py     把书从书库读回来
  retrieve.py    书装不下时挑章节（BM25）
  persona.py     加载 prompts/ 下的人格模板
  brain.py       可插拔模型后端：anthropic（官方 SDK）/ openai 兼容（标准库 HTTP）
  config.py      大脑配置与预设
  ask.py         把书、人格、大脑接到一起
  cli.py         命令行
prompts/         三种人格的提示词 —— 这是项目真正要反复打磨的东西
library/         解析产物（已 gitignore）
design/          前端界面稿（Claude Design 画布，静态设计稿，不是能跑的前端）
ProjectModel/    开发流程框架：流程 Skills、设计文档、各阶段产出（见下半篇）
```

## 改这个项目时注意

- **人格提示词在 `prompts/` 里，不在代码里。** 要调对话质量就改那三个 md，
  不要把提示词硬编码回 Python。
- **中文排版有几个反复踩的坑**，改 `html2md.py` / `clean.py` 前先读文件头注释：
  全角空格缩进会污染正文、行首 4 空格会变代码块、滑窗二元组会切出跨词边界的垃圾。
- **api key 不进配置文件。** `booksoul.toml` 里写的是环境变量名，key 从环境读。
  这条不要破坏——那个文件是设计成可以提交进仓库的。
- **解析器的失败要局部化。** 单个章节文件坏掉不该毁掉整本书；`epub.py` 里用
  `book.warn()` 记下来继续走，不要让异常冒到顶上。
- **不要假装成功。** 抽不出文字的扫描版 PDF 要明确报错并给出 OCR 建议，
  而不是写出一个空 md。

## 验证改动

```bash
PYTHONPATH=src python -m booksoul parse 某本书.epub   # 解析
PYTHONPATH=src python -m booksoul list                # 看产物
PYTHONPATH=src python -m booksoul ask --brain qwen "问题"   # 端到端
```

装过 `pip install -e .` 之后，上面三条可以直接写成 `booksoul parse` / `booksoul list` /
`booksoul ask`，不用带 `PYTHONPATH`。注意 editable 安装记的是绝对路径，**项目目录改名或
搬家后必须重新 `pip install -e .`**，否则 `booksoul` 命令会报 `No module named 'booksoul'`。

改了解析逻辑后，至少抽查一章的 Markdown：看有没有全角空格残留、有没有被误判成
代码块的行、脚注有没有接上。

## 两组同名目录，别混

这个仓库里有两套叫「Skills」和「Design」的东西，用途完全不同：

| 路径 | 是什么 |
|---|---|
| `.claude/skills/booksoul/` | **与书对话**的 skill：怎么读 `library/`、怎么用三种人格 |
| `ProjectModel/Skills/` | **开发流程**的 skills：grill / spec / plan / tdd 那一套 |
| `design/` | 前端**界面稿**（Claude Design 画布，`.dc.html` + 导出的 html） |
| `ProjectModel/Design/` | 框架的**设计文档**（`frontend.md` / `backend.md` / `database/`） |

---

# 开发流程 · ProjectModel

本项目定义了以下开发流程 Skills，位于 ProjectModel/Skills/ 目录。
当用户提到对应的关键词或进入对应的开发阶段时，必须读取对应的 SKILL.md 并严格遵循其流程。

## 开发流水线

grill-me-with-design → write-a-spec → spec-to-plan → tdd →(自动) design-doc-sync

旁路工具（按需显式触发）：to-design、read-design-before-code、improve-codebase-architecture（只产出 RFC、不改代码，其 RFC 经用户显式输入 "rfc to plan" 走快车道实施）

## Skill 触发规则

| Trigger keywords | Skill | File path |
|------------------|-------|-----------|
| "grill me", "grill me simple"（中型改动路径）, "stress-test design", "validate my design" | grill-me-with-design | ProjectModel/Skills/grill-me-with-design/SKILL.md |
| "write spec", "create spec", "draft spec" | write-a-spec | ProjectModel/Skills/write-a-spec/SKILL.md |
| "spec to plan", "rfc to plan"（RFC 快车道）, "convert spec to plan", "break down spec" | spec-to-plan | ProjectModel/Skills/spec-to-plan/SKILL.md |
| "tdd", "tdd auto"（连续执行切片模式）, "red green refactor", "test-driven development" | tdd | ProjectModel/Skills/tdd/SKILL.md |
| "sync design docs", "update design docs" | design-doc-sync | ProjectModel/Skills/design-doc-sync/SKILL.md |
| "improve architecture", "review architecture", "deepen modules" | improve-codebase-architecture | ProjectModel/Skills/improve-codebase-architecture/SKILL.md |
| "edit code" / "modify code" (outside TDD flow) | read-design-before-code | ProjectModel/Skills/read-design-before-code/SKILL.md |
| "to design" | to-design | ProjectModel/Skills/to-design/SKILL.md |

## 工作规则

1. 当触发任何 Skill 时，先读取对应的 SKILL.md 全文，再严格按照其中的流程执行
2. 每个 Skill 完成后，按照 Pipeline Position 中的下游指引进入下一步。以下衔接**自动执行**（无需用户再次触发）：grill-me-with-design → write-a-spec（主流水线）；grill-me-with-design → tdd（"grill me simple" 中型路径）；tdd → design-doc-sync（全部路径）；read-design-before-code 改码完成 → design-doc-sync。improve-codebase-architecture 不在流水线内，由 design-doc-sync 收尾提示后用户按需**显式触发**；其 RFC 由用户显式输入 "rfc to plan" 走快车道实施；其余衔接均需用户确认
3. 所有 Skill 产出的文档存放在 ProjectModel/Todo/ 目录下对应的子目录中
4. 设计文档位于 ProjectModel/Design/ 目录
5. ProjectModel 范式框架与目录结构说明见 `ProjectModel/README.md`（框架权威总入口）。项目级代码规范位于各 Design 文档的规范章节（`frontend.md` / `backend.md`「设计模式与约定」、`database/sql.md`「约定」），不设独立规范文件 —— 由 `to-design` 首次建立（逆向：每条附本项目代码 / 配置证据，无证据写待确认；正向：按选定技术栈的社区基线草拟、汇总确认一次），也可由用户手工维护；to-design 遇已填规范章节默认建议合并，**不得静默覆盖**。改码中产生的规范候选由 `tdd`（单切片模式每片完成时、auto 模式全部完成时）与 `read-design-before-code`（改码完成时）列出，**经用户显式判定后才写入**；`design-doc-sync` 只同步架构章节，不改动规范章节；`tdd` 与 `read-design-before-code` 在改码前**必读**相关 Design 文档（含规范章节）。
6. 本文件（`CLAUDE.md`、`AGENTS.md`、`.github/copilot-instructions.md`）三份内容必须保持完全一致；修改任意一份时，必须同步另外两份。这条约束覆盖整个文件，上半篇的项目说明同样适用。
