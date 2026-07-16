# Anshu's Tech Deck

An auto-updating tech-news dashboard. It pulls the latest stories from a set of
trusted feeds, sorts them into **Smartphones / India / AI & Tech**, shows them
as image-rich glass cards on a light background, and lets you click any card to
read the full article at its source. Paywalled sources (Financial Times) show a
short **tailored summary** so you get the gist without a login.

It rebuilds itself **every 3 hours on GitHub's free servers** — your computer
does not need to be on.

---

## What's in this folder

| File | What it does |
|---|---|
| `build.py` | The engine. Fetches feeds, sorts + de-duplicates stories, finds images, and writes the web page. **Edit the top of this file to add/remove sources.** |
| `template.html` | The look of the page (colours, fonts, layout). Edit this to restyle. |
| `requirements.txt` | The Python libraries `build.py` needs. |
| `.github/workflows/deploy.yml` | The automation that rebuilds + republishes the site every 3 hours on GitHub. |
| `public/index.html` | The finished page (created by `build.py`). This is what visitors see. |

---

## A) Preview it on your own computer

You only need to do this if you want to see changes before publishing.

```bash
# 1. one-time setup (creates an isolated Python environment)
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. build the page
python build.py               # takes ~30–60s; writes public/index.html

# 3. open it
open public/index.html        # macOS
# Windows:  start public\index.html
# Linux:    xdg-open public/index.html
```

Re-run `python build.py` any time to pull the newest stories.

---

## B) Put it live on the internet (free, via GitHub)

This is a one-time setup. After it, the site updates itself forever.

Your GitHub account is **anshuneelkanth12-ctrl**, so the steps below use that.

1. **Create a new, empty repository** named `tech-deck` at
   [github.com/new](https://github.com/new). Leave "Add a README" unchecked and
   keep it Public.
2. **Upload this folder** to that repository. Two ways:
   - *Easiest:* on the repo page, click **Add file → Upload files**, drag in
     everything here **except** the `.venv` and `public` folders, and commit.
   - *Or with git:* `git init && git add . && git commit -m "first" && git branch -M main`
     then follow GitHub's "push an existing repository" commands.
3. **Turn on GitHub Pages:** in the repo, go to **Settings → Pages**. Under
   **Build and deployment → Source**, choose **GitHub Actions**.
4. **Run it once now:** go to the **Actions** tab → *Build & deploy Tech Deck* →
   **Run workflow**. Wait ~1–2 minutes for the green tick.
5. **Your site is live** at
   `https://anshuneelkanth12-ctrl.github.io/tech-deck/`.
   Bookmark it. From now on it refreshes automatically every 3 hours.

> If a step looks different or shows an error, tell Claude Code exactly what you
> see on screen and it will walk you through it.

---

## C) Change what appears

Open `build.py` and look at the clearly-marked sections near the top:

- **`SOURCES`** — add a line for a new feed, or set `"enabled": False` to hide
  one. Mark a subscription site with `"paywall": True`, and an India-focused
  site with `"india_source": True`.
- **`KW_PHONE` / `KW_INDIA` / `KW_AI`** — the keywords that decide a story's
  category. Add words to fine-tune where stories land.
- **`MAX_ARTICLES`, `PER_FEED_LIMIT`** — how many stories the page shows.

Save, then either push to GitHub (it rebuilds automatically) or run
`python build.py` locally to preview.

---

## Sources currently included (15)

**Smartphones & general tech:** MacRumors · GSMArena · Android Central ·
Android Headlines · Android Authority · FoneArena · AppleInsider ·
Samsung Newsroom · The Verge
**India:** LiveMint · Economic Times · FoneArena · Smartprix
**Discovery (catches stories the others miss):** Techmeme · Hacker News
**Paywalled (shown with a tailored summary):** Financial Times 🔒

### Sources from the original plan that are currently OFF (and why)

These are kept in `build.py` (marked `"enabled": False`) so they're easy to
re-enable if their feeds come back:

- **WSJ** — its public RSS feeds are frozen on old (Jan 2025) articles, so they
  aren't live news any more. *Financial Times replaces it as the paywalled
  source, with fresh stories and real summaries.*
- **Bloomberg** — has no public feed; only second-hand aggregator headlines
  exist, with no clean link or summary, so it's left out for now.
- **Business Standard** — blocks feed access (HTTP 403).
- **Apple Newsroom** — its feed 404s, and the workaround returns mostly Apple
  TV/Arcade PR. Apple hardware news is already well covered by MacRumors and
  AppleInsider.

If you especially want one of these back, tell Claude Code — some can be
recovered with extra work.

---

## How the auto-update works

The GitHub Action in `.github/workflows/deploy.yml` runs `build.py` on GitHub's
servers **every 3 hours** (and whenever you push a change, or click *Run
workflow*). Each run fetches the newest stories and republishes the page, so
opening your bookmarked URL always shows current news — no server for you to
manage, and your computer can be off.

To change the frequency, edit the `cron` line in that file — `"0 * * * *"` for
hourly, `"*/15 * * * *"` for every 15 minutes. Note that GitHub's free scheduler
is best-effort: during busy periods a scheduled run can be delayed by several
minutes or occasionally skipped, so a short interval is a target, not a
guarantee.

**AI-summary cost & caching:** paywalled (Financial Times) cards get an AI
summary via the Anthropic API, which is pay-as-you-go. Summaries are cached (via
`actions/cache`) and keyed by article URL, so each run only pays to summarize
*new* stories rather than re-writing the same ones every refresh. This keeps the
bill small and matters more the faster you refresh. Switch `AI_MODEL` in
`build.py` to `"claude-haiku-4-5"` for the lowest cost.
