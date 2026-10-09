from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = ROOT / 'captures'      # raw html from the extension, gzipped
PENDING = ROOT / 'pending'        # one folder per list still to be matched / playlisted
DONE = ROOT / 'done'              # lists whose playlist was created, moved here so they aren't redone
INDEX = ROOT / 'INDEX.md'         # every list with its playlist + leftovers links
CACHE = ROOT / '.cache'           # http cache + spotify token, not committed
OVERRIDES = ROOT / 'overrides.toml'
SECTIONS = ROOT / 'sections.toml'    # optional: split a list into one playlist per page range / genre
INBOX = Path.home() / 'Downloads' / 'rymlist'
SECRETS = ROOT / 'secrets'        # youtube oauth client + token, not committed
