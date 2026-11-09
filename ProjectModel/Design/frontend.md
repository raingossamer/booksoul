# 前端设计文档

> **本文档职责**：记录前端架构、技术栈、目录结构、路由、组件层级、状态管理与 API 调用层（架构章节），以及前端**项目级代码规范**（「设计模式与约定」章节）。
> 架构章节由 `to-design` 生成（正向 0-to-1 / 逆向）、由 `design-doc-sync` 在每轮开发收尾时同步更新。
> **「设计模式与约定」章节例外**：它是项目级代码规范，只由 `to-design` 首次建立，或由 `tdd` / `read-design-before-code` 列出规范候选、经你显式判定后写入；`design-doc-sync` 不改动它。
> 是 `grill-me-with-design` / `write-a-spec` / `tdd`（GREEN 阶段）读取的约束源之一。
>
> 以下为约定骨架，章节标题固定，内容按实际项目填充；不适用的章节保留标题并标注"暂无"。占位词约定见 `ProjectModel/README.md`。
>
> **本次生成为正向（0-to-1）**：项目当前无前端源码（无 `package.json`、无 `vite.config.*`）。视觉系统一项例外——它有实证依据，来自 `design/` 下九份已完成的界面稿。

## 技术栈

| 项 | 选型 | 来源 |
|----|------|------|
| 框架 | Vue 3（`<script setup>` + Composition API） | 〔来源：用户指定〕 |
| 语言 | TypeScript（`strict: true`） | 〔来源：用户指定〕 |
| 构建 | Vite 5 | 〔来源：Vue 3 社区基线〕 |
| 状态管理 | Pinia | 〔来源：Vue 3 社区基线〕 |
| 路由 | Vue Router 4 | 〔来源：Vue 3 社区基线〕 |
| 请求 | 原生 `fetch` 封装；SSE 用 `fetch` + `ReadableStream` 手动解析 | 〔来源：见「API 调用层」说明〕 |
| UI 库 | **不引入**，手写组件 | 〔来源：见下〕 |
| Markdown 渲染 | `markdown-it` + `markdown-it-footnote` | 〔来源：`writer.py` 产出标准 Markdown 脚注 `[^n]`，需要脚注插件〕 |
| 样式 | 原生 CSS + CSS 变量（design token），不引 Tailwind/预处理器 | 〔来源：`design/*.dc.html` 全部为内联原生 CSS〕 |
| 交付 | `vite build` → `web/dist`，由 nginx 提供并反代 `/api`（见 `docker/nginx.conf`）；开发时走 vite dev server + `vite.config.ts` 里的 `/api` 代理 | 〔来源：`compose.yml` / `Dockerfile`〕 |

**为什么不引 Element Plus / Ant Design Vue**：`design/` 九屏的视觉系统是自成一套的书卷风格——0–2px 直角、朱批红做引文标记、Noto Serif SC 排正文、72px 深色图标栏。套用组件库的默认外观后，改回这套的工作量大于手写。真正需要的复杂控件只有下拉与开关两种。

**为什么不引 axios**：本项目两条最关键的链路（解析进度、对话输出）都是 SSE，axios 不支持流式读取，仍得用 `fetch`。与其两套并存，不如统一用 `fetch`。

## 目录结构

```
web/
├── index.html
├── vite.config.ts
├── tsconfig.json
└── src/
    ├── main.ts
    ├── App.vue
    ├── main.ts                # 另兜一件事：文件拖到拖放区以外松手，不让浏览器把它打开、把应用换走
    ├── router/index.ts        # 路由表 + 登录守卫
    ├── stores/                # Pinia：只有 auth。chat、brain、library 都没建 store（见「状态管理」）
    ├── api/                   # 接口调用层，一个资源一个文件
    │   ├── client.ts          #   唯一的请求出口 send：注入 token 与 key、401 续期重放、统一错误；
    │   │                      #     取法四种：JSON / 上传 / 二进制 / 流（SSE）
    │   ├── auth.ts、books.ts、conversations.ts、brains.ts、citations.ts、retrieval.ts、formats.ts
    ├── types/                 # 与后端 schema 对应的 TS 类型
    ├── views/                 # 页面级组件，与路由一一对应
    ├── components/            # 可复用组件
    │   ├── layout/            #   IconRail、AppShell
    │   ├── book/              #   BookCard、EmptyLibrary、ImportRow、ParseStageBar、ChapterTree、
    │   │                      #     ChapterProse、ReaderToc
    │   ├── chat/              #   PersonaTabs、AnswerBlock、ConversationList
    │   ├── brain/             #   BrainRow、ModelPicker、AddEndpoint（⑧屏左半）
    │   ├── persona/           #   PersonaEditor（⑧屏右栏）
    │   └── common/            #   AppDialog、NoteBanner、BackButton
    ├── lib/                   # 不依赖 Vue 的小工具：brainKeys（key 与选中的大脑，localStorage；
    │                          #   大脑能不能问 isReady、新开会话用谁 brainForNewChat）、
    │                          #   contextSource（额度来路的说法）、markdown（书的正文 / 模型回答
    │                          #   各一台渲染器）、citations、keys（`isImeEnter`：回车是不是输入法的；
    │                          #   对话输入框、换模型弹窗两格、填 key 那一格共用，都挂在 keydown 上）
    ├── composables/           # useAsk、useConversations、useFileDrop（拖放区，书库与导入共用）、
    │                          #   useFormats、useImports、useLibrary、useSSE
    └── styles/
        ├── tokens.css         # design token（下方「样式约定」）
        └── base.css
```

> **部署形态对路由的要求**：`createWebHistory()` 意味着深链在磁盘上没有对应文件，
> 服务端必须把它们回退到 `index.html`（`docker/nginx.conf` 的 `try_files`）。
> 换成 hash 路由就不需要这一条，但地址栏里会多一个 `#`，且⑤屏那种「把链接发给别人」
> 的用法会变难看。

## 路由结构

| 路径 | 页面 | 对应界面稿 |
|------|------|-----------|
| `/login` | 登录 / 注册 | 新增（设计稿未覆盖） |
| `/` | 书库 | ③ 书库 |
| `/import` | 导入与解析（这一批的进度放在模块里，离开再回来还在） | ① 导入与解析 |
| `/books/:id` | 书籍详情（解析中的书就地轮询：每拍只问 `/state`，离开解析中才拉一次整份详情、自己变过来；重新解析后留在这一页） | ④ 书籍详情 |
| `/books/:id/report` | 解析报告 | ② 解析报告 |
| `/books/:id/read/:idx` | 阅读 | ⑤ 阅读与溯源 |
| `/books/:bookId/chat` | 新会话：一张空白稿纸，问出第一句才建；第一问的流一开即 `replace` 到 `/chat/:id` | ⑦ 对话 |
| `/chat/:id` | 对话。带 `#m-<回答 id>` 进来（阅读页「回到对话」）就落在那条回答上，不带就到底部 | ⑦ 对话 |
| `/chat/:id/retrieval` | 检索与预算明细 | ⑥ 检索与预算 |
| `/settings` | 大脑与人格 | ⑧ 大脑与人格 |

> **`/books/:id/read/:idx?cite=<citationId>`**：带 `cite` 进来就是从对话跳过来的——顶上画一条朱批色来路条（写明跳过来之前问的是什么），正文里把被引的那一块标出来并滚到它那里；来路条上的「回到对话」带着 `#m-<messageId>` 回去。正文与引文一起取、一起换上。阅读页 ←/→ 翻节（输入框里、带修饰键的不算）。
> **`/chat/:id/retrieval` 不带 messageId**：选章明细挂在会话上，不属于某一条回答——一轮里三种人格各答一次，却只选一次章。

书库为空时 `/` 渲染首次启动态（⓪ 初次启动），不单独占路由。

登录守卫：除 `/login` 外全部要求 `auth.isAuthenticated`，否则重定向并记录 `redirect` 查询参数。

## 核心页面与组件

| 组件 | 职责 | 备注 |
|------|------|------|
| `AppShell` | 72px 深色图标栏 + 内容区的整体框架 | 九屏共用。图标栏里能点的几格悬停变亮、带 `title` |
| `BookCard` | 书库卡片，含四种状态：就绪 / 存疑 / 解析中 / 阻断 | 状态色见「样式约定」。停下了的卡片按 `errorCode` 给角标与一句说明：`no_text_layer` / `bad_archive` / `no_content` / `unsupported_format`（「格式不支持」）/ `internal`（「没能完成」），其余兜底「停下了」。`unsupported_format` 不摆「重新解析」——这个部署没装那种格式的后端，原样重跑还是一样，出路写在说明里（换个格式再传） |
| `EmptyLibrary` | ⓪屏首次启动态 | 格式那一排由 `/api/formats` 的探测结果经 `formats` prop 交进来，不写死 |
| `ImportRow` | ①屏一个文件的一行：上传中 / 解析中 / 已入库 / 停下了 / 没收下 | 已入库那一行的书名就是去书籍详情的链接 |
| `ChapterTree` | 目录树，字数条画在同一组件里 | **字数条是质检工具**：过短章节标黄提示用户核对 |
| `ParseStageBar` | 解析五阶段进度（读取→解析目录→转换正文→清洗→写入） | 数据来自 SSE `stage` 事件（推送断了由 `useImports` 退回轮询 `GET /api/jobs/{id}`，同一份记录） |
| `ReaderToc` | ⑤屏左栏：目录 + 全书位置条 | 当前那一节滚进视野（`scrollIntoView({block:'nearest'})`） |
| `NoteBanner` | 存疑（黄檗）/ 阻断（重墨）两种提示条 | 阻断态用整块墨色，不用第二种红 |
| `PersonaTabs` | 研读者 / 作者 / 书 / 对读 四个标签 | 「对读」并排渲染三列。点人格标签只改**下一问**用谁（`ChatView` 的 `chosen`），随提问一起交给服务端，服务端在写下这一问的同一个事务里换主干；对读不换主干 |
| `AnswerBlock` | 一个人格的回答：署名 + 正文 + 引文块 + 结语 | 单列与对读三列共用同一组件。正文按 Markdown 渲（`lib/markdown` 的 `renderAnswer`），流到一半也渲——没闭合的 `**` 先原样露着。根元素带 `id="m-<回答 id>"`（「回到对话」的落点）。只给 `failure` 不给正文时画成一张只有署名与失败原因的卡（一路一个字都没说就失败的那几路） |
| 引文块（在 `AnswerBlock` 里，未拆组件） | 引文 + 三种互斥状态各说一句：定位到了给「去原文 · 章名」（重解析后找回的再加「重解析后重新找回」）/ 章节被重建而没找回：「原位置已变更」/ 当初就没找到：「没找到」 | 「去原文」跳 `/books/:id/read/:idx?cite=<id>`，阅读页滚到被引那一块并高亮 |
| `ConversationList` | 一串会话，点进去接着聊，旁边「删掉」。书库「最近问过的」（网格、带书名）与书籍详情「这本书的会话」（窄栏一条一行）共用 | 展示组件；加载、删前确认、删完补满由 `useConversations` 做：一轮都没问过的空会话直接删，问过的先说清几轮会一起删、不能恢复。正在回答时服务端 409，照样显示那句话 |
| `ChapterProse` | ⑤屏一节正文：Markdown 渲染 + 就地展开的脚注 + 书里的图 | 图由 `ReaderView` 带 token 取成 blob、以 `images` prop 交进来（不填进 `blocks`：换了 blocks 会收起已展开的脚注）；只认 `images/` 下的文件，外链图只留说明文字、不去取 |
| 预算仪表（`ChatView` 底栏，未拆组件） | 载入 n/m 节、token、缓存命中 | 点击进 ⑥ 屏 |
| 排名表（`RetrievalView` 主体，未拆组件） | 相关度排名表 + 预算线 | ⑥ 屏主体。表头写「相关度」，算法名不进界面 |
| `BrainRow` | ⑧屏大脑表的一行：模型（**模型名本身就是换模型的开关** `qwen-plus ▾`，点开弹窗）、正文额度 + 来路（内置 / 估计 / 你填的 / 供应商）、这本书装不装得下、key | **只显示 key 名字与掩码，不显示 key**。估计的那一行来路字与「这本书」一格着黄檗 |
| `ModelPicker` | 换模型弹窗的内容（装在 `AppDialog` 里）：「列出模型」→ 可搜索的列表（「共 N 个，来自 …」）；**模型名与窗口两个手填框永远在**；底栏「只影响以后开的会话」+「用这个」保存；「恢复默认」（仅改过的内置预设） | 展示组件，开弹窗、拉列表与保存由 `SettingsView` 做。失败措辞用服务端的，行为按 `code`（超时 / 连不上才给「再试一次」）。同一时刻只开一个 |
| `PersonaEditor` | ⑧屏右栏：三种人格的提示词，保存 / 还原 | 草稿只在组件里。有未保存改动时三条路都拦：切人格标签（`confirm`）、换路由（`onBeforeRouteLeave`）、关或刷新浏览器标签（`beforeunload`）。Ctrl+S / ⌘S 就地保存 |
| `AppDialog` | 弹窗：原生 `<dialog>` + `showModal()`，标题 + 关闭 + 内容槽 | 开关归父组件的 `v-if`；Esc（拦下 `cancel`）与点遮罩（按下、松开都在遮罩上）都只发 `close` |
| `BackButton` | 返回上一层：描边朱批、带 `←`。书籍详情「回书库」、解析报告「回书籍详情」、检索明细「回到对话」三处共用 | **描边而非实心**：实心朱批留给主按钮（发起动作的），返回是导航 |

## 状态管理

| Store | 持有 | 说明 |
|-------|------|------|
| `auth` | user、accessToken、refreshToken | token 存 localStorage，刷新页面恢复。token 归 `api/client.ts`，store 记的是「已登录」：续期也续不上时 client 清掉 token，`router/index.ts` 的 401 处理随即 `auth.logout()` 把 store 一起清，再带着 `redirect` 去登录页（刷新页面时那次核实就失效的，交给守卫去送） |
| `library`（**未建 store**） | 书列表 | 实际落地：`useLibrary`（书库页自己的）——书列表、正在上传的文件（上传那几秒书库里还没有卡片，顶上一条靛色「正在上传…」，传完一本刷一次）、有书在解析时每 1.5 秒轮询一次。章节正文不缓存：阅读页翻一节取一节 |
| 导入这一批（**模块级，不是 store**） | ①屏的一行行进度 | `useImports` 把列表放在模块里（与 `useFormats` 的探测结果同一种做法）：离开 `/import` 再回来，进度与停下来那几本的补救办法都还在。离开时放掉全部连接，回来时还在解析的几行重新接上；推送断了（或服务端推送自己出了事，`error` 不带 `errorCode`）不当成解析失败，退回每 1.5 秒轮询 `GET /api/jobs/{id}`，直到有结局 |
| `chat`（**未建 store**） | 当前会话、消息列表、流式中的临时消息 | 实际落地：会话、还没问出第一句的稿纸、下一问的人格 / 对读 / 处境模式都在 `ChatView` 组件内；流着的那一轮（按人格分列的字、告示、整轮与各路的结局）在 `useAsk`，`done` 后回头拉一次会话转正。`ChatView` 另记：「贴着底」（离底部 48px 以内才跟着新字滚，发问时一律回到底部）；上一问问在哪（`lastAsked`）——一路一个字都没说就失败时服务端不存，失败原因靠它留在拉回来的那一轮底下直到下一问；最后一问没有任何回答时给「重问」（还在答的会被服务端挂回那条流，不重复计费）；服务端没接下的问题把字还回输入框。离开页面即中止读流（服务端照样跑完） |
| `brain`（**未建 store**） | 大脑列表、当前选中、**各大脑的用户 key** | 实际落地：列表与换模型面板的状态在 `SettingsView` 组件内；key 与「选中的大脑」在 `lib/brainKeys`（localStorage）；**模型选择存在服务端**（`brain_choices`，跟着账号走）。新开会话用哪个大脑只由 `brainForNewChat(known)` 一处决定：选过而且还在名单里就是它，否则 claude，名单里连 claude 都没有就是第一个；设置页的圆点、书籍详情的朱批标记、对话页的稿纸都按各自手里那份名单调它。设置页删掉自定义端点前先问（它的 key 一起忘掉、用它开的会话之后不能再问），删的正是选中的那个就把选择一并忘掉。能不能问只由 `isReady()` 判（本机的不要 key），设置页「点选能用的就回书籍详情」与对话页输入框共用 |

**`brain` store 的 key 处理（硬规则）**：

> 用户的模型 API key 只存在 `localStorage`，key 名形如 `booksoul.brainKey.<brainName>`。发起对话请求时由 `api/client.ts` 放进 `X-Brain-Key` 请求头。
>
> 禁止：把 key 放进 Pinia 的持久化快照以外的任何地方、放进 URL、放进任何日志或埋点、随错误上报一起发出。⑧ 屏的输入框用 `type="password"`，保存后只回显掩码。
> 〔来源：`backend.md`「认证授权」硬规则，用户确认 2026-09-16〕

## API 调用层

`api/client.ts` 是唯一的请求出口，职责五项。前四项只在内部的 `send` 里写一遍：注入 `Authorization`；注入 `X-Brain-Key`（**白名单制**：只有显式传了 `brain` 选项的调用才带——目前是 `useAsk`（提问与开会话）与⑧屏列模型 `api/brains.listModels` 两处）；401 时用 refresh_token 自动续期并重放一次，续不上就清 token、交给 router 的 401 处理（对提问的 POST 流也安全：401 出在认证那一步，服务端还没写提问、没调模型）；把非 2xx 响应转成带 `detail` 与 `code` 的 `ApiError` 抛出（422 的 `detail` 是数组时取第一条的 `msg`）。第五项是几种取法：`request`（JSON）、`requestUpload`（FormData）、`requestBlob`——书里的图：`<img src>` 带不上 token，取成 Blob 再交给 `URL.createObjectURL`（服务端只端位图，SVG 一律 404）——与 `requestStream`（SSE，返回响应本身：开会话的 `Location` 这一刻就在头里）。

**⑧屏的两个导航约定**：保存一个 key 后自动弹出换模型窗并拉模型列表，让人当场挑默认模型；从书籍详情「选大脑 →」（`/settings?bookId=`）进来时，点选一个**能用的**大脑（本机的，或已有 key）立刻回书籍详情，缺 key 就留下，填好 key 再回。书籍详情「装得进哪个大脑」用朱批标出选中的那一个。

**会话从哪里进**：「开始对话」（`/books/:bookId/chat`）每次开一个新的，**问出第一句才建**：进来是一张空白稿纸（`ChatView` 的 `blank`），人格、处境模式、对读都先记在页面上，第一问连同它们一起交给 `POST /api/conversations`（`useAsk` 的 `{ start }` 目标），会话与第一问一个事务落下，点进来又走掉什么都不留。流一开（响应头 `Location`）就 `router.replace('/chat/:id')`——流着的时候刷新回到的是这个会话；这次换地址引起的重新加载跳过，说完再拉一次。稿纸与已有会话都按 `isReady` 判能不能问：本机的大脑不要 key；列表里认不出这个大脑就不拦，让服务端说。接着上次聊，从书籍详情侧栏「这本书的会话」或书库「最近问过的」进。两处都走 `useConversations`（`GET /api/conversations?limit=&bookId=`、`DELETE /api/conversations/{id}`），删完重拉一次把列表补满。对话页顶栏的书名就是回书籍详情的链接。流开之后才 `replace` 地址：流开那一刻人已经不在这张稿纸上（点去了别处），就不把他拽回来；离开对话页即中止读流。

**书籍详情解析中只轮询状态**：书处于解析中时，`BookDetailView` 每 1.5 秒调 `api/books.getBookState`（`GET /api/books/{id}/state`，服务端一条查询），状态离开解析中才 `getBook` 拉一次整份详情（章节、字数、存疑都随解析结果变了）；状态请求失败照样排下一拍，已经摆出来的书不收走。原先每一拍都拉整份详情，装得进哪个大脑、审核记录、会话数全算一遍。书库页（`useLibrary`）仍按列表轮询——一页书的状态在列表接口里本来就是一条查询取齐的。

**SSE 读流**：解析进度与对话输出都用 SSE。由于需要带自定义请求头（`Authorization`、`X-Brain-Key`），**不能用 `EventSource`**（它不支持自定义头），改用 `fetch` + `response.body.getReader()` 手动按 `\n\n` 切事件。请求在 `api/client.ts` 的 `requestStream`（和别的请求同一个出口，401 同样续期重放），`composables/useSSE.ts` 只切事件；它的 `onOpen` 在读事件之前把响应交出去（开会话要读 `Location`）。

两条流断了之后的做法相反，依据是「重来要不要花钱」：**解析进度**是只读的，断了（或服务端的 `error` 不带 `errorCode`，那是推送自己出了事）就退回轮询 `GET /api/jobs/{id}` 同一份记录直到有结局（`useImports`）；**对话**断了不重发（见下）。

事件约定与后端一致，**分两层**：

| 事件 | 时机 | 谁的结局 |
|------|------|---------|
| `stage` | 随时 | 「正在挑章节…」「挑好了…」。只有一个位置，收到字就收掉 |
| `notice`（`{code, text}`） | 第一个字之前（重连回放时也排在最前） | **这一轮** —— 「这本书重新解析过了…这次按全价算」。`useAsk.notices` 收着，**收到字、说完了都不清**，下一次提问才清；`ChatView` 在对话流之外画成一条存疑色横幅，照样显示服务端的 `text`，要分支看 `code` |
| `token`（带 `persona`） | 逐字 | — |
| `answered` / `failed`（带 `persona`） | **当场** | **某一路** —— 收掉那一列的转圈 |
| `done` / `error` | **落库之后** | **整轮** —— 收到它才可以回头拉会话 |

**同一个问题在两个标签页里同时问、或断线后重问**：服务端认出这一轮正在答，把新请求挂到同一条流上回放，**不再调模型**；正在答的是另一个问题则回 409（「上一个问题还在回答」），书在重新解析时也回 409——都是 `ApiError`，`useAsk` 照样把服务端那句话显示出来。

**流断了不自动重发**（`composables/useAsk.ts`）：网络抖一下就重试，等于让同一个问题被计两次费（对读是六次）。断了就停下，把已经收到的那一段留在屏幕上，由用户决定要不要再花一次钱。已收到的字也不清空——服务端那边同样会把残文落库，两边说的是同一件事。

类型：`types/` 下手写与后端 Pydantic schema 对应的 interface，字段一律 camelCase，与 API JSON 一致，前端不再转换（见 `backend.md`「跨层命名对照」）。

## 设计模式与约定（前端代码规范）

> 本章节约束"代码怎么写"，与上文架构章节的"做什么、边界在哪"正交。每条已填规范末尾以 `〔来源：…〕` 注明依据。
> 正向模式下各条来自所选技术栈的社区基线，经用户一次性确认；视觉系统相关条目来自 `design/` 已完成的界面稿。

### 架构级模式

- 组件全部用 `<script setup lang="ts">`，不写 Options API。〔来源：Vue 3 社区基线，用户确认 2026-09-16〕
- 页面级组件放 `views/`，与路由一一对应；`views/` 不被其他组件 import。〔来源：Vue 3 社区基线〕
- 数据请求只在 `api/` 层发起，组件与 store 都不直接 `fetch`。〔来源：Vue 3 社区基线〕
- **请求只有一个出口：composable 也不直接 `fetch`；JSON、上传、二进制、流都走 `api/client.ts` 的 `send`，续期、401、错误转换只写一遍。** 流原先在 `useSSE` 里另写了一份，少的正是续期：access token 半小时过期，闲置半小时再问，别的请求都续得上，偏偏提问失败。以后新增取法照此加在 `client.ts` 里，不另起一份。〔来源：流式请求续期修复（read-design-before-code），用户 2026-09-24 裁定〕
- 跨组件共享状态进 Pinia；组件内部状态用 `ref`/`reactive`，不提升。〔来源：Pinia 社区基线〕
- 可复用逻辑抽成 `composables/use*.ts`，不用 mixin。〔来源：Vue 3 社区基线〕
- **界面给出的结论，必须用产生那个结论的同一个依据。** 两种形态：解释性列表的排序要用产生结果的那个式子（⑥屏「命中的词」按 BM25 贡献排，不按出现次数——按次数排出来的头几个恰恰是 idf 接近 0、对结果没起作用的那些）；**证据不足时不要点名**，改说「怎么把范围缩小」（内容审核嫌疑没收窄到一节时，一节都不挂重墨——随便挂一个等于凭空指认，而用户会照着去删一节没问题的正文）。〔来源：Plan S8 规范候选 #22 + S13 #45，用户 2026-09-20 裁定〕
- **跨语言重复实现同一条切分 / 解析规则时，下游必须能自我校正。** 段号由 Python 数（`services/citations.py:paragraphs()`）、由 TypeScript 用（`lib/markdown.ts`），两边分叉不报错，只会把高亮画到相邻那一块上——而那一块看起来同样像是出处。下游拿索引当快路、拿内容当准绳：对不上就自己去原文里找。〔来源：Plan S9 规范候选 #24，用户 2026-09-20 裁定〕
- **失败的措辞只在服务端写一处，前端按 `code` 决定行为。** 那句给人看的话由服务端给（`detail`），前端照样显示，不另写一套——两处各写，改一处忘一处；前端只根据写死的 `code` 决定**做什么**：超时、连不上才给「再试一次」，key 被拒、服务不给列表则不怂恿重试（`ModelPicker`）。这是「要让代码分支的东西必须机读」的另一半：分支靠 code，措辞靠服务端。〔来源：Todo/Plans/brain-model-picker-plan.md Slice 5，用户 2026-09-22 裁定〕
- **凡是「把用户的东西换掉」的操作，动手前要说清楚会波及什么、要花多少钱；没有波及就不要问。** 重解析前说明「有 N 个会话会受影响、下一轮按全价重算」，没有会话时直接执行。删会话同理：一轮都没问过的空会话直接删，问过的先说清「N 轮问答会一起删掉，不能恢复」（`useConversations`）。每次都弹的确认框等于没有确认框。〔来源：Plan S12 规范候选 #41，用户 2026-09-20 裁定；删会话的例子：会话删除改动（read-design-before-code），用户 2026-09-23 裁定〕
- **渲染模型写的 Markdown 一律不认图片。** 模型读的是用户传进来的书，书里藏一句指令就能让它写出 `![](外站?q=对话内容)`——渲出来浏览器当场去取，不用谁点一下，对话就出去了；站点没有 CSP 兜着。关掉图片规则后 `![x](url)` 只剩一条要人亲手点的普通链接（新标签页、`noreferrer`）。以后凡是新渲模型输出的地方（对读小结等）照此办；实现见 `lib/markdown.ts` 的 `renderAnswer`。〔来源：回答 Markdown 渲染改动（read-design-before-code），用户 2026-09-23 裁定〕
- **改配置用弹窗，不在表格行下展开。** 用原生 `<dialog>` 的 `showModal()`（`components/common/AppDialog.vue`）：焦点困在里面、Esc 关闭、背后变成不可交互——这三件事浏览器都管了，手写要各写一遍，漏了哪件都不报错。开关归父组件的 `v-if`：Esc 拦下 `cancel` 改发 `close`，不让浏览器自己关掉而父组件还以为开着；点遮罩要按下与松开都落在遮罩上才算，否则在框里拖选文字、松手滑到框外，改了一半的东西就没了。〔来源：⑧屏换模型改弹窗（read-design-before-code），用户 2026-09-23 裁定〕
- **要登录才能看的二进制（书里的图）带着 token 取成 blob，再交给 `URL.createObjectURL`；不另开免登录的地址。** `<img src>` 带不上 Authorization 头，而免登录的地址等于把别人的书按递增 id 摆在门外。服务端只端位图白名单，并带 `X-Content-Type-Options: nosniff`；**SVG 不端**——`blob:` 地址继承本站的源，右键「在新标签页打开」，SVG 里的脚本就在本站跑，localStorage 里的 key 近在咫尺。blob 地址占内存，换一节、离开页面都要 `revokeObjectURL`。〔来源：阅读页显示书里的图（read-design-before-code），用户 2026-09-23 裁定〕
- **同一件事只许一处说了算：判断只写一份，状态只有一个主人。** 各算各的不报错，只会在某个角落对不上。两处实测：「大脑能不能问」原先对话页看存没存 key、设置页看 `needsKey`，本机模型（ollama、LM Studio）在设置页算能用、回到对话页却问不出去——现在只有 `lib/brainKeys` 的 `isReady`；「登录了没有」原先 token 归 `client.ts`、store 另记一份，续期失败时 client 清了 token、store 还说已登录，登录页的守卫又把人送回原处，人在两页之间来回弹——现在 401 处理里 store 跟着清（`router/index.ts` 调 `auth.logout()`）。〔来源：对话页本机模型与登录失效修复（read-design-before-code），用户 2026-09-24 裁定〕
- **「回车就提交」的输入框先让输入法：挂在 `keydown` 上，先问 `lib/keys` 的 `isImeEnter`。** 用中文输入法敲一个英文词（模型名、术语），按回车是让字母上屏，不是提交；到了 `keyup`，拼字已经结束，那个回车和普通回车分不出来。原先对话框、换模型弹窗的两格、填 key 那一格都会把半截内容提交出去——对话那一下已经在花钱。〔来源：前端交互走查（read-design-before-code），用户 2026-09-25 裁定；实现见 `lib/keys.ts`〕
- **没被服务端接下的输入，还给用户。** 发出去时清空输入框是为了看起来利落；可请求要是没被接下（4xx、断在建流之前），什么都没存下，那段字就只剩在用户脑子里。以「接下了」为界：接下之前出了事，字还回输入框（输入框里已经有新字就不覆盖）。实测：上一问还在答时再问一句，409 之后问题没了，得重打一遍。〔来源：前端交互走查（read-design-before-code），用户 2026-09-25 裁定；实现见 `ChatView.send` 的 `landed`〕
- **「回头拉一次、以服务端为准」时，服务端不存的那部分由前端留住，直到下一次动作。** 以服务端为准是对的——残文、引文都以库里那份为准；但服务端刻意不存的东西会跟着那一拉一起消失：一路一个字都没说就失败时服务端什么都不落，屏幕上只剩一个没人答的问题，失败的原因闪一下就没了。拉之前先想清楚哪些是服务端不存的。〔来源：前端交互走查（read-design-before-code），用户 2026-09-25 裁定；实现见 `ChatView` 的 `lastAsked` / `lost`〕
- **断了之后怎么办，看重来要不要花钱。** 只读的进度（解析）断了，退回轮询同一份记录，直到有结局；会花钱的提问断了不重发（`useAsk` 的三条纪律）。推送自己出的事（服务端的 `error` 不带 `code`）不当成任务失败——原先导入页把「进度推送中断了」记成「停下了」，一本正在解析的书被标成失败；推送真断了的那一行则永远冻在「解析中」。〔来源：前端交互走查（read-design-before-code），用户 2026-09-25 裁定；实现见 `useImports.track`〕
- **不把人丢在别处。** 做完一件事留在原页等它变（重新解析之后书籍详情就地轮询；原先一点就被送回书库）；跳出去核对再回来，落回跳走的那一处（阅读页「回到对话」带 `#m-<回答 id>`；原先一律回到对话底部）；从别处跳进来要看的那一处，直接滚到它（被引的那一段、目录里的当前节）。〔来源：前端交互走查（read-design-before-code），用户 2026-09-25 裁定；实现见 `BookDetailView.reparse`、`ChatView.settleScroll`、`ReaderView.reveal`、`ReaderToc`〕
- **有草稿的编辑器，三条离开的路都要拦：切标签、换路由（`onBeforeRouteLeave`）、关或刷新浏览器标签（`beforeunload`）。** 只拦其中一条，等于告诉用户「这里会替你留着」，然后在另外两条路上悄悄丢掉。原先人格编辑器只拦了切标签，点一下左边的图标栏，改了二十分钟的提示词就没了。〔来源：前端交互走查（read-design-before-code），用户 2026-09-25 裁定；实现见 `components/persona/PersonaEditor.vue`〕

### 代码格式化

- 2 空格缩进。〔来源：Vue / TypeScript 社区基线，用户确认 2026-09-16〕
- 单引号，语句结尾不加分号。〔来源：Vue 官方模板 ESLint 配置默认值〕
- 行宽 100。〔来源：社区基线，用户确认 2026-09-16〕
- 工具：ESLint（`eslint-plugin-vue` + `@typescript-eslint`）+ Prettier，提交前无 error。〔来源：Vue 3 社区基线〕
- `tsconfig` 开 `strict: true`，禁用 `any`（必要处用 `unknown` 加收窄）。〔来源：TypeScript 社区基线〕
- **不用全角空格（U+3000）做排版**，间距一律交给 flex `gap` / padding。看不见的字符不该承担布局职责——它会跟着复制粘贴跑，而且 ESLint `no-irregular-whitespace` 本来就会拦。〔来源：Plan S5 规范候选 #11，用户 2026-09-20 裁定〕
- **源码一律 LF，`prettier --check` 并进 `npm run lint`。** ESLint 与 `vue-tsc` 都不管换行符，只有 Prettier 管；它原先不在 lint 里，于是 7 个文件被脚本改写成 CRLF 也没有任何一道闸拦下。改写文件的脚本要保留原换行（Windows 上 Python 的 `write_text` 会把 `\n` 换成 `\r\n`）。〔来源：Todo/Plans/brain-model-picker-plan.md Slice 2，用户 2026-09-22 裁定；实现见 `web/package.json` 的 `lint`〕
- **markdown-it 一律 `html: false` + `typographer: false`；`breaks` 看渲的是谁：书的正文 `false`，模型回答 `true`。** `html: false` 让原文里的标签只会被转义显示；智能引号会替坏中文的「」。`breaks` 两边相反，问的是同一件事——单换行是不是有意的：书的硬换行 `clean.py` 已经接过，再按换行拆就是拆回去；模型从不折行，它写的每个单换行都是有意的，接成一段会让「**小标题**↵正文」的小标题粘进正文。〔来源：Plan S5 规范候选 #12，用户 2026-09-20 裁定；「模型回答 `true`」一半：回答 Markdown 渲染改动，用户 2026-09-23 裁定〕

### 组件风格

- 单文件组件顺序：`<script setup>` → `<template>` → `<style scoped>`。〔来源：Vue 官方风格指南〕
- Props 用 `defineProps<T>()` 泛型声明，不用运行时对象声明。〔来源：Vue 3 + TS 社区基线〕
- 事件用 `defineEmits<T>()` 声明，命名用 kebab-case。〔来源：Vue 官方风格指南〕
- 组件文件一律多词命名（`BookCard.vue` 而非 `Card.vue`）。〔来源：Vue 官方风格指南「组件名为多个单词」〕
- 展示组件不发请求、不读 store，数据由 props 传入；容器职责留给 `views/`。〔来源：社区基线，用户确认 2026-09-16〕

### 样式约定

- 原生 CSS + `<style scoped>`，不引预处理器与原子化框架。〔来源：`design/*.dc.html` 九屏全部为原生 CSS〕
- 全部颜色、字号、间距走 `styles/tokens.css` 的 CSS 变量，组件内不写字面色值。〔来源：社区基线，用户确认 2026-09-16〕
- **色彩语义（硬约定，来自界面稿）**：

  | token | 值 | 归谁 | 用在哪 |
  |-------|-----|------|--------|
  | `--mark` | `#A8332A` | 人 | 引文、当前位置、主按钮、批注 |
  | `--machine` | `#3A4C78` | 机器 | 解析中、检索、模型、进度 |
  | `--doubt` | `#8A6520` / 底 `#FAF4E6` | 存疑 | 等人判断的地方 |
  | `--ink` | `#1A1E24` | 阻断 | 停下来了；整块墨底 + `#E6D9CE` 文字 |
  | `--paper` / `--surface` / `--panel` | `#F2F3EF` / `#FBFBF8` / `#EDEEE9` | — | 页面底 / 卡片底 / 侧栏底 |
  | `--rule` / `--hair` | `#D9DBD2` / `#E7E8E1` | — | 分割线 / 表格线 |

  **成功不给颜色**：解析成功是默认状态，只显示数字，不用绿色对勾。只有偏离默认（存疑、阻断、进行中）才着色。
  〔来源：`design/` 九屏界面稿第二版色彩规则，用户确认 2026-09-16〕
- 字体三档：`Noto Serif SC` 排书名与正文、`Noto Sans SC` 排界面文字、`IBM Plex Mono` 排数字与路径；数字列加 `font-variant-numeric: tabular-nums`。〔来源：`design/*.dc.html` 九屏一致使用〕
- 圆角 0–2px，不用大圆角。〔来源：`design/*.dc.html` 无一处 `border-radius` 超过 2px，开关控件除外〕
- **给分屏里的窄表加交互之前，先算列宽；优先把值本身做成开关，不另加按钮列。** ⑧屏那张表与人格编辑器分一屏宽（约 800px），加一个「换模型」按钮列、再加宽额度列，「这本书」一格就被挤成「整本装…」——不报错，只是那句最要紧的话看不全。做法：模型名本身就是开关（`qwen-plus ▾`，虚线底），点开是弹窗，列宽不变。〔来源：Todo/Plans/brain-model-picker-plan.md Slice 7 走查，用户 2026-09-22 裁定；「点开是弹窗」：用户 2026-09-23 裁定〕
- **区分平级的身份用填法（实心 / 描边 / 深浅），不借用状态色。** 三种人格是实朱 / 实墨 / 朱框空心，不是三种语义色。上表里每个颜色只回答一个问题「这是谁做的、要不要人管」；拿状态色去区分三个平级的说话人，会让其中一个看起来像出了故障。〔来源：Plan S10 规范候选 #32，用户 2026-09-20 裁定〕
- **`v-html` 内容的 `:deep()` 样式挂在 `v-html` 所在的那个容器上，不挂组件根。** 写成 `.prose :deep(p)`，编译出来是 `.prose[data-v] p`，组件自己模板里的 `<p>` 也在根下面，一起被套上，而且比它自己的 `.tail-hint[data-v]` 还高一档——不报错，只是悄悄长错：⑤屏章末那行浅灰小字提示就这样被排成了正文，读者以为是书里写的。改成 `.block :deep(p)`（`AnswerBlock` 的 `.body` 本身就是 `v-html` 容器，符合）。〔来源：阅读页章末提示样式修复（read-design-before-code），用户 2026-09-23 裁定〕

### 目录与模块组织

- `api/` 按资源分文件，与后端路由前缀对应（`books.ts` ↔ `/api/books`）。〔来源：社区基线，用户确认 2026-09-16〕
- `components/` 按领域分子目录（`book/`、`chat/`、`layout/`、`common/`），不按组件类型分。〔来源：社区基线，用户确认 2026-09-16〕
- `types/` 只放与后端契约对应的类型；组件内部类型就近定义。〔来源：社区基线，用户确认 2026-09-16〕

### 注释

- 注释解释**为什么**，尤其是反直觉的决定。〔来源：与 `backend.md` 同一约定，保持前后端一致〕
- 与后端契约相关的取舍必须写明出处（如「不用 EventSource，因为它不支持自定义请求头」）。〔来源：社区基线，用户确认 2026-09-16〕

### 文案

- **两个都叫「字数」但量的不是一回事时，界面上要用两个词。** 预算算字符（含标点与 Markdown 标记），目录算正文字数；同名会让相邻两栏的加法看起来算错，而用户会以为是程序算错了。〔来源：Plan S8 规范候选 #23，用户 2026-09-20 裁定〕
- **同一个符号在中文里兼做引用与强调时，落空的处理要分开。** 「」既用来引原文也用来强调，所以：块引用（`>`）是明确的「我在引原文」，落空要说破（「这句话在原文里没找到」）；行内「」落空要沉默，当它是强调。对着用户喊一次狼来了，下一次真有引文对不上时他已经不看那句提示了。〔来源：Plan S9 规范候选 #26，用户 2026-09-20 裁定〕
- **界面上不写实现细节与开发者内容。** 环境变量名、配置文件、CLI 命令、算法内部（BM25、分词、idf）、设计稿编号（「⑧屏」）都不进界面；要描述时只留用户据此行动、或关乎花钱与不可逆后果的那一句，理由写进代码注释。〔来源：前端文案精简（read-design-before-code），用户 2026-09-23 裁定〕

### 命名约定

| 对象 | 规范 | 举例 |
|------|------|------|
| 组件文件 | PascalCase，多词 | `BookCard.vue`、`RetrievalTable.vue` |
| 页面文件 | PascalCase + `View` 后缀 | `LibraryView.vue`、`ChatView.vue` |
| composable | `use` 前缀，camelCase | `useSSE.ts`、`useParseProgress.ts` |
| Pinia store | `use` 前缀 + `Store` 后缀 | `useAuthStore`、`useChatStore` |
| 变量 / 函数 | camelCase | `wordCount`、`loadChapter` |
| 布尔变量 | `is` / `has` / `can` 前缀 | `isStreaming`、`hasUnloadedChapters` |
| 常量 | 全大写 + 下划线 | `MAX_UPLOAD_MB` |
| CSS 变量 | `--` + kebab-case | `--mark`、`--paper` |
| CSS 类 | kebab-case | `.book-card`、`.quote-block` |
| 路由 name | kebab-case | `book-detail`、`chat-retrieval` |
| API JSON 字段 | camelCase，与后端序列化后一致，前端不转换 | `wordCount` |

## 待确认问题

1. **登录 / 注册页无界面稿。** `design/` 九屏从「初次启动」开始，未覆盖登录注册。需要补设计或按现有 token 直接实现，请裁决。
2. **【已裁决 2026-09-18】「对读」模式的成本提示。** **不弹确认。** 三路共享整本书的缓存（缓存断点在全书之后、人格之前，实测三次调用的书那一段前缀哈希逐字相同），真实成本不是三倍；而对读正是这个产品的立意，每次多一步点击会把它变成一个没人用的功能。代价写在按钮下那行小字与底部仪表上，真实账单在⑥屏。
3. **上传体积上限未定。** 需与后端一致。界面稿未标注。
4. **移动端未覆盖。** 九屏均为 1280×860 桌面尺寸，响应式断点未设计。本轮按桌面优先实现，移动端留待后续。
5. **【已裁决 2026-09-18】人格提示词编辑器（⑧ 屏右栏）的保存粒度。** 无草稿持久化（不落库、不进 localStorage；切人格标签页时 `confirm()` 拦一下）——替用户悄悄记着一份改了一半的提示词，比丢掉更糟。版本历史不做，只有**单级还原**：回到第一次从界面改动之前那一版。`backend.md` 待确认 3 同步结案：本轮全局共享，界面显式写明「改动对所有用户生效」。
6. **交叉验证结果**：本文档不逐个枚举端点（那是 `backend.md`「API 端点」的职责），只按资源划分 `api/` 模块——`books.ts` ↔ `/api/books` 与 `/api/jobs`、`formats.ts` ↔ `/api/formats`，`conversations.ts` ↔ `/api/conversations` 与 `/api/messages`，`brains.ts` ↔ `/api/brains` 与 `/api/personas`、`citations.ts` ↔ `/api/citations`、`retrieval.ts` ↔ `/api/conversations/{id}/retrieval`。三组映射覆盖了后端全部四组路由（认证组在 `client.ts` 内处理）。字段命名与「跨层命名对照」一致：前端 camelCase，不再转换。SSE 事件名（`stage` / `notice` / `token` / `answered` / `failed` / `done` / `error`）两份文档一致（`notice` 由 turn-state 收尾时加入并复核）。书里的图（`/api/books/{id}/images/{name}`）、按书列会话（`?bookId=`）与删会话都落在原有资源下，映射不变（2026-09-23 复核）。开会话改为连同第一问的 SSE（`POST /api/conversations`，201 + `Location`），仍在 `/api/conversations` 资源下，但和提问一样走 `useAsk` 而不在 `conversations.ts`（2026-09-24 复核）。2026-09-25 复核：前端交互修整没有新增或改动任何接口；`GET /api/jobs/{id}`（`api/books.getJob`）此前在前端无人调用，现在是解析进度推送断了之后的轮询兜底。2026-09-25 book-lifecycle 收尾复核：新增的 `GET /api/books/{id}/state` 落在 `/api/books` 资源下，前端在 `books.ts`（`getBookState`），类型 `BookState` 与后端 `BookStateOut` 一一对应（`bookId / status / published / jobId / stage / done / total / errorCode / message / remedy`）；其余接口只是多了 409 / 415 的 `code`，前端照样显示服务端的 `detail`；解析进度推送多了 10 分钟寿命，到点静默收尾（流结束、没有结局事件），`useImports` 照旧退回轮询，不用改。
