# Podcast Grabber

Checks RSS feeds, downloads new episodes, and tags the resulting MP3s.
Each episode gets its own folder:

```
{Podcast Name}/{Episode Title}/01 {Episode Title}.mp3
{Podcast Name}/{Episode Title}/01 poster.jpg
{Podcast Name}/{Episode Title}/01 {Episode Title}.lrc
```

The `.lrc` file is a synced-lyrics-style transcript, converted from
whichever transcript the feed provides (SRT preferred, then WebVTT).
If a feed only offers a format the script doesn't recognize as
timed cues, the raw transcript is saved as `01 {Episode Title}.transcript.txt`
instead, so nothing is lost.

## Setup

```
pip install -r requirements.txt
cp config.example.json config.json
```

Edit `config.json` — one entry per feed:

```json
{
  "name": "Norah Jones Is Playing Along",
  "rss_url": "https://.../rss",
  "output_dir": "D:/Podcasts",
  "genre": "Podcast",
  "check_latest": 5
}
```

- `check_latest` — how many of the newest feed entries to look at each
  run (default 5). Already-downloaded episodes are skipped, so this
  just caps how much a brand-new feed will backfill on its first run.
- `download_transcript` — download and convert the episode transcript
  to LRC, if the feed provides one (default `true`). Set to `false`
  to skip this entirely for a given feed.

### Sort order

`Album Artist Sort Order` and `Artist Sort Order` come from a
hard-coded lookup at the top of `podcast_grabber.py`:

```python
SORT_ORDER_MAP = {
    "Norah Jones Is Playing Along": "Jones, Norah Is Playing Along",
}
```

Add one line per podcast (key must match the `name` in your config
exactly). Anything not listed just falls back to the plain podcast
name and prints a warning.

## Running

```
python podcast_grabber.py --config config.json
```

Useful flags:

- `--dry-run` — show what's new without downloading anything
- `--feed "Podcast Name"` — only check one feed from the config

## Syncing descriptions to Plex

If you're organizing podcasts as a Plex **Music** library (Artist =
Podcast Name, Album = Episode Title), Plex won't pull the ID3
`Comment` into the Album summary on its own. `sync_plex_descriptions.py`
handles that separately:

- Walks every album in the configured library.
- Skips anything that already has a summary in Plex.
- For the rest, reads the episode description out of the MP3's ID3
  comment and pushes it up as the album summary (locked by default,
  so a future Plex metadata refresh won't clobber it).
- Anything with no ID3 comment to pull from is skipped with a logged
  warning rather than guessed at.

```
pip install plexapi
cp plex_config.example.json plex_config.json
```

Fill in `plex_config.json`:

```json
{
  "plex_url": "https://plex.snydern.com",
  "plex_token": "your-token-here",
  "library_name": "Audiobooks & Radio",
  "lock_summary": true
}
```

`path_map` is only needed if this script runs somewhere that sees the
media at a different path than the Plex server does (leave both
prefixes blank to skip it).

Run it:

```
python sync_plex_descriptions.py --config plex_config.json --dry-run
python sync_plex_descriptions.py --config plex_config.json
```


Runs safely on a schedule — a state file tracks what's already been
downloaded, so re-running only picks up genuinely new episodes.

```
*/30 * * * * cd /path/to/podcast-grabber && /usr/bin/python3 podcast_grabber.py --config config.json >> podcast_grabber.log 2>&1
```
