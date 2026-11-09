# booksoul · 书魂

把书解析成模型读得懂的 Markdown（书的**形**），再让模型扮演它（书的**魂**）。

不是又一个"上传 PDF 问问题"的工具。它想做的是：当你带着自己的困惑去翻一本书时，
让这本书能开口回应你——用它自己的思路，而不是一段贴给谁都成立的道理。

```
$ booksoul parse 悉达多.epub
📖 悉达多.epub　[EPUB 电子书]
  书名：悉达多　作者：赫尔曼·黑塞
  产物：library\悉达多
  统计：17 节　48,260 字　脚注 21 条　图片 3 张

$ booksoul ask "悉达多在离开佛陀时说了什么，他拒绝追随的理由是什么"
```

---

## 一、解析：把书变成 Markdown

```bash
booksoul parse 你的书.epub        # 单本
booksoul parse ./books/           # 整个目录
booksoul list                     # 看看书库里有什么
```

支持 `epub` / `mobi` / `azw3` / `pdf` / `docx` / `txt` / `html` / `md`。
**按文件内容判断格式，不信扩展名**——下载来的书标着 `.txt` 其实是 epub 的情况太多了。

产物落在 `library/<书名>/`：

```
library/悉达多/
  INDEX.md      导览：元信息 + 目录 + 每章字数。先读这个决定看哪章
  book.md       全书单文件，带 YAML front matter。上下文放得下时整本喂
  chapters/     分章文件，001-婆罗门之子.md …… 按需加载
  meta.json     结构化元信息，程序用
  images/       正文引用到的图片
```

### 为什么不直接用现成的转换工具

中文电子书有一批别人不太处理的细节，这个项目逐个处理了：

| 坑 | 处理 |
|---|---|
| 全角空格 `　　` 做首行缩进，转出来污染正文 | 归一后剥掉；行首 4 空格会被 Markdown 当代码块 |
| EPUB 的 spine 才是阅读顺序，manifest 不是 | 按 spine 走，目录（NCX/nav）只用来给标题和层级 |
| 整本书塞进一两个 xhtml，章节靠 `#锚点` 分 | 按锚点切分 DOM，不会切出一个几十万字的"单章" |
| 脚注变成 `[[1]](part0005.xhtml#f1)` 一堆噪声 | 转成标准 Markdown 脚注 `[^1]` / `[^1]: …` |
| TXT 是 GBK，chardet 常判错 | 逐个候选编码试解，按"解出来有多少像中文"打分 |
| TXT 每 30 字硬换行，一段被拆成十几行 | 按句末标点合并回自然段 |
| PDF 每页重复的页眉页脚和页码 | 按跨页出现频率识别并剥离 |
| 扫描版 PDF 抽不出字 | 明确报错并给 OCR 命令，不产出空文件 |

## 二、对话：让书开口

### 三种人格

| 人格 | 它是谁 | 什么时候用 |
|---|---|---|
| `scholar` 研读者 | 把书读透的人 | 想把书读明白：这章什么意思、前后怎么呼应、书的边界在哪 |
| `author` 作者 | 作者本人 | 想听"如果是黑塞，他会怎么看我这个问题" |
| `book` 书本身 | 这本书 | 想让书直接回应你——它只有自己这些字，但它知道内部的呼应 |

```bash
booksoul ask "悉达多为什么离开佛陀"                  # 默认研读者
booksoul ask -p author "我该不该辞掉稳定的工作"       # 作者来回答
booksoul ask -p book --reflect "我总觉得在重复同样的错"  # 书来回应你的处境
booksoul chat -p author                              # 多轮对话
```

`--reflect` 是告诉模型：这次读者带来的不是知识问题，是他自己的处境。

**提示词在 `prompts/` 下的三个 Markdown 里，直接改就生效。** 它们是这个项目真正
需要反复打磨的东西，所以没有硬编码进代码。

### 在 Claude Code 里直接用

这个目录带了一个 skill（`.claude/skills/booksoul/`）。在项目里直接说
"以黑塞的身份回答我……"，Claude Code 会自己去读 `library/` 和 `prompts/`。
**零配置，不花 API 钱**，也是效果最好的一条路——因为书直接进了对话上下文。

## 三、换一个大脑

模型后端是可插拔的。Anthropic 走官方 SDK，其余一律走 OpenAI 兼容端点——
DeepSeek、Kimi、智谱、通义、硅基流动、OpenAI，以及本地的 Ollama / LM Studio / vLLM
都是这一套协议，换供应商只是换 `base_url` 和 `model`。

```bash
booksoul brains                              # 看有哪些，以及 key 设没设
booksoul ask --brain deepseek "……"
booksoul ask --brain kimi "……"
booksoul ask --brain ollama --model qwen2.5:14b "……"      # 本地，不花钱
booksoul ask --base-url http://自建:8000/v1 --model my-model "……"   # 任意自建端点
```

| 预设 | 端点 | 需要的环境变量 |
|---|---|---|
| `claude` | Anthropic 官方 SDK | `ANTHROPIC_API_KEY` |
| `deepseek` | api.deepseek.com | `DEEPSEEK_API_KEY` |
| `kimi` | api.moonshot.cn | `MOONSHOT_API_KEY` |
| `glm` | open.bigmodel.cn | `ZHIPU_API_KEY` |
| `qwen` | dashscope 兼容模式 | `DASHSCOPE_API_KEY` |
| `siliconflow` | api.siliconflow.cn | `SILICONFLOW_API_KEY` |
| `openai` | api.openai.com | `OPENAI_API_KEY` |
| `ollama` / `lmstudio` | 本地 | 不需要 |

预设里的模型名只是开箱默认值，随各家更新会变，用 `--model` 或配置文件覆盖即可。

### 配置文件

```bash
booksoul init-config        # 生成 booksoul.toml
```

```toml
default = "deepseek"

[brains.我的自建]
provider = "openai"
base_url = "http://192.168.1.10:8000/v1"
model = "Qwen2.5-72B-Instruct"
api_key_env = "MY_API_KEY"     # 写环境变量名，不写 key 本身
```

**api key 永远不进配置文件**，配置里写的是环境变量名。所以这个文件可以安心提交
进仓库、可以贴给别人看。

## 四、装

```bash
pip install -e .              # 只装必需的 lxml
pip install -e ".[all]"       # 连可选格式和 Anthropic SDK 一起装
```

按需装：

```bash
pip install pymupdf           # PDF
pip install python-docx       # DOCX
pip install mobi              # MOBI / AZW3
pip install anthropic         # --brain claude
```

要求 Python ≥ 3.11（用了标准库的 `tomllib`）。

没安装也能跑：

```bash
PYTHONPATH=src python -m booksoul list
```

## 五、网页版

除了命令行，还有一套网页界面：书库、上传解析、阅读与溯源、三种人格对读、检索明细、
大脑与人格设置。

### 起来最省事的方式

```bash
cp .env.example .env
# 把 BOOKSOUL_SECRET_KEY 填上，生成一条：
#   python -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up -d
```

打开 **http://127.0.0.1:8317** 。三个容器：`db`（PostgreSQL 16）、`api`、`web`（nginx，
提供静态文件并把 `/api` 反代给 api）。schema 由 api 容器启动时 `alembic upgrade head`
自动迁好，不用手动建表。

数据落在这几处，都在主机上看得见：

| 路径 | 是什么 |
|------|--------|
| `data/library/` | 解析产物。网页版（compose）与本机开发**共用这一份**，因为它们共用同一个数据库。根目录那份 `library/` 只给命令行 `booksoul list` 用 |
| `data/uploads/` | 原始上传文件，重新解析要用 |
| `prompts/` | 人格提示词，和命令行那条路共用同一份。⑧屏改了它，主机上这三个 md 就变了 |
| `db-data`（命名卷） | 数据库。`docker compose down -v` 会连它一起删掉 |

端口被占就改 `.env` 里的 `BOOKSOUL_PORT`。

> **只绑 127.0.0.1，这是有意的。** 用户的模型 API key 每次提问都经 `X-Brain-Key` 头
> 明文送到服务端，而这套 compose 没有 TLS。要放到局域网或公网，前面必须再加一层
> TLS 终结，而不是把 `compose.yml` 里那个 `127.0.0.1:` 删掉。

### 改代码的时候用这个

compose 里的前端是构建好的静态文件，改代码不会热更新。开发时**数据库仍用 compose 那个**，
前后端在本机跑：

```bash
docker compose up -d db           # 只起数据库，它对本机开 127.0.0.1:5432
docker compose stop api           # 如果整套开着，先把容器里那个 api 停掉（原因见下）
python -m uvicorn booksoul_api.main:app --reload --port 8000
cd web && npm run dev             # 另开一个终端
```

打开 http://127.0.0.1:5173 。Vite 已经把 `/api` 代理到 8000，前端代码里只写
`/api/...`，开发与同源部署不用改地址。

几件要知道的事：

- **本机开发与网页版共用同一个数据库和同一个书库目录（`data/`）。** `.env` 里的
  `BOOKSOUL_LIBRARY_ROOT` / `BOOKSOUL_UPLOAD_ROOT` 就是为此设的：库里存的是
  `{user_id}/{book_id}-书名`，两边的根目录一旦分叉，本机就会去一个空目录里找一本
  库里明明有记录的书 —— 查不到，而且不报错。
- **不要让本机的 uvicorn 和容器里的 api 同时开着。** 那是两个进程连同一个库：后起的
  那个启动时会做一次孤儿清扫，把先起的那个**正在跑的解析**标成「服务重启时被中断」。
  理由同 `docker compose up --scale api=2` 不可以（见 `backend.md` 待确认第 2 条）。
- **测试也连这个库**，但用另一个库名 `booksoul_test`，每个测试前清空；你的书在
  `booksoul` 库里，碰不到。

### 模型 key 填在哪

注册登录、上传解析、阅读这些都不需要 key。**提问**才需要：进「大脑与人格」页把某个
大脑的 key 填进去。那个 key 只存你自己浏览器的 `localStorage`，每次提问经请求头带给
服务端，**服务端用完即丢** —— 不写数据库、不写文件、不写日志、不出现在任何响应里。

## 已知边界

- **PDF 的段落边界不总是可靠**。PDF 记录的是"字放在页面哪个位置"，不是"这是一个
  段落"。解析后建议抽查几章。
- **MOBI/AZW3 依赖 `mobi` 包解包**。装不上就用 Calibre 的 `ebook-convert` 转成
  EPUB 再解析，错误信息里会直接给命令。
- **书装不下上下文时才会挑章节**，用的是 BM25 词面检索。它对"问法和原文用词完全
  不同"以及"某个词贯穿全书、不具备章节区分度"这两类问题无能为力。现在的模型
  上下文动辄百万 token，一本普通的书整本塞进去才是最优解——这个检索是给几百万字
  的大部头兜底的。
- **国内平台的内容审核会拦严肃文学**。这是实测结论，不是猜测：用通义读《悉达多》
  全书会被 `HTTP 400 inappropriate content` 拒绝，二分定位到《轮回》一章——悉达多
  沉沦赌博酗酒、以及章末与迦摩罗的情爱描写那段。与 key、尺寸都无关（4.5 万字无害
  文本照样通过，同一 key 空提示也通过）。出路有三条，错误信息里会直接列出来：
  换大脑（Claude / DeepSeek / Kimi）、用 `--budget` 调小载入量让检索避开那一章、
  或用本地模型。实测 `--budget 20000` 载入 7/16 节即可通过，答案质量不受影响。
- **扮演不等于复活**。人格提示词里明确要求区分"书里明写的"和"按书的理路推的"，
  也要求承认书照不到的地方。如果你发现它开始给放之四海而皆准的鸡汤，那是提示词
  该调了，改 `prompts/` 下的文件。

## 许可

MIT。书的版权归原作者和出版方，这个工具只处理你自己合法持有的文件。
