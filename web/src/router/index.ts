import { createRouter, createWebHistory } from 'vue-router'

import { setUnauthorizedHandler } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true }
    },
    {
      path: '/',
      name: 'library',
      component: () => import('@/views/LibraryView.vue')
    },
    {
      path: '/import',
      name: 'import',
      component: () => import('@/views/ImportView.vue')
    },
    {
      // 新会话：一张空白稿纸，问出第一句才建（会话与第一问一起落，不留空会话）。
      // 第一问的流一开就 replace 到 /chat/:id：流着的时候刷新，回到的是这个会话；
      // 后退也不会回到稿纸上，再开一个。
      path: '/books/:bookId/chat',
      name: 'chat-new',
      component: () => import('@/views/ChatView.vue')
    },
    {
      path: '/chat/:id',
      name: 'chat',
      component: () => import('@/views/ChatView.vue')
    },
    {
      // 选章明细挂在会话上，不挂在某条回答上：一轮里三种人格各答一次，
      // 却只选一次章。后续轮次复用那次决策，没有自己的明细可言。
      path: '/chat/:id/retrieval',
      name: 'chat-retrieval',
      component: () => import('@/views/RetrievalView.vue')
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue')
    },
    {
      path: '/books/:id',
      name: 'book-detail',
      component: () => import('@/views/BookDetailView.vue')
    },
    {
      path: '/books/:id/read/:idx',
      name: 'book-read',
      component: () => import('@/views/ReaderView.vue')
    },
    {
      path: '/books/:id/report',
      name: 'book-report',
      component: () => import('@/views/ReportView.vue')
    }
  ]
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  // 刷新页面后 store 是空的，但 localStorage 里可能还有 token。
  // 先向 /api/auth/me 核实一次，再决定放不放行——否则每次刷新都会被踢回登录页。
  await auth.restore()

  if (to.meta.public) {
    // 已登录还去登录页，直接回书库（或回他原本想去的地方）
    if (auth.isAuthenticated) {
      const redirect = typeof to.query.redirect === 'string' ? to.query.redirect : '/'
      return redirect
    }
    return true
  }

  if (!auth.isAuthenticated) {
    // 记下他原本想去哪，登录完送回去，而不是一律扔到首页
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  return true
})

// 请求中途 401、续期也续不上：client 已经清掉了 token，这里把人送回登录页。
setUnauthorizedHandler(() => {
  const auth = useAuthStore()
  const signedIn = auth.user !== null
  // store 里那份「已登录」也得跟着清。不清，登录页的守卫看它还是 true，又把人送回
  // 原处——人在两页之间来回弹，永远到不了登录框
  auth.logout()
  // 刷新页面时守卫里那次核实就失效了：守卫自己会带着原本要去的地方送去登录页。
  // 这里再跳一次，会抢在它前面，把那个地方换成首页
  if (!signedIn) return
  const current = router.currentRoute.value
  if (current.name !== 'login') {
    void router.replace({ name: 'login', query: { redirect: current.fullPath } })
  }
})

export default router
