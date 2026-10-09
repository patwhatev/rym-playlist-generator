// Starts / resumes a capture in the background worker and shows its progress.
// The popup can be closed at any time; the capture keeps going.

const statusEl = document.getElementById('status')
const captureBtn = document.getElementById('capture')
const resetBtn = document.getElementById('reset')
const bar = document.getElementById('bar')

let tab = null
let listId = null

function listIdFromUrl(url) {
  const m = url && new URL(url).pathname.match(/^\/list\/([^/]+)\/([^/]+)\//)
  return m ? `${decodeURIComponent(m[1])}__${decodeURIComponent(m[2])}` : null
}

function show(job, savedPages) {
  const have = job ? job.have : savedPages
  const total = job && job.total
  bar.hidden = !total
  if (total) { bar.max = total; bar.value = have }
  resetBtn.hidden = !(savedPages || (job && job.state !== 'done'))
  statusEl.classList.toggle('error', Boolean(job && job.state === 'paused'))

  if (!job) {
    captureBtn.textContent = savedPages ? `Resume (${savedPages} pages saved)` : 'Capture this list'
    statusEl.textContent = ''
    return
  }
  const busy = ['running', 'waiting', 'saving'].includes(job.state)
  captureBtn.disabled = busy
  captureBtn.textContent = busy ? 'Capturing…' : job.state === 'paused' ? `Resume (${have}/${total} saved)` : 'Capture again'
  statusEl.textContent = {
    running: `Page ${job.page} of ${total}… (${have} saved). You can close this popup.`,
    waiting: `${job.error}\n${have}/${total} pages saved.`,
    saving: 'Writing the file…',
    paused: `Stopped: ${job.error}\n${have}/${total} pages are saved. Click Resume to continue from page ${job.page || have + 1}.`,
    done: `Saved ${have} pages to\n${job.file}\n\nRun: uv run rymlist run`,
  }[job.state] || ''
}

async function refresh() {
  if (!listId) return
  const data = await chrome.storage.local.get([`job:${listId}`, `pages:${listId}`])
  show(data[`job:${listId}`], Object.keys(data[`pages:${listId}`] || {}).length)
}

chrome.storage.onChanged.addListener(changes => {
  if (changes[`job:${listId}`] || changes[`pages:${listId}`]) refresh()
  if (Object.keys(changes).some(k => k.startsWith('rec'))) refreshRecording()
})

captureBtn.addEventListener('click', async () => {
  captureBtn.disabled = true
  const res = await chrome.runtime.sendMessage({ type: 'capture', tabId: tab.id })
  if (res && res.error && !listId) {
    statusEl.textContent = res.error
    statusEl.classList.add('error')
    captureBtn.disabled = false
  }
  refresh()
})

resetBtn.addEventListener('click', async () => {
  await chrome.runtime.sendMessage({ type: 'reset', tabId: tab.id })
  captureBtn.disabled = false
  refresh()
})

;(async () => {
  ;[tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  listId = listIdFromUrl(tab && tab.url)
  refreshRecording()
  if (!listId) {
    captureBtn.disabled = true
    statusEl.textContent = 'Open a rateyourmusic.com/list/... page first.'
    return
  }
  refresh()
})()

// ---------------------------------------------------------------- manual record

const recStart = document.getElementById('rec-start')
const recStop = document.getElementById('rec-stop')
const recDiscard = document.getElementById('rec-discard')
const recLists = document.getElementById('rec-lists')
const recStatus = document.getElementById('rec-status')
let lastExport = null

async function refreshRecording() {
  const all = await chrome.storage.local.get(null)
  const active = Boolean(all.rec && all.rec.active)
  const lists = {}
  for (const [key, p] of Object.entries(all)) {
    if (key.startsWith('rec:')) (lists[p.id] ||= []).push({ key, page: p.page })
  }
  const count = Object.values(lists).reduce((n, pages) => n + pages.length, 0)

  recStart.hidden = active
  recStop.hidden = !active
  recDiscard.hidden = !active && !count
  recStop.disabled = !count
  recStop.textContent = count ? `Stop record and export JSON (${count} pages)` : 'Stop record and export JSON'

  recLists.replaceChildren(...Object.entries(lists).sort().map(([id, pages]) => {
    const div = document.createElement('div')
    div.className = 'list'
    const name = document.createElement('b')
    name.textContent = id
    div.append(name)
    for (const p of pages.sort((a, b) => a.page - b.page)) {
      const chip = document.createElement('span')
      chip.className = 'page'
      chip.textContent = `p${p.page}`
      const x = document.createElement('button')
      x.textContent = '×'
      x.title = `Drop page ${p.page}`
      x.addEventListener('click', () => chrome.runtime.sendMessage({ type: 'rec-drop', key: p.key }))
      chip.append(x)
      div.append(chip)
    }
    return div
  }))

  if (active) {
    recStatus.textContent = count
      ? 'Keep browsing: every list page you open is saved (reopen a page to update it). Close this popup any time.'
      : 'Recording. Open the list pages you want; each one is saved as it loads.'
  } else if (lastExport) {
    recStatus.textContent = lastExport
  } else {
    recStatus.textContent = 'Saves only the list pages you open yourself, then exports them as one file per list.'
  }
}

recStart.addEventListener('click', async () => {
  lastExport = null
  await chrome.runtime.sendMessage({ type: 'rec-start', tabId: tab && tab.id })
  refreshRecording()
})

recStop.addEventListener('click', async () => {
  recStop.disabled = true
  const res = await chrome.runtime.sendMessage({ type: 'rec-stop' })
  recStatus.classList.toggle('error', Boolean(res.error))
  lastExport = res.error || 'Saved:\n' + res.files.map(f => `${f.file} (pages ${f.pages.join(', ')})`).join('\n') +
    '\n\nRun: uv run rymlist run'
  refreshRecording()
})

recDiscard.addEventListener('click', async () => {
  if (recDiscard.dataset.armed !== '1') {
    recDiscard.dataset.armed = '1'
    recDiscard.textContent = 'Click again to discard every recorded page'
    return
  }
  recDiscard.dataset.armed = ''
  recDiscard.textContent = 'Discard recording'
  lastExport = null
  await chrome.runtime.sendMessage({ type: 'rec-discard' })
  refreshRecording()
})
