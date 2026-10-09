// Manual recording: while a recording is on, every rateyourmusic list page you open is saved
// as-is from the page your browser already loaded. No extra requests are made.
// Each page goes in its own storage key (rec:<list id>:<page>) so several tabs can't clobber each other.

// guarded: the popup may inject this into a tab that already has it
if (!window.__rymlistRecorder) {
  window.__rymlistRecorder = true

  const LIST_PATH = /^\/list\/([^/]+)\/([^/]+)\/(?:(\d+)\/?)?$/

  function pageInfo() {
    const m = location.pathname.match(LIST_PATH)
    if (!m) return null
    const user = decodeURIComponent(m[1])
    const slug = decodeURIComponent(m[2])
    return {
      id: `${user}__${slug}`,
      user, slug,
      page: m[3] ? Number(m[3]) : 1,
      base: new URL(`/list/${m[1]}/${m[2]}/`, location.origin).href,
    }
  }

  function hasReleases() {
    return !document.title.includes('Just a moment') && Boolean(document.querySelector('a.list_album'))
  }

  let badge = null
  function showBadge(text) {
    if (!badge) {
      badge = document.createElement('div')
      badge.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;padding:6px 10px;' +
        'border-radius:6px;background:#b00020;color:#fff;font:12px/1.3 -apple-system,system-ui,sans-serif;' +
        'box-shadow:0 2px 8px rgba(0,0,0,.3);pointer-events:none'
      document.documentElement.appendChild(badge)
    }
    badge.textContent = text
    badge.hidden = !text
  }

  async function record() {
    const info = pageInfo()
    if (!info) return
    const { rec } = await chrome.storage.local.get('rec')
    if (!rec || !rec.active) return showBadge('')
    if (!hasReleases()) return showBadge('● recording: nothing saved here (no releases, or a Cloudflare check: reload once it passes)')

    if (badge) badge.remove()  // keep the badge out of the saved html
    badge = null
    await chrome.storage.local.set({
      [`rec:${info.id}:${info.page}`]: {
        ...info,
        url: location.href,
        title: document.title,
        html: document.documentElement.outerHTML,
        at: Date.now(),
      },
    })
    const keys = chrome.storage.local.getKeys
      ? await chrome.storage.local.getKeys()
      : Object.keys(await chrome.storage.local.get(null))
    const mine = keys.filter(k => k.startsWith(`rec:${info.id}:`)).length
    showBadge(`● recording: page ${info.page} saved (${mine} from this list)`)
  }

  // record on load, and on the current page the moment a recording is started
  record()
  chrome.storage.onChanged.addListener(changes => {
    if (!changes.rec) return
    const now = changes.rec.newValue
    if (now && now.active && !(changes.rec.oldValue && changes.rec.oldValue.active)) record()
    else if (!now || !now.active) showBadge('')
  })
}
