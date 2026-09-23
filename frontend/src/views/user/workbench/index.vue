<template>
  <div class="workbench">
    <section class="welcome">
      <div class="eyebrow">
        你的内容，值得再打磨一次
      </div>
      <h2>让每一次表达，更准确。</h2>
      <p>从一段文字到一份文档，发现问题，逐条确认，安心交付。</p>
    </section>
    <section
      class="start-grid"
      aria-label="开始审校"
    >
      <router-link
        class="start-card primary-card"
        to="/proofread/text"
      >
        <div class="card-icon">
          <el-icon><EditPen /></el-icon>
        </div>
        <h3>开始一次审校 <span>↗</span></h3>
        <p>粘贴或输入文本，检查文字、语法与表达。</p>
        <span class="card-action">输入文本 <span>→</span></span>
      </router-link>
      <router-link
        class="start-card"
        to="/proofread/document"
      >
        <div class="card-icon">
          <el-icon><Document /></el-icon>
        </div>
        <h3>审校一份文档 <span>↗</span></h3>
        <p>上传 Word、PDF 或 TXT，逐条处理修改建议。</p>
        <span class="card-action">上传文档 <span>→</span></span>
      </router-link>
    </section>
    <section class="recent-section">
      <div class="section-heading">
        <div><h3>继续上次的工作</h3><p>已保存的审校记录，随时回来继续处理。</p></div><router-link
          v-if="user.isLoggedIn"
          to="/history"
        >
          全部记录 →
        </router-link>
      </div>
      <div
        v-if="!user.isLoggedIn"
        class="empty-state"
      >
        <el-icon><FolderOpened /></el-icon><h4>登录后，审校记录随时可查</h4><p>你仍可直接开始审校，登录后可保存草稿和版本。</p><el-button @click="router.push('/login')">
          登录账号
        </el-button>
      </div>
      <div
        v-else-if="loading"
        class="empty-state"
        role="status"
      >
        正在读取最近的审校记录…
      </div>
      <div
        v-else-if="error"
        class="empty-state"
        role="alert"
      >
        <p>暂时无法读取记录，请稍后重试。</p><el-button @click="loadRecent">
          重新加载
        </el-button>
      </div>
      <div
        v-else-if="!recent.length"
        class="empty-state"
      >
        <el-icon><FolderOpened /></el-icon><h4>从第一份内容开始</h4><p>完成审校后，记录会出现在这里。</p><el-button
          plain
          @click="router.push('/proofread/text')"
        >
          新建审校
        </el-button>
      </div>
      <div
        v-else
        class="recent-list"
      >
        <button
          v-for="item in recent"
          :key="item.id"
          class="recent-item"
          @click="openRecord(item)"
        >
          <span class="file-icon"><el-icon><Document /></el-icon></span>
          <span class="record-copy"><strong>{{ item.source_filename || item.text_preview?.slice(0, 48) || '未命名文本' }}</strong><span>{{ item.type === 'document' ? '文档审校' : '文本审校' }} · {{ formatDate(item.created_at) }}</span></span>
          <span class="record-status">{{ item.review_summary.pending ? `${item.review_summary.pending} 项待处理` : '查看审阅' }}<small v-if="item.coverage_status !== 'complete'">{{ item.coverage_status === 'partial' ? '部分内容尚未检查' : '覆盖范围未记录' }}</small></span><span class="open-arrow">→</span>
        </button>
      </div>
    </section>
    <section
      class="tools-grid"
      aria-label="更多写作工具"
    >
      <router-link to="/polish">
        <el-icon><MagicStick /></el-icon><div><h4>打磨表达</h4><p>调整语气，让文字更自然</p></div><span>→</span>
      </router-link>
      <router-link
        v-if="user.isLoggedIn && user.hasPermission('fact-check:run')"
        to="/fact-check"
      >
        <el-icon><Search /></el-icon><div><h4>核实事实</h4><p>追溯证据，确认内容依据</p></div><span>→</span>
      </router-link>
      <router-link
        v-if="user.isLoggedIn"
        to="/dictionary"
      >
        <el-icon><Collection /></el-icon><div><h4>我的词库</h4><p>让专有名词得到正确识别</p></div><span>→</span>
      </router-link>
    </section>
    <div class="workflow-note">
      <span>01 输入内容</span><span>→</span><span>02 逐条确认建议</span><span>→</span><span>03 导出修订结果</span>
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { listHistoryApi, type HistoryItem } from '@/api/history'
const user = useUserStore()
const router = useRouter()
const recent = ref<HistoryItem[]>([])
const loading = ref(false)
const error = ref(false)
let alive = true
onBeforeUnmount(() => { alive = false })
async function loadRecent() {
  if (!user.isLoggedIn || loading.value) return
  loading.value = true
  error.value = false
  try {
    const [text, document] = await Promise.all([listHistoryApi({ page_size: 5, type: 'text' }), listHistoryApi({ page_size: 5, type: 'document' })])
    if (alive) recent.value = [...text.items, ...document.items].sort((a, b) => b.id - a.id).slice(0, 5)
  } catch { if (alive) error.value = true }
  finally { if (alive) loading.value = false }
}
function openRecord(item: HistoryItem) { void router.push({ path: item.type === 'document' ? '/proofread/document' : '/proofread/text', query: { review: String(item.id) } }) }
function formatDate(value?: string) { if (!value) return '时间未记录'; const date = new Date(value); return Number.isNaN(date.getTime()) ? '时间未记录' : date.toLocaleDateString('zh-CN') }
onMounted(loadRecent)
</script>
<style scoped>
.workbench { max-width: 1120px; margin: 0 auto; padding: 22px 0; }
.welcome { margin-bottom: 32px; }
.eyebrow { color: var(--color-primary); font-size: 12px; font-weight: 600; letter-spacing: 2px; margin-bottom: 14px; }
h2 { font-size: clamp(25px, 3vw, 36px); font-weight: 650; letter-spacing: -1px; margin-bottom: 14px; }
.welcome p, .section-heading p { color: var(--color-text-secondary); line-height: 1.8; }
.start-grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 20px; margin-bottom: 38px; }
.start-card { display: block; padding: 28px; background: var(--surface); border: 1px solid var(--color-border); border-radius: 16px; color: var(--color-text); text-decoration: none; transition: border-color .2s, transform .2s; }
.start-card:hover { border-color: var(--color-primary); transform: translateY(-2px); }
.primary-card { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); }
.card-icon { width: 42px; height: 42px; border-radius: 12px; background: var(--surface); color: var(--color-primary); display: grid; place-items: center; font-size: 22px; margin-bottom: 24px; }
.start-card h3 { font-size: 21px; display: flex; justify-content: space-between; margin-bottom: 12px; }
.start-card h3 span { color: var(--color-text-secondary); font-weight: 400; }
.start-card p { color: var(--color-text-secondary); font-size: 13px; line-height: 1.8; }
.card-action { display: inline-flex; gap: 36px; margin-top: 26px; font-weight: 600; color: var(--color-primary); }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; }
.section-heading h3 { font-size: 18px; margin-bottom: 6px; }.section-heading p { font-size: 12px; }
.section-heading a { color: var(--color-primary); text-decoration: none; white-space: nowrap; font-size: 13px; }
.recent-list, .empty-state { background: var(--surface); border: 1px solid var(--color-border); border-radius: 12px; overflow: hidden; }
.empty-state { display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 36px 20px; text-align: center; color: var(--color-text-secondary); font-size: 13px; }.empty-state > .el-icon { font-size: 30px; }.empty-state h4 { color: var(--color-text); font-size: 15px; }
.recent-item { display: flex; width: 100%; align-items: center; gap: 16px; text-align: left; border: 0; border-bottom: 1px solid var(--color-border); background: transparent; padding: 20px; cursor: pointer; color: var(--color-text); }.recent-item:last-child { border-bottom: 0; }.recent-item:hover { background: var(--surface-soft); }
.file-icon { padding: 10px; background: var(--surface-soft); color: var(--color-primary); border-radius: 8px; font-size: 20px; }
.record-copy { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 7px; }.record-copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; font-weight: 500; }.record-copy > span { font-size: 12px; color: var(--color-text-secondary); }
.record-status { font-size: 12px; color: var(--color-primary); }.record-status small { display: block; margin-top: 6px; color: var(--color-text-secondary); }.open-arrow { color: var(--color-text-secondary); }
.tools-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(210px,1fr)); gap: 16px; margin-top: 28px; }.tools-grid a { display: flex; align-items: center; gap: 12px; padding: 20px; color: var(--color-text); text-decoration: none; border: 1px solid var(--color-border); border-radius: 12px; }.tools-grid a:hover { background: var(--surface); }.tools-grid .el-icon { font-size: 22px; color: var(--color-text-secondary); }.tools-grid h4 { font-size: 14px; margin-bottom: 6px; }.tools-grid p { font-size: 12px; color: var(--color-text-secondary); }.tools-grid a > span { margin-left: auto; }
.workflow-note { display: flex; justify-content: center; gap: 24px; font-size: 12px; color: var(--color-text-secondary); margin-top: 36px; }
@media(max-width: 650px) { .workbench { padding: 12px 0; }.start-grid { grid-template-columns: 1fr; gap: 12px; }.start-card { padding: 22px; }.card-icon { margin-bottom: 16px; }.welcome { margin-bottom: 24px; }.section-heading p { display: none; }.record-status { max-width: 90px; }.recent-item { padding: 16px 12px; gap: 10px; }.workflow-note { gap: 8px; font-size: 10px; } }
</style>
