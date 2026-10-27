---
name: ig
description: Operate this repo's ig tool (igstudio) that manages several Instagram professional accounts through Meta's official API, publishes images, reels, stories and carousels, schedules them, reads insights and comments, ranks posts, and writes captions held to an account's house file and record. Use whenever the conversation is about posting, scheduling, checking or answering on Instagram with `ig`, registering an account or its token, hosting media for a post, where an account's files live, or what the API refuses.
---

# Operating ig

This file, `igstudio/SKILL.md`, is the source of truth for how `ig` is
operated. `.claude/skills/ig/SKILL.md` is a link to it, and
`tests/test_ig_skill.py` fails when the link breaks or a command goes
unnamed here. The depth is in `docs/instagram.md` (setup, every command's
flags, where things live) and `ig <command> --help`.

## What this is

`ig` (`./ig` at the repo root, or `python3 -m igstudio`) speaks Meta's
Instagram API for any number of professional accounts you own: the
Instagram Login flavour (graph.instagram.com) or the Facebook Login one
(graph.facebook.com). It never drives the app or the website, never holds
a password, and never prints a token. Content is made elsewhere: the
`instagram-viral-content` skill renders reels, stories and carousels;
`ig` publishes what is rendered.

## The invariants

1. **A token is a reference, never a value.** The registry
   (`~/.config/ig/accounts.json`) holds `env:VAR` or `file:PATH`; a token
   typed once goes to `~/.config/ig/tokens/<name>` at mode 0600. Nothing
   prints one, a `--json` listing shows `file:…`, and the tests run with
   `IG_NO_NET` so the real transport refuses.
2. **Meta fetches the media; nothing is uploaded.** Every image and video
   is a public https URL. A local path is refused before any call. Images
   must be JPEG (a PNG is converted first), 4:5 to 1.91:1; a reel is
   H.264 and AAC, 9:16, under Meta's bitrate cap.
3. **The API is the only publisher.** A post is a container created, polled
   to FINISHED, then published; a carousel is one container per item and a
   parent. The 24-hour quota (100 posts) is read first and a full quota
   refuses the post. Orphan containers from a failed attempt expire on
   their own.
4. **The house and the record bind what the model writes.** `ig caption`
   is handed the account's house block (`igstudio/house.py`: the shipped
   default, `~/.config/ig/house.md`, `house/<account>.md`, `--house`) and
   the account's last twelve posts (`igstudio/record.py`), declares the
   hook's trigger in `ytstudio.narrative.HOOK_TRIGGERS` and the CTA's type,
   and the repeats it would still make are printed as `account:` warnings.
   `ig post` prints the same warnings before sending; they decide nothing.
5. **A story is not recorded and takes no caption.** Stories appear in
   posting order, live 24 hours, and the API gives them no caption, no
   comment and no sticker; credits for a story go on screen.
6. **Nothing is committed to the record that was not published.** A row is
   written by `ig post` and `ig run` after `media_publish` answers, never
   before; `ig record --sync` backfills from the API's own media list.

## The commands and their files

| Command | Does | Writes |
|---|---|---|
| `ig accounts list` / `add` / `discover` / `remove` / `verify` / `refresh` | the registry; `discover` says which account id a token reaches; `refresh` gets a fresh 60-day token and writes it back (`--if-within DAYS` for cron) | `accounts.json`, `tokens/<name>` |
| `ig status [ACCOUNT]` | profile, publishing quota, token days left | |
| `ig media [ACCOUNT]` | recent posts | |
| `ig insights [ACCOUNT] [--days N \| --media ID]` | account totals (`views` replaced `impressions` in April 2025) or one post's metrics by its product type | |
| `ig top ACCOUNT [--days N] [--metric views]` | posts ranked against the account's median; a winner is `WINNER_RATIO` (1.5x, a house prior) above it; a `--variant-of` post says whether it beat its parent | |
| `ig comments [ACCOUNT] [--media ID] [--unanswered]` | comments on recent posts | |
| `ig post ACCOUNT --image\|--reel\|--story URL \| --carousel URL...` | publish now, or queue with `--at WHEN`; `--dry-run` prints the requests; `--trial auto\|manual` posts a reel as a trial (about 1,000 followers needed); `--ai-generated` sets Meta's label; `--trigger`, `--cta`, `--variant-of` label the record row | `queue.json` (with `--at`), `record/<account>.json` |
| `ig queue list` / `remove ID` / `clear` | the scheduled posts | `queue.json` |
| `ig run [--loop SECONDS]` | publishes every queued post that is due; a transient failure retries three times | `queue.json`, `record/<account>.json` |
| `ig reply` / `ig comment` / `ig hide` / `ig delete-comment` | answering | |
| `ig caption "brief" [--goal] [--account]` | a caption with its trigger and CTA type, hashtags, alt text, two hook variants, the repeats it would make and the model's cost | |
| `ig house [ACCOUNT] [--stage]` | the standards block a stage is handed, after layering | |
| `ig record ACCOUNT [--sync]` | the account record | `record/<account>.json` |

Keys load as `yt`'s do, and from this checkout's `.env` and
`_tmp/do_not_delete.env` whatever the current directory
(`accounts.load_keys`), so `ig` runs from cron and from a post's folder.
`IG_HOME` or `--home` moves the registry; `IG_API_VERSION` names another
Graph API version; `IG_NO_NET=1` makes every real call refuse.

## Hosting media for a post

The media lives in the public repo `github.com/pedroanisio/ig-assets`,
one folder per post. Serve **images from raw.githubusercontent.com**
(image/jpeg) and **video from cdn.jsdelivr.net** (video/mp4; raw GitHub
serves an mp4 as a generic binary, which Meta sometimes rejects; jsDelivr
stops at 20 MB, so a heavier reel is re-encoded at about 3 to 4 Mbps).
Check each URL with a request before posting; a cold CDN edge on Meta's
side has refused a carousel item that answered fine from here, and the
retry on raw GitHub went through.

## Traps already paid for

| Trap | What happens | What to do |
|---|---|---|
| A story fails and is retried | it posts after the ones that followed, since stories appear in posting order | delete it in the app and repost the ones after it, or accept the order |
| A token typed on the command line | it is in the shell's history | put it in a key file and register with `--token-env` |
| `ig` run from another directory | the key files were not found (the search is relative to the current directory) | fixed: `accounts.load_keys` reads this checkout's key files too |
| A carousel of PNG slides | the API accepts JPEG only | convert first (Pillow, quality 92) |
| A reel at 35 to 43 Mbps | over Meta's 25 Mbps cap and over the CDN's 20 MB | re-encode at a capped bitrate; keep the master beside it |
| Library music on a reel | the Audio API needs the Facebook Login flavour; an Instagram Login account cannot attach a track | mix a licensed bed into the file before publishing, or move the account to Facebook Login |
| Two sessions posting to one account | the record and the quota are shared, and `ig top` shows posts this session never made | say so before trial reels go on; the record is the one place both are seen |

## Where to read more

- `docs/instagram.md`: the Meta app setup, every command, the house file,
  the account record, what it does not do.
- `igstudio/house/default.md`: the shipped standards, distilled from the
  Instagram skill's virality playbook.
- `tests/test_ig_*.py`: the suite, against `tests/ig_fakes.py`; nothing
  touches the network.
