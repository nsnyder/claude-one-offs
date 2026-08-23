# Podcast Grabber

Checks RSS feeds, downloads new episodes, and tags the resulting MP3s.
Each episode gets its own folder:

```
{Podcast Name}/{Episode Title}/01 {Episode Title}.mp3
{Podcast Name}/{Episode Title}/01 poster.jpg
```

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

## Cron

Runs safely on a schedule — a state file tracks what's already been
downloaded, so re-running only picks up genuinely new episodes.

```
*/30 * * * * cd /path/to/podcast-grabber && /usr/bin/python3 podcast_grabber.py --config config.json >> podcast_grabber.log 2>&1
```
