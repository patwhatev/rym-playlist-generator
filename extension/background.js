// Captures every page of a RYM list. Each page is written to chrome.storage.local as soon
// as it arrives, so a failure (503, closed browser, ...) can resume from the first missing
// page instead of starting over. The finished capture is saved to
// ~/Downloads/rymlist/<user>__<slug>.json and its stored pages are cleared.

const PAGE_DELAY_MS = [3000, 5000]          // random pause between page requests
const RETRY_WAITS_MS = [20000, 60000, 120000] // after a 429/503/challenge
const running = new Set()

const pagesKey = id => `pages:${id}`
const jobKey = id => `job:${id}`

async function setJob(id, job) {
  await chrome.storage.local.set({ [jobKey(id)]: { ...job, updated_at: Date.now() } })
}

// keep the service worker alive through long waits: extension api calls reset its idle timer
async function sleep(ms) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    await new Promise(r => setTimeout(r, Math.min(10000, end - Date.now())))
    await chrome.runtime.getPlatformInfo()
  }
}

// --- these two run inside the RYM tab, so requests carry the user's session + cloudflare clearance
function inspectList() {
  const match = location.pathname.match(/^\/list\/([^/]+)\/([^/]+)\//)
  if (!match) return { error: 'Open a rateyourmusic.com/list/... page first.' }
  const base = `/list/${match[1]}/${match[2]}/`
  const user = decodeURIComponent(match[1])
  const slug = decodeURIComponent(match[2])
  let lastPage = 1
  for (const a of document.querySelectorAll(`a[href^="${base}"]`)) {
    const m = a.getAttribute('href').slice(base.length).match(/^(\d+)\/?$/)
    if (m) lastPage = Math.max(lastPage, Number(m[1]))
  }
  const current = location.pathname.slice(base.length).match(/^(\d+)/)
  return {
    user, slug, lastPage,
    base: new URL(base, location.origin).href,
    title: document.title,
    currentPage: current ? Number(current[1]) : 1,
    html: document.documentElement.outerHTML,
  }
}

async function fetchPage(url) {
  try {
    const res = await fetch(url, { credentials: 'include' })
    const html = await res.text()
    return { status: res.status, html }
  } catch (err) {
    return { status: 0, error: String(err) }
  }
}
// ---

function isChallenge(html) {
  return html.slice(0, 5000).includes('Just a moment')
}

async function inTab(tabId, func, args = []) {
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func, args })
  return result
}

function toBase64(text) {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  }
  return btoa(binary)
}

async function capture(tabId) {
  const info = await inTab(tabId, inspectList)
  if (info.error) return { error: info.error }
  const id = `${info.user}__${info.slug}`
  if (running.has(id)) return { id, already: true }
  running.add(id)

  try {
    const stored = (await chrome.storage.local.get(pagesKey(id)))[pagesKey(id)] || {}
    const save = async () => chrome.storage.local.set({ [pagesKey(id)]: stored })
    const progress = (state, extra = {}) => setJob(id, {
      state, id, total: info.lastPage, have: Object.keys(stored).length, ...extra,
    })

    const pageUrl = n => (n === 1 ? info.base : `${info.base}${n}/`)
    if (!stored[info.currentPage] && !isChallenge(info.html)) {
      stored[info.currentPage] = { url: pageUrl(info.currentPage), html: info.html, at: Date.now() }
      await save()
    }

    for (let n = 1; n <= info.lastPage; n++) {
      if (stored[n]) continue
      await progress('running', { page: n })
      await sleep(PAGE_DELAY_MS[0] + Math.random() * (PAGE_DELAY_MS[1] - PAGE_DELAY_MS[0]))

      let page = null
      for (let attempt = 0; attempt <= RETRY_WAITS_MS.length; attempt++) {
        const res = await inTab(tabId, fetchPage, [pageUrl(n)])
        if (res.status === 200 && !isChallenge(res.html)) {
          page = res
          break
        }
        const why = res.status === 200 ? 'cloudflare challenge' : `HTTP ${res.status || res.error}`
        if (attempt === RETRY_WAITS_MS.length) {
          await progress('paused', { page: n, error: `page ${n}: ${why} after ${attempt + 1} tries` })
          return { id, paused: true }
        }
        await progress('waiting', { page: n, error: `page ${n}: ${why}, retrying in ${RETRY_WAITS_MS[attempt] / 1000}s` })
        await sleep(RETRY_WAITS_MS[attempt])
      }
      stored[n] = { url: pageUrl(n), html: page.html, at: Date.now() }
      await save()
    }

    await progress('saving')
    const pages = Object.entries(stored)
      .map(([n, p]) => ({ page: Number(n), url: p.url, html: p.html, fetched_at: new Date(p.at).toISOString() }))
      .filter(p => p.page <= info.lastPage)
      .sort((a, b) => a.page - b.page)
    const doc = {
      source: 'rymlist-extension', version: 2,
      user: info.user, slug: info.slug, url: info.base,
      title: info.title, captured_at: new Date().toISOString(), pages,
    }
    const filename = `rymlist/${id}.json`
    await chrome.downloads.download({
      url: 'data:application/json;base64,' + toBase64(JSON.stringify(doc)),
      filename, conflictAction: 'overwrite', saveAs: false,
    })
    await chrome.storage.local.remove(pagesKey(id))
    await progress('done', { have: pages.length, file: `~/Downloads/${filename}` })
    return { id, done: true }
  } catch (err) {
    await setJob(id, { state: 'paused', id, error: String(err.message || err) })
    return { id, error: String(err.message || err) }
  } finally {
    running.delete(id)
  }
}

async function reset(tabId) {
  const info = await inTab(tabId, inspectList)
  if (info.error) return { error: info.error }
  const id = `${info.user}__${info.slug}`
  await chrome.storage.local.remove([pagesKey(id), jobKey(id)])
  return { id }
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  const run = msg.type === 'capture' ? capture(msg.tabId) : msg.type === 'reset' ? reset(msg.tabId) : null
  if (!run) return false
  run.then(reply, err => reply({ error: String(err) }))
  return true
})

// --- manual recording: content.js saves each list page you visit while it's on; this packs them up

async function recordedPages() {
  const all = await chrome.storage.local.get(null)
  const lists = {}
  for (const [key, page] of Object.entries(all)) {
    if (!key.startsWith('rec:') || key === 'rec') continue
    ;(lists[page.id] ||= []).push({ key, ...page })
  }
  return lists
}

async function setBadge() {
  const { rec } = await chrome.storage.local.get('rec')
  const lists = await recordedPages()
  const count = Object.values(lists).reduce((n, pages) => n + pages.length, 0)
  await chrome.action.setBadgeBackgroundColor({ color: '#b00020' })
  await chrome.action.setBadgeText({ text: rec && rec.active ? String(count || 'REC') : '' })
}

async function startRecording(tabId) {
  await chrome.storage.local.set({ rec: { active: true, started_at: Date.now() } })
  if (tabId) {
    // tabs opened before the extension was (re)loaded don't have the content script yet
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] }).catch(() => {})
  }
  return { recording: true }
}

async function stopRecording(discard) {
  const lists = await recordedPages()
  const files = []
  if (!discard) {
    for (const [id, recorded] of Object.entries(lists)) {
      const pages = recorded
        .sort((a, b) => a.page - b.page)
        .map(p => ({ page: p.page, url: p.url, html: p.html, fetched_at: new Date(p.at).toISOString() }))
      const first = recorded[0]
      const doc = {
        source: 'rymlist-extension', version: 2, mode: 'manual',
        user: first.user, slug: first.slug, url: first.base,
        title: first.title, captured_at: new Date().toISOString(), pages,
      }
      const filename = `rymlist/${id}.json`
      await chrome.downloads.download({
        url: 'data:application/json;base64,' + toBase64(JSON.stringify(doc)),
        filename, conflictAction: 'overwrite', saveAs: false,
      })
      files.push({ file: `~/Downloads/${filename}`, pages: pages.map(p => p.page) })
    }
  }
  const keys = Object.values(lists).flat().map(p => p.key)
  await chrome.storage.local.remove([...keys, 'rec'])
  return { files }
}

async function dropRecordedPage(key) {
  await chrome.storage.local.remove(key)
  return {}
}

chrome.storage.onChanged.addListener(changes => {
  if (Object.keys(changes).some(k => k.startsWith('rec'))) setBadge()
})
chrome.runtime.onStartup.addListener(setBadge)
chrome.runtime.onInstalled.addListener(setBadge)

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const run = {
    'rec-start': () => startRecording(msg.tabId),
    'rec-stop': () => stopRecording(false),
    'rec-discard': () => stopRecording(true),
    'rec-drop': () => dropRecordedPage(msg.key),
  }[msg.type]
  if (!run) return false
  run().then(reply, err => reply({ error: String(err.message || err) }))
  return true
})
