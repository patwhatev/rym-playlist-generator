# rymlist

Turn rateyourmusic lists into Spotify or YouTube playlists, with a report of what was found, what needs a human look, and what couldn't be found. Matches are validated, not just "same name": see [How matches are validated](#how-matches-are-validated) and [YouTube](#youtube).

Every report leads with **coverage**: how many of the list's releases each service actually got into a playlist, and how many it left out. Run a list on both services and it also shows how many releases are only on Spotify, only on YouTube, or on neither, so you can tell when Spotify is underperforming. It varies by list: on *Now is Never On Thyme*, Spotify got 19 of 120 releases and YouTube 43, while on *Mirage* Spotify did better (32 against 26 of 75). The two mostly find different releases, so running both can cover a lot more.

The workflow:

1. **Capture** lists as you browse, with the extension (RYM sits behind Cloudflare, so scripts can't fetch it).
2. **`rymlist run`** (Spotify) or **`rymlist run --to youtube`** whenever you like: ingests new captures, then matches and builds playlists for every pending list and moves each finished list to `done/`.

## Setup

```sh
brew install uv          # once
uv sync                  # creates .venv with python 3.12 + deps
```

Spotify app: create one at https://developer.spotify.com/dashboard with redirect URI `http://127.0.0.1:8888/callback` (Web API), then export its credentials in your shell profile:

```sh
export SPOTIFY_CLIENT=...
export SPOTIFY_SECRET=...
```

Load the extension once: `chrome://extensions` → Developer mode → **Load unpacked** → pick `extension/`.

For YouTube playlists, see [YouTube setup](#youtube-setup-once): a Google OAuth client in `secrets/`. Discogs is optional; see [Discogs](#discogs).

## Use

```sh
# on any rateyourmusic.com/list/... page: click the extension → "Capture this list"
# → ~/Downloads/rymlist/<user>__<slug>.json once every page is in

uv run rymlist run      # ingest + match + playlist + move to done/, for everything pending
uv run rymlist run --to youtube   # same, onto YouTube
uv run rymlist lists    # every list with its state, coverage per service and playlist links
```

Capturing is resumable. Pages are fetched 3-5s apart and each one is kept in the extension's storage as soon as it arrives; a 503/429/Cloudflare check is retried after 20s, 60s and 2min. If it still stops, the popup says how many pages are saved and **Resume** continues from the first missing page. The popup can be closed while it works. **Start over** discards saved pages for that list. After changing the extension, hit reload on it in `chrome://extensions`.

### Manual record: only the pages you pick

For lists where you only want some pages: click **● Start manual record**, then browse the list yourself. Every list page you open is saved straight from the page your browser already loaded, so the extension makes no requests of its own. A red tag in the corner of the page confirms each save. Pages with a Cloudflare check or no releases aren't saved; reload once the check passes. Opening a page again replaces the saved copy. The popup lists the recorded pages, and **×** drops one. **Stop record and export JSON** writes one `~/Downloads/rymlist/<list>.json` per list you recorded from. The recording survives closing the popup or the browser until you stop or discard it.

A manual capture keeps RYM's real page numbers, so `sections.toml` still applies. It also replaces any earlier capture of the same list: the list becomes exactly the pages you exported, and playlists for sections that are no longer in it aren't touched on Spotify.

The first `run` opens a Spotify login in your browser once. Failures are contained:
- **One release:** if a lookup fails, that release is marked `error`, left out of the playlist and listed in the report and leftovers, and the rest of the list carries on. Search queries over Spotify's 250-character limit (long multi-artist credits) are shortened before they're sent.
- **Five in a row:** that means a service or the network is down, so the list stops.
- **A whole list:** a list that stops or fails stays in `pending/` with the error recorded, and the next `run` retries it. The rest of the batch carries on.

`rymlist reset <list>` followed by `run` retries a list's `error` releases. Lookups are cached, so only the failed ones are redone.

Playlists are named `RYM <list title>`. Recapturing a list that's already done moves it back to pending, and the next `run` updates **the same** playlist rather than making a new one. `rymlist reset <list>` does that without recapturing (after editing `overrides.toml`, say).

### One playlist per genre / page

A list sorted into sections by page can become one playlist per section. Add it to `sections.toml`, keyed by the list id or any unique part of it (the slug works):

```toml
["some-list-slug"]
1 = "Finnish Death Metal"
2 = "Raw Black Metal"
36 = "Spookies: Body Horror"
```

Playlists are named `RYM <list title> — <section>`. Pages with the same name are merged into one playlist; pages not listed go into an "Other" playlist. `leftovers.md` is grouped by section too. Sections are applied when playlists are built, so after editing them run `rymlist reset <list>` then `rymlist run`, which reuses cached matches.

Spotify currently ignores the API's privacy flag, so new playlists show up as public on your profile. Use "Remove from profile" / "Make private" in the Spotify app if you want one hidden. Lists over 10,000 tracks are split into numbered playlists.

`run` options: `--to spotify|youtube`, `--include-uncertain` (also add "needs review" matches), `--no-mb` (fast, skips MusicBrainz so less can be "verified"), `--no-discogs`, `--refresh` (ignore cached responses), and for YouTube `--unlisted` / `--public`.

Single steps are there too: `ingest`, `match <list> [--to youtube] [--limit N]`, `playlist <list> [--to youtube]`, `reset <list>`. `<list>` is a list id, any unique part of one, or `all`. Full-width ids match plain typing too: `broken` finds `ｂｒｏｋｅｎ-ｓｉｍｕｌａｔｉｏｎ`.

First matches are slow because MusicBrainz allows ~1 request/second (20-30 min for 700 releases). Every response is cached in `.cache/`, so reruns take seconds.

## YouTube

```sh
uv run rymlist run --to youtube        # every pending list, matched on public YouTube
uv run rymlist reset <list>            # to do a list that's already done on Spotify, then:
uv run rymlist run --to youtube
```

Each list remembers the service it was last run with, so a plain `rymlist run` continues where it left off. Lists can have both: playlists are tracked per service, and the report compares them. A quick way to decide is to run a list on YouTube after Spotify and look at the coverage table at the top of its `report.md`.

**How it searches:** public YouTube search results through yt-dlp. It reads metadata only (titles, lengths, channels), downloads nothing and uses no API quota. Searches are paced about 1.5 seconds apart and cached. It looks for a full-album upload: the artist and the title must both be in the video title (or the artist is the channel), with no review, reaction, cover, edit, remix or similar words, and no "live" unless RYM lists it as live. Then the length has to be right:

- **With a known runtime:** compared against it, allowing 5% for verified, 10% for likely and 20% for needs review, with tighter limits on short releases. The runtime comes from the Spotify match if there is one, then MusicBrainz track lengths, then Discogs. Discogs is only asked when its answer can change the outcome: uploads matched but none passed, the best is only "needs review", or the uploads disagree on length.
- **Without one:** a full album has to be at least 15 minutes and labelled "full" (or on the artist's channel) to count as likely. Five to 15 minutes is needs review (fine for punk or grind, but check it), and anything under 5 minutes is rejected. EPs use 8 and 3 minutes. Anything over 3 hours is needs review, since it may be a discography upload.

**How it writes playlists:** through the official YouTube Data API. Playlists are **private** unless you pass `--unlisted` (anyone with the link) or `--public` (shown on your channel and in search). YouTube respects this, unlike Spotify. Public is fairly low-risk: a playlist only points at other people's uploads, so takedowns go to the uploader and the video just disappears from your playlist. It is public under your account's name, though, so think twice for lists with sections you wouldn't want on your channel. You can also flip privacy later in YouTube itself; rymlist only changes it when you pass a different flag.
- The daily quota is 10,000 units, and adding one video costs 50, so about 195 albums a day.
- When the quota runs out, the list stays in `pending/` marked paused, and the next `run` carries on. What's already in the playlist is read back from YouTube, so nothing is added twice.
- The same goes for any other error part-way through: the playlist's id is saved first, so a rerun fills the same playlist instead of creating a duplicate. YouTube's occasional "409 operation aborted" on back-to-back adds is retried automatically.
- Deleted or private videos are dropped before adding, and listed under `unavailable` in `list.json`.
- Lists over 5,000 videos are split into numbered playlists.

### YouTube setup (once)

Writing playlists needs an **OAuth client**, not an API key: an API key can only read public data.

1. Go to https://console.cloud.google.com, create a project (any name), then **APIs & Services → Library**, search for **YouTube Data API v3** and enable it.
2. **APIs & Services → OAuth consent screen:** choose External, fill in an app name and your email, and add yourself under **Test users**. No scopes need adding here.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**, application type **Desktop app**. Download the JSON and save it as `secrets/youtube-client.json` in this repo. `secrets/` is gitignored.
4. The first `run --to youtube` that builds a playlist opens a Google login in your browser. The token is saved in `secrets/youtube-token.json`.

While the consent screen is in "Testing", Google expires the login every 7 days, and you'll be asked to sign in again. Clicking **Publish app** on the consent screen stops that. For a personal app you don't need Google's verification, but you'll click through an "unverified app" warning when you sign in.

### Discogs

Optional, used sparingly as described above. Create an app at https://www.discogs.com/settings/developers and export its consumer key and secret. No callback URL is needed:

```sh
export DISCOGS_CONSUMER=...
export DISCOGS_SECRET=...
```

Discogs also firms up an "uncertain" Spotify match: if a matching Discogs pressing has the Spotify album's barcode, it becomes verified. `--no-discogs` turns Discogs off.

## Where things go

```
captures/<list>.json.gz      raw html of every page, as captured (kept forever)
pending/<list>/              lists still to do
done/<list>/                 lists whose playlist was made, skipped by `run`
    list.json                parsed releases + "status" (see below)
    results.json             Spotify match per release: status, album, evidence, rejected candidates
    results.youtube.json     YouTube match per release: video, real runtime + source, rejected uploads
    report.md                coverage per service (included / not included), then matched / needs review / not found
    leftovers.md             only what needs a manual search, per service, with Bandcamp/YouTube/Discogs search links
    leftovers.json           same, machine readable
INDEX.md                     every list: state, counts, playlist link, leftovers link
```

`list.json` → `status` records what happened:

```json
"status": {
  "state": "done",
  "target": "spotify",
  "captured_at": "...", "completed_at": "...",
  "services": {
    "spotify": {"matched_at": "...", "counts": {"likely": 35, "not_found": 640, "uncertain": 26},
                "included": 35, "not_included": 666,
                "leftovers": {"count": 666, "not_found": 640, "needs_review": 26, "errors": 0}},
    "youtube": {...}
  },
  "playlists": [
    {"service": "spotify", "section": "Raw Black Metal", "pages": [2], "name": "RYM ... — Raw Black Metal",
     "url": "https://open.spotify.com/playlist/...", "tracks": 212, "releases": 14,
     "created_at": "...", "updated_at": "...", "parts": [...]}
  ],
  "leftovers": {"file": "done/<list>/leftovers.md"},
  "error": null
}
```

One entry per service and section (`section` is `null` without sections). An entry has `url: null` and a `note` when nothing in that section was found. YouTube entries also carry `privacy`, `unavailable` (dropped dead videos) and `incomplete: true` while a quota pause is pending. Older `list.json` files are migrated to this shape when they're read. If the parser ever needs fixing, `rymlist ingest --reparse` rebuilds every `list.json` from the stored captures.

## How matches are validated

For each release, Spotify is searched several ways (title + artist, romanized variants, the artist's albums, free text). A candidate is only considered if **both** the artist and the title match closely after normalization (case, accents, `&`/`and`, "Remastered"/"Deluxe" noise). Then:

| status | in playlist | means |
| --- | --- | --- |
| **verified** | yes | MusicBrainz independently confirms it: it links this exact Spotify album, the barcode matches the Spotify UPC, or MusicBrainz links this Spotify artist and the year or track count agrees |
| **likely** | yes | artist, title and year all match, and no other Spotify artist with that name came up |
| **needs review** | with `--include-uncertain` | names match but something's off: Spotify date is later (reissue), RYM has no year, several Spotify artists share the name, or the spelling is a near miss |
| **not found** | no | nothing passed. The report says whether the artist is on Spotify at all and lists any rejected candidates |

Candidates are **rejected** outright when MusicBrainz links the artist to a *different* Spotify profile (a same-name artist), or when the Spotify release predates the original.

## Overrides

Fix individual matches in `overrides.toml`, keyed by RYM release URL, then rerun `match`. `skip` applies to both services:

```toml
["https://rateyourmusic.com/release/album/artist/title/"]
spotify = "https://open.spotify.com/album/..."   # pin this album
youtube = "https://www.youtube.com/watch?v=..."  # pin this upload

["https://rateyourmusic.com/release/album/artist/other/"]
skip = true                                      # leave it out
```

## Tests

```sh
uv run pytest
```
