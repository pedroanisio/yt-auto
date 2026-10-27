# `ig`: managing several Instagram accounts

`ig` publishes, schedules, reads and answers for any number of Instagram
professional accounts from one shell, through Meta's official Instagram
API. It never drives the app or the website, never holds a password, and
never prints a token. `./ig --help` lists the commands; `python -m
igstudio` is the same entry point.

```
ig accounts list | add | discover | remove | verify | refresh
ig status [ACCOUNT]                profile, publishing quota, token age
ig media [ACCOUNT] [--limit N]     recent posts
ig insights [ACCOUNT] [--days N | --media ID]
ig comments [ACCOUNT] [--media ID] [--unanswered]
ig post ACCOUNT --image|--reel|--story URL | --carousel URL... [--caption ...] [--at WHEN] [--dry-run]
ig queue list | remove ID | clear
ig run [--loop SECONDS]            publish what is due
ig reply | comment | hide | delete-comment
ig caption "brief" [--goal saves|shares|comments|follows|reach] [--account ACC]
ig house [ACCOUNT] [--stage caption]   the standards a stage is handed
ig record ACCOUNT [--sync]              the account record and its repeats
```

`ACCOUNT` is a name you chose, `all` (the default for reading commands)
or names joined by commas. `--json` on any command prints JSON for a
script to parse.

## What you need, once per account

The API serves **professional** accounts only (Business or Creator; the
switch is free, in the app under Settings, Account type). You also need a
Meta app with the Instagram product added, which is where the tokens come
from. Two flows exist; `ig` speaks both:

| Flow | Host | Token for | Set up where |
|---|---|---|---|
| Instagram API with **Instagram Login** | graph.instagram.com | one account per login | app dashboard, Instagram, API setup with Instagram login |
| Instagram API with **Facebook Login** | graph.facebook.com | every account linked to a Page you manage | app dashboard, Instagram, API setup with Facebook login |

Instagram Login is simpler for a handful of your own accounts: in the
dashboard, add each account under "Generate access tokens", log in as
it, and copy the long-lived token. Grant at least
`instagram_business_basic`, `instagram_business_content_publish`,
`instagram_business_manage_comments` and `instagram_business_manage_insights`.
An app in Development mode works for the accounts you add as testers or
owners, which is all an owner-operator needs; App Review is for serving
other people's accounts.

Then:

```bash
./ig accounts discover --token 'IGAA…'            # which account id the token reaches
./ig accounts add shop --user-id 17841400000000 --token 'IGAA…' --timezone Europe/Lisbon
./ig accounts add brand --user-id 17841400000001 --token-env IG_TOKEN_BRAND
./ig accounts list
```

`--token` writes the token to `~/.config/ig/tokens/<name>` (mode 0600)
and the registry keeps only that file's path. `--token-env` reads a
variable instead, from the shell or from the key files `yt` reads
(`_tmp/do_not_delete.env`, `.env`, `~/.config/pals-notes/.env`). The
registry itself is `~/.config/ig/accounts.json`; `IG_HOME` or `--home`
moves it. A Facebook Login token is added the same way with `--login
facebook`; `discover --login facebook` lists the Pages' linked accounts.

Long-lived tokens last 60 days. `ig accounts refresh --if-within 10` gets
a fresh one when fewer than ten days are left and writes it back; put it
in cron beside `ig run`. For a Facebook Login token the refresh is the
`fb_exchange_token` exchange and needs the app's `IG_APP_ID` and
`IG_APP_SECRET` in the environment or a key file.

## Publishing

Meta fetches the media from a **public URL**; there is no upload in this
flow, so a file on this machine is refused with that message. Host it
where https reaches it (object storage, the scene-render host's web root,
a CDN) and pass the URL.

```bash
./ig post shop --image https://cdn.example/sale.jpg --caption "Hook line
…" --alt "a red coat on a hanger"
./ig post shop --reel https://cdn.example/clip.mp4 --cover https://cdn.example/cover.jpg --caption-file post.txt
./ig post shop --story https://cdn.example/story.mp4
./ig post shop --carousel https://cdn.example/1.jpg https://cdn.example/2.jpg https://cdn.example/3.mp4 --caption "…"
./ig post shop --image … --dry-run                 # the requests, nothing sent
```

A post is checked before any call (kind against media count, video
against image, caption length and hashtag count, public URLs). The flow
is the API's own: a container is created, polled until processed
(`--poll`, `--timeout`), then published; a carousel makes one container
per item and a parent. The 24-hour quota of 100 API-published posts is
read first and a full quota refuses the post; `ig status` shows it.

Formats and limits are Meta's: images JPEG, up to 8 MB, aspect 4:5 to
1.91:1; reels MP4/MOV H.264 + AAC, 3 s to 15 min, 9:16, up to 1 GB;
carousels 2 to 10 items; captions 2200 characters and 30 hashtags.
Files from the `instagram-viral-content` skill (igforge's `render`) meet
these as rendered.

## Scheduling

`--at` queues instead of posting:

```bash
./ig post shop --image … --caption "…" --at "2026-10-28 09:00"   # the account's timezone
./ig post brand --reel … --at +2h
./ig queue list
./ig run                                                       # publishes what is due
```

Nothing runs by itself. A cron line publishes what is due and keeps
tokens fresh:

```
*/5 * * * *  cd /home/pals/src/automation && ./ig run >> ~/.config/ig/run.log 2>&1
0 6 * * *    cd /home/pals/src/automation && ./ig accounts refresh --if-within 10 >> ~/.config/ig/run.log 2>&1
```

or `./ig run --loop 300` in a tmux window does the same in one process.
An item is saved as `done` with its permalink or `failed` with the API's
message the moment its state changes; a transient failure (rate limit,
5xx) stays pending and is tried three times. `ig queue list --state
failed` shows what to fix; `ig queue clear` drops done items.

## Reading and answering

```bash
./ig status                              # every account: followers, posts, quota, token days
./ig media shop --limit 10
./ig insights --days 7                   # views, reach, profile views, interactions; follower series
./ig insights shop --media 1789…         # one post's metrics, by its type (feed, reel, story)
./ig comments --unanswered               # comments nobody from the account has replied to
./ig reply shop 1784…  "Thanks, the link is in the bio."
./ig hide shop 1784…                     # or --unhide; delete-comment removes it
```

Metric names are the API's current ones: `views` replaced `impressions`
and the Reels play counts in April 2025. `follower_count` is refused for
accounts under 100 followers; `ig insights` reports that as a warning
and still prints the totals.

## The account's standards: the house file

The same mechanism as `yt`'s house file (`ytstudio/house.py`): one Markdown
file of rules, layered, read by everything `ig` asks the model, so a rule is
written once and the digest of what a stage was handed is recorded beside
its output.

```
igstudio/house/default.md        the shipped default
~/.config/ig/house.md            every account in the registry
~/.config/ig/house/<account>.md  one account, replacing sections by name
--house FILE / --no-house        for one run
```

Sections are `## all`, `## hook`, `## caption`, `## cta`, `## alt` and
`## reply`; `ig caption` reads the first five. A later file replaces a
section by name and leaves the rest alone, so an account file holding only
`## hook` changes the hook rule and keeps everything else. `ig house
[ACCOUNT] [--stage caption]` prints the block a stage is handed and the
files it came from. The default distils the Instagram skill's virality
playbook: no invented facts, no engagement bait, a hook that declares its
curiosity trigger, a CTA that states a reason.

## The account record

`~/.config/ig/record/<account>.json` keeps one row per published post: the
caption's first line and its opening words, the hashtag set, the hook's
curiosity trigger and the CTA type when they were declared, the permalink
and the time. `ig post` and `ig run` write the row when a post is
published (stories are not recorded); `ig record ACCOUNT --sync` backfills
rows from the API's media list, where the trigger and CTA stay unknown;
`ig record ACCOUNT` lists the last twelve.

Before any post, `ig post` prints measured lines, `account: ...`, where
the new post repeats the record: the same opening words as an earlier
post, the same hashtag set, the same trigger as more than half of the
declared posts, the same CTA type as the last three. They are warnings and
decide nothing, as `yt styles`' channel lines are. `--trigger` and `--cta`
name the hook's trigger and the CTA's type for the record when the caption
did not come from `ig caption`.

The trigger vocabulary is `yt`'s (`ytstudio.narrative.HOOK_TRIGGERS`):
`question`, `paradox`, `unexpected_observation`, `unresolved_outcome`,
`someone_knows`. One vocabulary across YouTube and Instagram means a
channel's openings can be compared across both.

## Captions

```bash
./ig caption "Reel: pour-over in 3 steps, for people who own a kettle and nothing else" --goal shares --account shop
```

writes, with Claude through `claude_engine`, a hook first line with its
declared curiosity trigger, a short body, a CTA that matches the goal with
its type, 3 to 5 specific hashtags, alt text and two alternative hooks to
test. The writer is held to the account's house file and told what the
account's last twelve posts opened on, so it does not repeat an opening, a
formula or a hashtag set; the repeats the result would still make are
printed as `account:` warnings, and the model's cost is printed after the
caption. It invents no facts: numbers come from the brief or are left out,
and engagement bait is refused by the house. Paste the result into
`--caption` or `--caption-file`, and pass the trigger and CTA type with
`--trigger` and `--cta` so the record keeps them.

## Where things are

| Path | Holds |
|---|---|
| `~/.config/ig/accounts.json` | the registry: ids, logins, token *references*, timezones |
| `~/.config/ig/tokens/<name>` | one token per file, mode 0600 |
| `~/.config/ig/queue.json` | scheduled posts and what became of them |
| `~/.config/ig/house.md`, `house/<account>.md` | the standards, layered over `igstudio/house/default.md` |
| `~/.config/ig/record/<account>.json` | the account record: one row per published post |
| `igstudio/` | the package: `graph`, `accounts`, `publish`, `queue`, `insights`, `engage`, `caption`, `house`, `record`, `cli` |
| `tests/test_ig_*.py` | the suite, against a fake API (`tests/ig_fakes.py`); nothing touches the network |

`IG_API_VERSION` names another Graph API version (default `v24.0`);
`IG_NO_NET=1` makes every real call refuse, which the tests set.

## What it does not do

- Direct messages (the Messaging API needs its own permissions and a
  webhook; out of scope for now).
- Uploading local files (the publishing flow takes URLs only).
- Anything the API forbids: following or liking on your behalf, reading
  other accounts' private data, or driving a personal account.
