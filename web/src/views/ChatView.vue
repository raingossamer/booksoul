<script setup lang="ts">
/* 对话（界面稿⑦屏，单人格那一列）。
 *
 * 处境模式是 S11。这一屏走通的是：**选一本书、选一种（或三种）人格、
 * 逐字看到回答、引文能点回原文、断了也不丢**。
 *
 * 对读（S10）是第四个格子，不是第四种人格：同一个问题三列并排，看完挑一路
 * 接着聊。**点它不弹确认**——三路共享整本书的缓存，真实成本不是三倍，
 * 而对读正是这个产品的立意；每次多一步点击会把它变成一个没人用的功能。
 *
 * **会话从第一问开始存在。** 「开始对话」进来的是一张空白稿纸（`blank`）：书、大脑、
 * 人格、处境模式都先记在这一页，第一问连同它们一起交给服务端，会话与第一问一起落下。
 * 先建后问的话，点进来又走掉就留下一个空会话。
 *
 * 两处刻意的克制：
 *
 * 一、**流断了不自动重发。** 自动重试看起来贴心，实际是让同一个问题计两次费。
 * 断了就停下，把已经收到的留在屏幕上，让用户自己决定（Spec #38、#39）。
 *
 * 二、**问不出去时不让人先打完一整段问题才发现。** 要 key 而没填，输入框直接禁用，
 * 并给一条去设置页的路。本机的大脑不要 key：按服务端给的 `needsKey` 判，
 * 不按「存没存 key」判（`lib/brainKeys` 的 `isReady`）。
 *
 * 问出去之后的几件小事，各自对应一个原先踩过的坑：流着的时候跟着往下滚（但人往上翻
 * 就不拽他）；服务端没接下的问题把字还给输入框；一路一个字都没说就失败的原因留在
 * 那一轮底下；输入法拼字时的回车不算发送。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getBook } from '@/api/books'
import { listBrains } from '@/api/brains'
import { ApiError } from '@/api/client'
import { getConversation, pickPersona, setReflection } from '@/api/conversations'
import AnswerBlock from '@/components/chat/AnswerBlock.vue'
import PersonaTabs from '@/components/chat/PersonaTabs.vue'
import IconRail from '@/components/layout/IconRail.vue'
import { useAsk } from '@/composables/useAsk'
import { brainForNewChat, isReady } from '@/lib/brainKeys'
import { isImeEnter } from '@/lib/keys'
import type { Brain, ChatMessage, Conversation, PersonaKey } from '@/types'

/** 还没问出第一句的新会话：库里还没有这一行，这些都只是打算。 */
interface Blank {
  bookId: number
  bookTitle: string
  chapterCount: number
  wordCount: number
}

const ALL: PersonaKey[] = ['scholar', 'author', 'book']
/** 离底部多近算「贴着底」：留出最后一行的行高与滚动条取整的余量 */
const NEAR_BOTTOM = 48
/** 输入框最多长到多高，再多就在框里滚 */
const INPUT_MAX = 200

const route = useRoute()
const router = useRouter()

const conversation = ref<Conversation | null>(null)
const blank = ref<Blank | null>(null)
const brains = ref<Brain[]>([])
const draft = ref('')
/** 正在问的那一句。要等说完回头拉一次，它才在会话里；流着的时候先显示这一份。 */
const asked = ref('')
const error = ref('')
const scroller = ref<HTMLElement | null>(null)
const input = ref<HTMLTextAreaElement | null>(null)
const {
  streaming,
  settled,
  failures,
  columns,
  stage,
  notices,
  asking,
  error: askError,
  blocked,
  ask,
  stop
} = useAsk()

/**
 * 下一问用哪一种人格。点标签只改它，问出去时随提问一起交上去——服务端在写下
 * 这一问的同一个事务里换掉会话的主干人格。
 *
 * 原先点标签只改了屏幕上那份会话，请求里不带人格：服务端照旧用原来那个答，
 * 字流进另一列，点中的这一列一直空着，说完一拉，标签又跳回去。
 */
const chosen = ref<PersonaKey>('scholar')
/** 下一问用对读吗。它是一个模式，不是第四种人格，所以单独存。 */
const comparing = ref(false)
/** 新会话的处境模式：会话还没有，无处可存，先记在这里，第一问时一起交上去。 */
const blankReflection = ref(false)
/**
 * 上一问问在哪个会话、问的是什么。一路一个字都没说就失败时服务端什么都不存，失败的
 * 原因只在那条流里说过一遍——凭它把原因挂回拉回来的那一轮底下（见 `lost`）。
 */
const lastAsked = ref<{ conversationId: number; question: string } | null>(null)
/**
 * 自己刚换过去的会话地址。第一问的流一开，地址就从 `/books/:id/chat` 换成
 * `/chat/:id`；这次换地址引起的重新加载要跳过——流还在走，说完 `send` 自会拉一次。
 */
let adopted: number | null = null
/**
 * 贴着底：新字来了跟着往下滚。人往上翻去读前面的，就不再把他拽回底部；自己翻回来，
 * 又接着跟。原先流着的时候一下都不滚——问出去的那一句、正在长的回答都在视线下面。
 */
let following = true
/** 这一页还在。离开之后迟到的回调（流的收尾、回头那一拉）不必再动它 */
let alive = true

/**
 * 稿纸用哪个大脑：与书籍详情标出来的是同一个（`brainForNewChat`）。列表到了就按列表
 * 核一遍——选的那个自定义端点已经删掉了，就不拿一个不存在的名字去开会话。
 */
const blankBrain = computed(() => brainForNewChat(brains.value.map((b) => b.name)))
const brainName = computed(
  () => conversation.value?.brainName ?? (blank.value ? blankBrain.value : '')
)
const brain = computed(() => brains.value.find((b) => b.name === brainName.value))
/**
 * 能不能问。列表里认不出这个大脑（没拉到、自定义端点被删了）就不拦：
 * 让服务端说它那句实话，而不是替它猜一句「没填 key」。
 */
const canAsk = computed(() => !brain.value || isReady(brain.value))
const reflecting = computed(() => conversation.value?.reflection ?? blankReflection.value)
/** 这一轮流出过字。说完到回头拉回来之间，刚说完的那段靠它留在屏幕上。 */
const spoke = computed(() => Object.values(streaming.value).some(Boolean))
/** 会话的主干人格。旧回答没记人格时按它显示。 */
const trunk = computed(() => conversation.value?.persona ?? chosen.value)
const model = computed(() => conversation.value?.model || brain.value?.model || brainName.value)

/** 顶栏：有会话读会话的，还是稿纸就读稿纸的。 */
const head = computed(() => {
  const c = conversation.value
  if (c) return { title: c.bookTitle, chapters: c.bookChapterCount, words: c.bookWordCount }
  const b = blank.value
  if (b) return { title: b.bookTitle, chapters: b.chapterCount, words: b.wordCount }
  return null
})

/** 书名就是回书籍详情的路。原先对话页没有回那本书的入口，只能按浏览器的后退 */
const bookHref = computed(() => {
  const id = conversation.value?.bookId ?? blank.value?.bookId
  return id ? `/books/${id}` : ''
})

/** 按 turn 聚成一问一答。S10 的对读会让一个 turn 带三条回答，这个形状留着。 */
const turns = computed(() => {
  const out: { turn: number; question: string; answers: ChatMessage[] }[] = []
  for (const message of conversation.value?.messages ?? []) {
    let bucket = out.find((t) => t.turn === message.turn)
    if (!bucket) {
      bucket = { turn: message.turn, question: '', answers: [] }
      out.push(bucket)
    }
    if (message.role === 'user') bucket.question = message.content
    else bucket.answers.push(message)
  }
  return out
})

const lastTurn = computed(() => turns.value[turns.value.length - 1] ?? null)

/** 最后那一轮就是这一页刚问完的那一问：它的结局这一页从头看到了尾。 */
const justAsked = computed(() => {
  const turn = lastTurn.value
  const last = lastAsked.value
  return (
    turn !== null &&
    last !== null &&
    conversation.value?.id === last.conversationId &&
    turn.question === last.question
  )
})

/**
 * 最后那一轮里一个字都没说就失败的那几路（key 被拒、连不上……）。服务端一个字都没收到
 * 就什么都不存，拉回来的会话里没有它们——原先说完一拉，那句失败的原因跟着那一列一起
 * 消失，只剩一个没人答的问题。原因留在那一轮底下，直到下一问。
 */
const lost = computed(() => {
  const turn = lastTurn.value
  if (!turn || !justAsked.value || asking.value) return []
  const answered = new Set(turn.answers.map((a) => a.persona ?? trunk.value))
  return columns.value.flatMap((who) => {
    const why = failures.value[who]
    return why && !answered.has(who) ? [{ who, why }] : []
  })
})

/**
 * 最后那一问没留下任何回答：一路都没说出字，或者它还在回答时页面刷新了（那一轮在服务端
 * 照样跑，只是这一页没接着那条流）。给一个「重问」：还在答的话服务端会把它挂回正在说的
 * 那一段（同一个问题不再调模型），不在答了才是真的再问一次。
 */
const orphan = computed(() => !asking.value && !asked.value && lastTurn.value?.answers.length === 0)

/** 这一轮排几列：存下来的回答，加上最后一轮里没存下来的那几路 */
function widthOf(turn: { answers: ChatMessage[] }): number {
  return turn.answers.length + (turn === lastTurn.value ? lost.value.length : 0)
}

const usage = computed(() => {
  const answers = (conversation.value?.messages ?? []).filter((m) => m.role === 'assistant')
  const sum = (pick: (m: ChatMessage) => number) => answers.reduce((n, m) => n + pick(m), 0)
  return {
    input: sum((m) => m.inputTokens),
    output: sum((m) => m.outputTokens),
    cacheRead: sum((m) => m.cacheRead)
  }
})

function toBottom() {
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

function onScroll() {
  const el = scroller.value
  if (el) following = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM
}

/**
 * 读进一个会话之后停在哪：从阅读页「回到对话」回来的带着 `#m-<回答 id>`，落回点「去原文」
 * 的那条回答上——原先一律滚到底，读到一半去核对原文，回来就找不着刚才读到哪了。
 * 别的情况到底部，接着往下问。
 */
async function settleScroll() {
  await nextTick()
  const anchor = route.hash.startsWith('#m-') ? document.getElementById(route.hash.slice(1)) : null
  if (anchor) {
    anchor.scrollIntoView({ block: 'center' })
    onScroll()
    return
  }
  following = true
  toBottom()
}

/** 光标放进输入框。人已经点到别处去了（回答里的一个链接之类）就不抢。 */
function focusInput() {
  const el = input.value
  const active = document.activeElement
  if (!el || el.disabled || (active && active !== document.body)) return
  el.focus({ preventScroll: true })
}

/** 输入框跟着字长高，到上限为止，再多就在框里滚。 */
function fitInput() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  // scrollHeight 不含边框，而高度按 border-box 算：差的那两像素会让一行字也出滚动条
  const border = el.offsetHeight - el.clientHeight
  el.style.height = `${Math.min(el.scrollHeight + border, INPUT_MAX)}px`
}

function personaOf(raw: unknown): PersonaKey {
  return ALL.find((key) => key === raw) ?? 'scholar'
}

async function loadBrains() {
  try {
    brains.value = await listBrains()
  } catch {
    // 拉不到就不拦：能不能问交给服务端去说（见 canAsk）
    brains.value = []
  }
}

async function load() {
  const id = Number(route.params.id)
  if (route.name === 'chat' && id === adopted) {
    adopted = null
    return
  }
  error.value = ''
  comparing.value = false
  lastAsked.value = null
  try {
    if (route.name === 'chat-new') {
      const bookId = Number(route.params.bookId)
      const book = await getBook(bookId)
      conversation.value = null
      blank.value = {
        bookId,
        bookTitle: book.title,
        chapterCount: book.chapterCount,
        wordCount: book.wordCount
      }
      chosen.value = personaOf(route.query.persona)
      blankReflection.value = false
      await nextTick()
      focusInput()
      return
    }
    const loaded = await getConversation(id)
    conversation.value = loaded
    blank.value = null
    chosen.value = loaded.persona
    await settleScroll()
    focusInput()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '这个会话打不开。'
  }
}

/** 问完回头拉一次：以服务端落下来的为准。 */
async function refresh(id: number) {
  try {
    conversation.value = await getConversation(id)
    blank.value = null
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '这个会话没能重新读出来。刷新一下页面看看。'
  }
  await nextTick()
  if (following) toBottom()
}

/** 服务端接下了第一问：会话有了 id。流还在走，地址先换过去。 */
function adopt(id: number, bookId: number) {
  // 流开的那一刻人已经不在这张稿纸上了（点去了别处）：不把他拽回来
  if (route.name !== 'chat-new' || Number(route.params.bookId) !== bookId) return
  adopted = id
  void router.replace(`/chat/${id}`)
}

async function send(question: string, reselect = false) {
  const target = conversation.value
  const fresh = blank.value
  if ((!target && !fresh) || !question.trim() || asking.value) return
  const compare = comparing.value
  const personas = compare ? ALL : [chosen.value]
  draft.value = ''
  asked.value = question
  // 问出去的这一句得看得见：刚才翻到了哪都回到底部（滚动由下面那个 watch 在渲染后做）
  following = true
  // 服务端接下这一问的那一刻（流开了）才有落处。没接下就什么都没存
  let landed = 0
  try {
    if (target) {
      await ask({ conversationId: target.id }, question, {
        brain: target.brainName,
        // 对读不换主干：那是「跟这个继续聊」那个明确动作的事
        persona: compare ? undefined : chosen.value,
        reselect,
        compare,
        personas,
        onAccepted: (id) => {
          landed = id
        }
      })
    } else if (fresh) {
      const brain = blankBrain.value
      await ask(
        {
          start: {
            bookId: fresh.bookId,
            persona: chosen.value,
            brainName: brain,
            reflection: blankReflection.value
          }
        },
        question,
        {
          brain,
          compare,
          personas,
          onAccepted: (id) => {
            landed = id
            adopt(id, fresh.bookId)
          }
        }
      )
    }
    if (!alive) return
    if (!landed) {
      // 服务端没接下这一问（上一问还在答、没填 key、书在解析……），什么都没存下。
      // 字还给输入框：改一改、或者等一会儿再发，不用重打一遍。新会话也就没建，稿纸原样留着
      if (!draft.value) draft.value = question
      return
    }
    lastAsked.value = { conversationId: landed, question }
    // 不管说完没说完，都以服务端落下来的那条为准：残文也在库里，
    // 屏幕上的和刷新之后看到的必须是同一段字
    await refresh(landed)
  } finally {
    asked.value = ''
  }
}

/** 回车发出去；Shift+回车换行。 */
function onEnter(event: KeyboardEvent) {
  // 输入法还在拼字时按下的回车是「选这个词」，不是「发出去」。原先用中文输入法敲一个
  // 英文词、按回车上屏，问题就被半截发了出去——而那一下已经在花钱
  if (isImeEnter(event)) return
  event.preventDefault()
  void send(draft.value)
}

function pick(next: PersonaKey) {
  comparing.value = false
  chosen.value = next
}

/**
 * 处境模式（S11）：问的是「我」，不是「书」。
 *
 * 落库而不是只存在这个页面里 —— 下一轮的系统提示词由服务端拼，它读的是
 * `conversations.reflection`。只存在前端的话，刷新一下这个开关就悄悄关了，
 * 而用户以为它还开着。新会话还没有这一行，先记着，第一问时一起交上去。
 *
 * 拨它**不废缓存**：那一段附在人格之后，而缓存断点在全书之后、人格之前。
 * 所以它不需要像「重新挑章节」那样先问一句。
 */
async function reflect(on: boolean) {
  if (asking.value) return
  const target = conversation.value
  if (!target) {
    blankReflection.value = on
    return
  }
  try {
    conversation.value = await setReflection(target.id, on)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '这个开关没拨动。'
  }
}

/** 「跟这个继续聊」：把主干人格换成这一路。旁支一个字都不删，界面上仍然在。 */
async function follow(next: PersonaKey) {
  const target = conversation.value
  if (!target || asking.value) return
  comparing.value = false
  try {
    const updated = await pickPersona(target.id, next)
    conversation.value = updated
    chosen.value = updated.persona
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '没能换成这一路。'
  }
}

/** 用户点头重挑一次。原样再发那个被拦下来的问题，这次带上 reselect。 */
function reselect() {
  const pending = blocked.value
  if (pending) void send(pending.question, true)
}

/** 重问：把这一轮的问题原样再发一次，模型会重新答。 */
function retry(turn: { question: string }) {
  void send(turn.question)
}

/** 接着往下：把残文交回去，让它从断掉的地方续。 */
function resume(turn: { question: string; answers: ChatMessage[] }) {
  const partial = turn.answers[turn.answers.length - 1]?.content ?? ''
  void send(`接着往下说。你刚才说到这里就断了：\n\n${partial}`)
}

onMounted(() => {
  void loadBrains()
  void load()
})
watch(() => route.fullPath, load)

// 流着的时候跟着往下：问出去的那一句、每来一段字、换一句「正在挑章节」、挂上一条失败，
// 都看一眼要不要滚。贴着底才滚——人往上翻着读，就不去拽他
watch(
  [asked, streaming, stage, failures],
  () => {
    if (following) toBottom()
  },
  { flush: 'post' }
)
// 说完了，光标回到输入框：接着问不用先去点一下（问的时候它是禁用的，焦点早丢了）
watch(asking, (now, before) => {
  if (before && !now) void nextTick(focusInput)
})
watch(draft, fitInput, { flush: 'post' })

// 离开这一页就不再读这条流。服务端那一轮照样跑完、落库，回来时拉到的就是它
onBeforeUnmount(() => {
  alive = false
  stop()
})
</script>

<template>
  <div class="shell">
    <IconRail />

    <main class="main">
      <header class="top">
        <RouterLink v-if="bookHref" class="book" :to="bookHref" title="回书籍详情">
          {{ head?.title || '对话' }}
        </RouterLink>
        <span v-else class="book">对话</span>
        <span class="mono shape" v-if="head">
          全书 {{ head.chapters }} 节 · {{ head.words.toLocaleString() }} 字
        </span>
        <span class="grow" />
        <span class="mono brain">
          <span class="dot" />
          {{ model }}
        </span>
        <RouterLink class="cog" to="/settings">设置</RouterLink>
      </header>

      <PersonaTabs
        :current="chosen"
        :comparing="comparing"
        :reflection="reflecting"
        :disabled="asking"
        @pick="pick"
        @compare="comparing = true"
        @reflect="reflect"
      />

      <p v-if="error" class="banner" role="alert">{{ error }}</p>
      <!-- 这一轮有什么不一样（如「这本书重新解析过了，这次按全价算」）。放在对话流
           之外：它说的是整一轮，答完了也还成立，不该跟着那一列一起滚走。
           措辞是服务端写的，这里照样显示 -->
      <p v-for="(notice, i) in notices" :key="`${i}-${notice.code}`" class="banner" role="status">
        {{ notice.text }}
      </p>

      <div ref="scroller" class="thread" @scroll="onScroll">
        <section v-for="turn in turns" :key="turn.turn" class="turn">
          <div class="question">{{ turn.question }}</div>
          <!-- 一轮三条回答就是一次对读：并排看才讲得清差别 -->
          <div
            v-if="widthOf(turn)"
            class="answers"
            :class="{ 'answers--three': widthOf(turn) > 1 }"
          >
            <AnswerBlock
              v-for="answer in turn.answers"
              :key="answer.id"
              :message="answer"
              :persona="answer.persona ?? trunk"
              :busy="asking"
              :column="widthOf(turn) > 1"
              @retry="retry(turn)"
              @resume="resume(turn)"
              @pick="follow(answer.persona ?? trunk)"
            />
            <!-- 一个字都没说就失败的那几路：服务端没存，原因只在刚才那条流里说过 -->
            <template v-if="turn === lastTurn">
              <AnswerBlock
                v-for="one in lost"
                :key="`lost-${one.who}`"
                :persona="one.who"
                :failure="one.why"
                :column="widthOf(turn) > 1"
              />
            </template>
          </div>
          <!-- 黄檗 = 等你判断：要不要再问一次，由人决定 -->
          <div v-if="turn === lastTurn && orphan" class="orphan">
            <!-- 结局这一页没看见（刷新过、从列表点进来）才可能还在答；刚问完的就不是 -->
            <span class="orphan-text">
              这一问没有留下回答。
              <template v-if="!justAsked">还在回答的话，重问会接上它，不重复计费。</template>
            </span>
            <button class="orphan-act" type="button" @click="retry(turn)">重问</button>
          </div>
        </section>

        <!-- 正在问的这一轮。说完之后回头拉一次，它才进上面那串；拉回来之前一直留着，
             不闪一下空白 -->
        <section v-if="asking || asked" class="turn">
          <div v-if="asked" class="question">{{ asked }}</div>
          <div
            v-if="asking || spoke"
            class="answers"
            :class="{ 'answers--three': columns.length > 1 }"
          >
            <AnswerBlock
              v-for="who in columns"
              :key="who"
              :streaming="streaming[who] ?? ''"
              :stage="settled[who] ? '' : stage"
              :persona="who"
              :column="columns.length > 1"
              :failure="failures[who]"
            />
          </div>
        </section>

        <p v-if="askError && !asking" class="banner banner--cut" role="alert">
          {{ askError }}
        </p>

        <!-- 问到了这次没载入的章节。这一问还没发出去，一个 token 都没花；
             要不要花那笔重选的钱，由用户点头。 -->
        <div v-if="blocked && !asking" class="relock" role="alert">
          <p class="relock-text">{{ blocked.message }}</p>
          <div class="relock-acts">
            <button class="relock-go" type="button" @click="reselect">重新挑章节</button>
            <RouterLink
              v-if="conversation"
              class="relock-see"
              :to="`/chat/${conversation.id}/retrieval`"
            >
              先看看这次读了哪几节
            </RouterLink>
          </div>
        </div>
      </div>

      <div class="ask">
        <p v-if="!canAsk" class="nokey">
          还没填 <span class="mono">{{ brainName }}</span> 的 key。
          <RouterLink to="/settings">去设置页填一个</RouterLink>
        </p>
        <form class="box" @submit.prevent="send(draft)">
          <textarea
            ref="input"
            v-model="draft"
            class="input"
            rows="2"
            :disabled="!head || !canAsk || asking"
            :placeholder="asking ? '正在回答…' : '问点什么'"
            @keydown.enter.exact="onEnter"
          />
          <button class="go" type="submit" :disabled="!head || !canAsk || asking || !draft.trim()">
            {{ asking ? '说着…' : '问' }}
          </button>
        </form>
      </div>

      <footer class="meter">
        <span v-if="comparing" class="mono compare-note">对读一次 = 三次调用</span>
        <RouterLink
          v-if="conversation?.retrieval"
          class="mono read"
          :to="`/chat/${conversation.id}/retrieval`"
        >
          <template v-if="conversation.retrieval.wholeBook">
            整本载入 · {{ conversation.retrieval.totalCount }} 节
          </template>
          <template v-else>
            读了 {{ conversation.retrieval.selectedCount }}/{{ conversation.retrieval.totalCount }}
            节
          </template>
          <span class="see">· 看怎么挑的</span>
        </RouterLink>
        <span v-else class="mono">还没问过</span>
        <span class="mono sep">│</span>
        <span class="mono">输入 {{ usage.input.toLocaleString() }}</span>
        <span class="mono">输出 {{ usage.output.toLocaleString() }}</span>
        <span v-if="usage.cacheRead" class="mono">
          缓存命中 {{ usage.cacheRead.toLocaleString() }}
        </span>
      </footer>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  background: var(--paper);
  overflow: hidden;
}

.main {
  flex-grow: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.top {
  display: flex;
  align-items: baseline;
  gap: 13px;
  padding: 20px 30px 14px;
  border-bottom: 1px solid var(--rule);
}

.book {
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 18px;
  letter-spacing: 0.04em;
  color: var(--text);
  text-decoration: none;
}

a.book:hover {
  color: var(--mark);
}

.shape {
  font-size: 10.5px;
  color: var(--text-muted);
}

.grow {
  flex-grow: 1;
}

.brain {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 11px;
  color: var(--text-secondary);
}

/* 靛 = 机器。这一格说的是「现在接的是谁」 */
.dot {
  width: 6px;
  height: 6px;
  background: var(--machine);
}

.cog {
  margin-left: 14px;
  font-size: 11.5px;
  color: var(--text-muted);
  text-decoration: none;
  border-bottom: 1px solid var(--edge-strong);
}

.banner {
  margin: 14px 30px 0;
  padding: 11px 15px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  color: var(--doubt);
  font-size: 12px;
  line-height: 1.8;
}

.banner--cut {
  margin: 0;
}

.thread {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding: 22px 30px 26px;
  overflow-y: auto;
}

.turn {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 760px;
}

/* 对读那一轮要占满宽度，三列才放得下 */
.turn:has(.answers--three) {
  max-width: none;
}

.answers {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.answers--three {
  flex-direction: row;
  align-items: flex-start;
  gap: 14px;
}

/* 靛 = 机器要做的事。这一行说的是「下一问会发三次请求」 */
.compare-note {
  color: var(--machine);
}

/* 朱批 = 人说的话 */
.question {
  align-self: flex-start;
  max-width: 100%;
  padding: 10px 15px;
  background: var(--mark-bg);
  border-left: 2px solid var(--mark);
  font-size: 13.5px;
  line-height: 1.85;
  color: #7e2a22;
  white-space: pre-wrap;
}

.ask {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 30px 14px;
  border-top: 1px solid var(--rule);
  background: var(--panel);
}

.nokey {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--doubt);
}

.nokey a {
  color: var(--doubt);
}

/* 输入框跟着字长高；按钮留在底边，不跟着拉成一根柱子 */
.box {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.input {
  flex-grow: 1;
  min-height: 64px;
  max-height: 200px;
  padding: 9px 12px;
  background: var(--surface);
  border: 1px solid var(--edge-strong);
  font-family: var(--font-sans);
  font-size: 13px;
  line-height: 1.7;
  color: var(--text);
  resize: none;
  overflow-y: auto;
}

.input:focus {
  outline: none;
  border-color: var(--mark);
}

.go {
  flex-shrink: 0;
  width: 66px;
  height: 64px;
  background: var(--mark);
  border: none;
  color: var(--surface);
  font-size: 13px;
  font-weight: 500;
}

.go:disabled {
  background: var(--edge-strong);
  color: var(--text-faint);
}

.meter {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 9px 30px 11px;
  border-top: 1px solid var(--rule);
  font-size: 10.5px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.sep {
  color: var(--edge-strong);
}

.read {
  color: var(--text-muted);
  text-decoration: none;
}

/* 朱批 = 人可以点的。这一格是「我要自己去查」的入口 */
.see {
  margin-left: 6px;
  color: var(--mark);
  border-bottom: 1px solid var(--mark-bg);
}

/* 最后那一问没留下回答 */
.orphan {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 15px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
}

.orphan-text {
  flex-grow: 1;
  font-size: 12px;
  line-height: 1.8;
  color: var(--doubt);
}

.orphan-act {
  flex-shrink: 0;
  padding: 0 0 1px;
  background: none;
  border: none;
  border-bottom: 1px solid var(--doubt);
  color: var(--doubt);
  font-size: 12px;
  white-space: nowrap;
}

/* 黄檗 = 等你判断。这不是错误，是一个要你决定花不花钱的岔路 */
.relock {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 760px;
  padding: 12px 15px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
}

.relock-text {
  margin: 0;
  font-size: 12px;
  line-height: 1.85;
  color: #6b5218;
}

.relock-acts {
  display: flex;
  align-items: center;
  gap: 16px;
}

.relock-go {
  padding: 5px 13px;
  background: var(--doubt);
  border: none;
  color: var(--surface);
  font-size: 12px;
}

.relock-see {
  font-size: 11.5px;
  color: var(--doubt);
}
</style>
