# rymlist

Turn rateyourmusic lists into Spotify playlists, with a report of what was found, what needs a human look, and what isn't on Spotify. Matches are validated, not just "same name": see [How matches are validated](#how-matches-are-validated).

The workflow:

1. **Capture** lists as you browse, with the extension (RYM sits behind Cloudflare, so scripts can't fetch it).
2. **`rymlist run`** whenever you like: ingests new captures, then matches and builds a playlist for every pending list and moves each finished list to `done/`.

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

## Use

```sh
# on any rateyourmusic.com/list/... page: click the extension → "Capture this list"
# (fetches every page, ~1.5s apart) → ~/Downloads/rymlist/<user>__<slug>.json

uv run rymlist run      # ingest + match + playlist + move to done/, for everything pending
uv run rymlist lists    # every list with its state, counts and playlist link
```

The first `run` opens a Spotify login in your browser once. A list that fails (network, Spotify error) stays in `pending/` with the error recorded and is retried on the next `run`; the rest of the batch carries on.

Playlists are named `RYM <list title>`. Recapturing a list that's already done moves it back to pending, and the next `run` updates **the same** playlist rather than making a new one. `rymlist reset <list>` does that without recapturing (after editing `overrides.toml`, say).

Spotify currently ignores the API's privacy flag, so new playlists show up as public on your profile. Use "Remove from profile" / "Make private" in the Spotify app if you want one hidden. Lists over 10,000 tracks are split into numbered playlists.

`run` options: `--include-uncertain` (also add "needs review" matches), `--no-mb` (fast, skips MusicBrainz so nothing can be "verified"), `--refresh` (ignore cached responses).

Single steps are there too: `ingest`, `match <list> [--limit N]`, `playlist <list> [--name ...]`, `reset <list>`. `<list>` is a list id, any unique part of one, or `all`.

First matches are slow because MusicBrainz allows ~1 request/second (20-30 min for 700 releases). Every response is cached in `.cache/`, so reruns take seconds.

## Where things go

```
captures/<list>.json.gz      raw html of every page, as captured (kept forever)
pending/<list>/              lists still to do
done/<list>/                 lists whose playlist was made, skipped by `run`
    list.json                parsed releases + "status" (see below)
    results.json             match per release: status, Spotify album, evidence, rejected candidates
    report.md                full report: matched / needs review / not found
    leftovers.md             only what needs a manual search, with Bandcamp/YouTube/Discogs search links
    leftovers.json           same, machine readable
INDEX.md                     every list: state, counts, playlist link, leftovers link
```

`list.json` → `status` records what happened:

```json
"status": {
  "state": "done",
  "captured_at": "...", "matched_at": "...", "completed_at": "...",
  "counts": {"likely": 35, "not_found": 640, "uncertain": 26},
  "playlist": {"created_at": "...", "updated_at": "...", "url": "https://open.spotify.com/playlist/...",
               "name": "RYM ...", "tracks": 467, "releases": 35, "parts": [...]},
  "leftovers": {"count": 666, "not_found": 640, "needs_review": 26, "file": "done/<list>/leftovers.md"},
  "error": null
}
```

`playlist` is `null` (with a `note`) when nothing on a list is on Spotify. If the parser ever needs fixing, `rymlist ingest --reparse` rebuilds every `list.json` from the stored captures.

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

Fix individual matches in `overrides.toml`, keyed by RYM release URL, then rerun `match`:

```toml
["https://rateyourmusic.com/release/album/artist/title/"]
spotify = "https://open.spotify.com/album/..."   # pin this album

["https://rateyourmusic.com/release/album/artist/other/"]
skip = true                                      # leave it out
```

## Tests

```sh
uv run pytest
```
