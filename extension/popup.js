// Captures raw HTML for every page of the RYM list in the active tab and saves it
// to ~/Downloads/rymlist/<user>__<slug>.json. Parsing happens in the python cli.

const statusEl = document.getElementById('status')
const button = document.getElementById('capture')

function setStatus(text, isError = false) {
  statusEl.textContent = text
  statusEl.classList.toggle('error', isError)
}

// runs inside the RYM tab, so fetches carry the user's session + cloudflare clearance
async function captureList() {
  const match = location.pathname.match(/^\/list\/([^/]+)\/([^/]+)\//)
  if (!match) return { error: 'Open a rateyourmusic.com/list/... page first.' }
  const [, user, slug] = match
  const base = `/list/${user}/${slug}/`

  // page links look like /list/user/slug/2/
  let lastPage = 1
  for (const a of document.querySelectorAll(`a[href^="${base}"]`)) {
    const m = a.getAttribute('href').slice(base.length).match(/^(\d+)\/?$/)
    if (m) lastPage = Math.max(lastPage, Number(m[1]))
  }
  const currentMatch = location.pathname.slice(base.length).match(/^(\d+)/)
  const currentPage = currentMatch ? Number(currentMatch[1]) : 1

  const pages = []
  for (let n = 1; n <= lastPage; n++) {
    const url = new URL(n === 1 ? base : `${base}${n}/`, location.origin).href
    if (n === currentPage) {
      pages.push({ page: n, url, html: document.documentElement.outerHTML })
      continue
    }
    await new Promise(r => setTimeout(r, 1500)) // be gentle with rym
    const res = await fetch(url, { credentials: 'include' })
    if (!res.ok) return { error: `Page ${n} returned HTTP ${res.status}. Try again in a minute.` }
    pages.push({ page: n, url, html: await res.text() })
  }

  return {
    source: 'rymlist-extension',
    version: 1,
    user,
    slug,
    url: new URL(base, location.origin).href,
    title: document.title,
    captured_at: new Date().toISOString(),
    pages,
  }
}

button.addEventListener('click', async () => {
  button.disabled = true
  setStatus('Capturing… (about 1.5s per extra page)')
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: captureList })
    if (!result || result.error) throw new Error(result ? result.error : 'Nothing captured.')

    const blob = new Blob([JSON.stringify(result)], { type: 'application/json' })
    const filename = `rymlist/${result.user}__${result.slug}.json`
    await chrome.downloads.download({
      url: URL.createObjectURL(blob),
      filename,
      conflictAction: 'overwrite',
      saveAs: false,
    })
    setStatus(`Saved ${result.pages.length} page(s) to\n~/Downloads/${filename}\n\nRun: uv run rymlist ingest`)
  } catch (err) {
    setStatus(err.message || String(err), true)
  } finally {
    button.disabled = false
  }
})
