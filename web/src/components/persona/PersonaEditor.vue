<script setup lang="ts">
/* 人格提示词编辑器（界面稿⑧屏右栏）。
 *
 * 这是这个产品**唯一一处让用户改模型行为的地方**。左半那张表决定接哪个模型，
 * 这里决定用什么口气读——CLAUDE.md 说得直白：「要调对话质量就改那三个 md」。
 *
 * 三件事在界面上必须说清楚，不然它们只会在事后以别的形式出现：
 *
 * 一、**改的是磁盘上那个文件。** 顶上那行 mono 写着 `prompts/scholar.md`。
 * 这三个 md 同时也能用编辑器直接改，两条路要对得上号——用户看到文件名，
 * 才不会以为网页这边存了一份私有副本。
 *
 * 二、**改动对所有用户生效。** Spec N5：本轮不建 `user_personas` 表。
 * 代码里根本没有 userId，这条只能靠这段说明存在。
 *
 * 三、**离开这一页没保存的改动会丢。** 草稿存在组件里，不落库也不进
 * localStorage——一份改了一半的人格提示词悄悄替用户记着，比丢掉更糟。
 * 所以有未保存改动时要拦一下：切标签页、点去别的页面、关掉或刷新浏览器标签，
 * 三条路都拦。原先只拦了第一条，点一下左边的图标栏，二十分钟的改动就没了。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

import { ApiError } from '@/api/client'
import { getPersona, listPersonas, restorePersona, savePersona } from '@/api/brains'
import type { Persona, PersonaKey } from '@/types'

const rows = ref<Persona[]>([])
const current = ref<PersonaKey>('scholar')
/** 编辑框里的字。服务端那一版在 `saved`，两者不等就是「已改动，未保存」。 */
const draft = ref('')
const saved = ref('')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
/** 保存成功不给颜色，只让那行状态字回到「已存」——见 tokens.css 那条。 */
const note = ref('')

const one = computed(() => rows.value.find((r) => r.key === current.value))
const dirty = computed(() => draft.value !== saved.value)

async function loadList() {
  try {
    rows.value = await listPersonas()
    await loadOne(current.value)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '人格列表读不出来。'
  }
}

async function loadOne(key: PersonaKey) {
  loading.value = true
  error.value = ''
  note.value = ''
  try {
    const got = await getPersona(key)
    draft.value = got.text
    saved.value = got.text
    rows.value = rows.value.map((r) => (r.key === key ? { ...got, text: '' } : r))
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '这段提示词读不出来。'
  } finally {
    loading.value = false
  }
}

function switchTo(key: PersonaKey) {
  if (key === current.value) return
  // 切走就丢，所以先问一句。这里是少数几个值得拦一下的地方：
  // 用户可能已经在这个框里写了二十分钟。
  if (dirty.value && !window.confirm('这一段改了还没保存，切过去就丢了。确定切？')) return
  current.value = key
}

async function save() {
  busy.value = true
  error.value = ''
  note.value = ''
  try {
    const got = await savePersona(current.value, draft.value)
    saved.value = got.text
    draft.value = got.text
    rows.value = rows.value.map((r) => (r.key === got.key ? { ...got, text: '' } : r))
    note.value = '已存。下一轮对话就用新的。'
  } catch (e) {
    // 占位符拼错了服务端会 422 把那句话说清楚（哪个占位符、能用的有哪些）。
    // 原样给用户看——这一步的价值全在那句话里。
    error.value = e instanceof ApiError ? e.message : '这段提示词没能存进去。'
  } finally {
    busy.value = false
  }
}

async function restore() {
  if (!window.confirm('还原到出厂版？当前这一版会被覆盖。')) return
  busy.value = true
  error.value = ''
  note.value = ''
  try {
    const got = await restorePersona(current.value)
    saved.value = got.text
    draft.value = got.text
    rows.value = rows.value.map((r) => (r.key === got.key ? { ...got, text: '' } : r))
    note.value = '已还原。'
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '没能还原。'
  } finally {
    busy.value = false
  }
}

/** Ctrl+S / ⌘S 就地保存：编辑器里改完顺手一按，不必去找按钮，也不触发浏览器的「另存网页」 */
function saveByKey() {
  if (dirty.value && !busy.value) void save()
}

/** 关掉、刷新浏览器标签时，浏览器只肯弹它自己那句话；这里只负责告诉它「有东西会丢」 */
function holdUnload(event: BeforeUnloadEvent) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

onBeforeRouteLeave(
  () => !dirty.value || window.confirm('人格提示词改了还没保存，离开就丢了。确定离开？')
)

watch(current, (key) => void loadOne(key))
onMounted(() => {
  window.addEventListener('beforeunload', holdUnload)
  void loadList()
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', holdUnload))
</script>

<template>
  <aside class="pane">
    <header class="head">
      <h2>人格</h2>
    </header>

    <div class="tabs">
      <button
        v-for="row in rows"
        :key="row.key"
        type="button"
        class="tab"
        :class="{ 'tab--on': row.key === current }"
        @click="switchTo(row.key)"
      >
        {{ row.name }}
      </button>
    </div>

    <div class="body">
      <div class="status">
        <span class="mono file">{{ one?.filename ?? '' }}</span>
        <span class="grow" />
        <span v-if="dirty" class="mono flag flag--dirty">已改动，未保存</span>
        <span v-else-if="note" class="mono flag">{{ note }}</span>
        <span v-else-if="one?.edited" class="mono flag">已改过出厂版</span>
      </div>

      <textarea
        v-model="draft"
        class="mono editor"
        spellcheck="false"
        :disabled="loading || busy"
        :aria-label="`${one?.name ?? ''}的提示词`"
        @keydown.ctrl.s.prevent="saveByKey"
        @keydown.meta.s.prevent="saveByKey"
      />

      <p v-if="error" class="alert" role="alert">{{ error }}</p>

      <section class="note">
        <p>
          <span class="mono">{{ '{title}' }}</span> 这类占位符会自动填入书的信息。
        </p>
        <p class="note-sub">改动对<b>所有用户</b>生效。</p>
      </section>

      <div class="actions">
        <button class="save" type="button" :disabled="!dirty || busy" @click="save">
          {{ busy ? '存着…' : '保存' }}
        </button>
        <button class="ghost" type="button" :disabled="!one?.edited || busy" @click="restore">
          还原
        </button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.pane {
  width: 418px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--panel);
  border-left: 1px solid var(--rule);
}

.head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 24px 24px 15px;
  border-bottom: 1px solid var(--rule);
}

h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 20px;
  letter-spacing: 0.04em;
}

.tabs {
  display: flex;
  padding: 0 24px;
  border-bottom: 1px solid var(--rule);
}

.tab {
  padding: 10px 13px 9px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  font-family: var(--font-serif);
  font-weight: 500;
  font-size: 13.5px;
  color: var(--text-secondary);
}

/* 朱批 = 你选的那个。选人格是人做的动作 */
.tab--on {
  font-weight: 700;
  color: var(--mark);
  border-bottom-color: var(--mark);
}

.body {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  gap: 11px;
  padding: 16px 24px 20px;
  min-height: 0;
}

.status {
  display: flex;
  align-items: center;
  gap: 10px;
}

.file {
  font-size: 10.5px;
  color: var(--text-muted);
}

.grow {
  flex-grow: 1;
}

.flag {
  font-size: 10.5px;
  color: var(--text-muted);
}

/* 黄檗 = 等人判断。这一段字此刻只在这个浏览器里活着 */
.flag--dirty {
  color: var(--doubt);
}

.editor {
  flex-grow: 1;
  min-height: 0;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--edge-strong);
  border-radius: var(--radius);
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--text-prose);
  resize: none;
}

.editor:focus {
  outline: none;
  border-color: var(--mark);
}

.alert {
  margin: 0;
  padding: 10px 13px;
  background: var(--doubt-bg);
  border-left: 2px solid var(--doubt);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--doubt);
}

.note {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 13px;
  background: var(--surface);
  border: 1px solid var(--edge);
}

.note p {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--text-secondary);
}

.note b {
  font-weight: 500;
  color: var(--text);
}

.note .mono {
  font-size: 11px;
}

.note-sub {
  color: var(--text-muted) !important;
}

.actions {
  display: flex;
  gap: 8px;
}

.save {
  flex-grow: 1;
  height: 34px;
  background: var(--mark);
  border: none;
  color: var(--surface);
  font-size: 12.5px;
  font-weight: 500;
}

.save:disabled {
  background: var(--edge-strong);
  color: var(--text-muted);
}

.ghost {
  height: 34px;
  padding: 0 14px;
  background: var(--surface);
  border: 1px solid var(--edge-strong);
  color: var(--text-secondary);
  font-size: 12.5px;
}

.ghost:disabled {
  color: var(--text-faint);
}
</style>
