"""`ig` -- the command line. `ig --help` lists the commands.

Every command takes an account name where it needs one; `all` or a
comma-joined list runs a read-only command over several accounts. Output
is readable text, or JSON with `--json` for a script to parse. Nothing
is printed that holds a token.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

from . import accounts as acc_mod
from . import caption as caption_mod
from . import engage, insights, publish
from . import house as house_mod
from . import queue as queue_mod
from . import record as record_mod
from .graph import GraphError, NoNetwork, Transport

PROG = "ig"


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #

class Context:
    def __init__(self, args, *, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], dt.datetime] = acc_mod.now,
                 engine=None) -> None:
        self.args = args
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        self.now = now
        self.engine = engine
        self.root = Path(args.home).expanduser() if args.home else acc_mod.home()
        self.registry = acc_mod.Registry(self.root)
        self.json = bool(args.json)

    def graph(self, account: acc_mod.Account):
        return self.registry.graph(account, transport=self.transport, sleep=self.sleep)

    def say(self, line: str) -> None:
        print(line, file=sys.stderr if self.json else sys.stdout)

    def house(self, account: str = ""):
        return house_mod.load(self.root, account, explicit=getattr(self.args, "house", None),
                              enabled=not getattr(self.args, "no_house", False))

    def warn_repeats(self, account: str, row: dict) -> list[str]:
        lines = record_mod.repeats(row, record_mod.earlier(self.root, account))
        for line in lines:
            print(f"warning: {line}", file=sys.stderr)
        return lines

    def record(self, account: str, published: dict, post: publish.Post, *, trigger: str = "",
               cta: str = "") -> dict:
        row = record_mod.fingerprint(post.caption, kind=post.kind, trigger=trigger, cta=cta,
                                     media_id=published.get("id", ""),
                                     permalink=published.get("permalink", ""),
                                     published_at=record_mod._iso(published.get("timestamp")),
                                     variant_of=post.variant_of)
        record_mod.add(self.root, account, row)
        return row

    def emit(self, data, text: Callable[[], None] | None = None) -> None:
        if self.json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        elif text:
            text()


def _fmt_when(iso: str) -> str:
    try:
        when = dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return when.strftime("%Y-%m-%d %H:%M %z")


def _one_line(text: str, width: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 1] + "…"


# --------------------------------------------------------------------------- #
# accounts
# --------------------------------------------------------------------------- #

def _token_args(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--token", help="the long-lived token; stored in $IG_HOME/tokens/<name>, "
                                   "mode 0600, never in the registry")
    g.add_argument("--token-env", metavar="VAR", help="read the token from $VAR (key files too)")
    g.add_argument("--token-file", metavar="PATH", help="read the token from this file")
    p.add_argument("--login", choices=acc_mod.LOGINS, default="instagram",
                   help="which flow authorised the token (default: instagram)")


def cmd_accounts_list(ctx: Context) -> int:
    rows = []
    for name in ctx.registry.names():
        a = ctx.registry.get(name)
        days = a.token_days_left(ctx.now())
        rows.append({"name": a.name, "username": a.username, "ig_user_id": a.ig_user_id,
                     "login": a.login, "token": a.token.split(":", 1)[0] + ":…",
                     "timezone": a.timezone, "token_days_left": None if days is None
                     else round(days, 1), "verified_at": a.verified_at})

    def text():
        if not rows:
            print("no accounts yet: ig accounts add <name> --user-id <id> --token <token>")
            return
        print(f"{'name':<14} {'username':<20} {'ig user id':<20} {'login':<10} "
              f"{'token':<6} {'expires':<10} timezone")
        for r in rows:
            exp = "?" if r["token_days_left"] is None else f"{r['token_days_left']:.0f} d"
            print(f"{r['name']:<14} {('@' + r['username']) if r['username'] else '-':<20} "
                  f"{r['ig_user_id']:<20} {r['login']:<10} {r['token']:<6} {exp:<10} "
                  f"{r['timezone'] or '-'}")
    ctx.emit(rows, text)
    return 0


def cmd_accounts_add(ctx: Context) -> int:
    a = ctx.args
    account = ctx.registry.add(a.name, ig_user_id=a.user_id, login=a.login,
                               token=a.token or "", token_env=a.token_env or "",
                               token_file=a.token_file or "", timezone=a.timezone or "",
                               expires_in=a.expires_in, notes=a.notes or "")
    profile = {}
    if not a.no_verify:
        try:
            profile = ctx.registry.verify(account, ctx.graph(account))
        except (GraphError, LookupError, NoNetwork) as exc:
            ctx.registry.save()
            print(f"{account.name}: saved, but the token did not verify: {exc}", file=sys.stderr)
            return 1
    ctx.registry.save()
    ctx.emit({"account": account.name, "ig_user_id": account.ig_user_id,
              "username": account.username, "profile": profile},
             lambda: print(f"added {account.name}"
                           + (f" (@{account.username}, "
                              f"{profile.get('followers_count', '?')} followers)" if profile else "")))
    return 0


def cmd_accounts_discover(ctx: Context) -> int:
    a = ctx.args
    if a.token:
        token = a.token
    elif a.token_env:
        acc_mod.load_keys()
        token = os.environ.get(a.token_env, "")
    elif a.token_file:
        token = Path(a.token_file).expanduser().read_text(encoding="utf-8").strip()
    else:
        print("give --token, --token-env or --token-file", file=sys.stderr)
        return 2
    if not token:
        print("the token is empty", file=sys.stderr)
        return 2
    found = acc_mod.discover(token, login=a.login, transport=ctx.transport)

    def text():
        if not found:
            print("no professional account reachable with this token")
        for row in found:
            print(f"@{row['username'] or '?'}  id {row['ig_user_id']}  via {row['via']}")
        if found:
            print("add one with: ig accounts add <name> --user-id <id> "
                  f"--login {a.login} --token-... ")
    ctx.emit(found, text)
    return 0 if found else 1


def cmd_accounts_remove(ctx: Context) -> int:
    ctx.registry.remove(ctx.args.name, delete_token=not ctx.args.keep_token)
    ctx.registry.save()
    ctx.emit({"removed": ctx.args.name}, lambda: print(f"removed {ctx.args.name}"))
    return 0


def cmd_accounts_verify(ctx: Context) -> int:
    out, code = [], 0
    for account in ctx.registry.select(ctx.args.account):
        try:
            profile = ctx.registry.verify(account, ctx.graph(account))
            out.append({"account": account.name, "ok": True, "profile": profile})
            if not ctx.json:
                print(f"{account.name}: ok  @{profile.get('username')}  "
                      f"{profile.get('followers_count', '?')} followers  "
                      f"{profile.get('media_count', '?')} posts")
        except (GraphError, LookupError, NoNetwork) as exc:
            code = 1
            out.append({"account": account.name, "ok": False, "error": str(exc)})
            if not ctx.json:
                print(f"{account.name}: FAILED  {exc}")
    ctx.registry.save()
    if ctx.json:
        ctx.emit(out)
    return code


def cmd_accounts_refresh(ctx: Context) -> int:
    out, code = [], 0
    for account in ctx.registry.select(ctx.args.account):
        days = account.token_days_left(ctx.now())
        if ctx.args.if_within is not None and days is not None and days > ctx.args.if_within:
            out.append({"account": account.name, "skipped": f"{days:.0f} days left"})
            if not ctx.json:
                print(f"{account.name}: skipped, {days:.0f} days left")
            continue
        try:
            info = ctx.registry.refresh(account, ctx.graph(account))
            out.append({"account": account.name, "ok": True, **info})
            if not ctx.json:
                left = account.token_days_left(ctx.now())
                print(f"{account.name}: refreshed"
                      + (f", {left:.0f} days" if left is not None else ""))
        except (GraphError, LookupError, NoNetwork) as exc:
            code = 1
            out.append({"account": account.name, "ok": False, "error": str(exc)})
            if not ctx.json:
                print(f"{account.name}: FAILED  {exc}")
    ctx.registry.save()
    if ctx.json:
        ctx.emit(out)
    return code


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #

def cmd_status(ctx: Context) -> int:
    out, code = [], 0
    for account in ctx.registry.select(ctx.args.account):
        row = {"account": account.name, "token_days_left": account.token_days_left(ctx.now())}
        try:
            graph = ctx.graph(account)
            row["profile"] = ctx.registry.verify(account, graph)
            used, total = publish.quota(graph, account.ig_user_id)
            row["quota"] = {"used": used, "total": total}
        except (GraphError, LookupError, NoNetwork) as exc:
            row["error"] = str(exc)
            code = 1
        out.append(row)
        if not ctx.json:
            if "error" in row:
                print(f"{account.name}: FAILED  {row['error']}")
                continue
            p = row["profile"]
            days = row["token_days_left"]
            print(f"{account.name}: @{p.get('username')}  {p.get('followers_count', '?')} "
                  f"followers  {p.get('media_count', '?')} posts  quota "
                  f"{row['quota']['used']}/{row['quota']['total']}  token "
                  + ("expiry unknown" if days is None else f"{days:.0f} d left"))
    ctx.registry.save()
    if ctx.json:
        ctx.emit(out)
    return code


def cmd_media(ctx: Context) -> int:
    out = []
    for account in ctx.registry.select(ctx.args.account):
        rows = insights.media(ctx.graph(account), account.ig_user_id, limit=ctx.args.limit)
        out.append({"account": account.name, "media": rows})
        if not ctx.json:
            print(f"== {account.name}")
            for r in rows:
                print(f"{r.get('timestamp', '')[:10]}  {r.get('media_product_type', ''):<6} "
                      f"{r.get('like_count', 0):>5} likes {r.get('comments_count', 0):>4} cmts  "
                      f"{r.get('id')}  {_one_line(r.get('caption', ''), 50)}")
    if ctx.json:
        ctx.emit(out)
    return 0


def cmd_insights(ctx: Context) -> int:
    out = []
    for account in ctx.registry.select(ctx.args.account):
        graph = ctx.graph(account)
        if ctx.args.media:
            data = insights.media_insights(graph, ctx.args.media)
            out.append({"account": account.name, **data})
            if not ctx.json:
                print(f"== {account.name} media {ctx.args.media} ({data['product_type']})")
                for k, v in data["metrics"].items():
                    print(f"  {k:<32} {v}")
                for w in data.get("warnings", []):
                    print(f"  warning: {w}")
            continue
        data = insights.account_insights(graph, account.ig_user_id, days=ctx.args.days,
                                         until=ctx.now())
        out.append({"account": account.name, **data})
        if not ctx.json:
            print(f"== {account.name}, last {data['days']} days")
            for k, v in data["totals"].items():
                print(f"  {k:<20} {v}")
            for name, points in data["series"].items():
                if points:
                    print(f"  {name:<20} {points[0][1]} -> {points[-1][1]}")
            for w in data["warnings"]:
                print(f"  warning: {w}")
    if ctx.json:
        ctx.emit(out)
    return 0


def cmd_comments(ctx: Context) -> int:
    out = []
    for account in ctx.registry.select(ctx.args.account):
        graph = ctx.graph(account)
        if ctx.args.media:
            rows = engage.comments(graph, ctx.args.media, limit=ctx.args.limit)
        else:
            rows = engage.recent(graph, account.ig_user_id, media_limit=ctx.args.posts,
                                 per_media=ctx.args.limit)
        if ctx.args.unanswered:
            rows = engage.unanswered(rows, account.username)
        out.append({"account": account.name, "comments": rows})
        if not ctx.json:
            print(f"== {account.name}: {len(rows)} comments")
            for c in rows:
                replies = len((c.get("replies") or {}).get("data", []))
                print(f"{c.get('timestamp', '')[:16]}  {c.get('id')}  @{c.get('username')}: "
                      f"{_one_line(c.get('text', ''), 70)}"
                      + (f"  [{replies} replies]" if replies else "")
                      + ("  [hidden]" if c.get("hidden") else ""))
    if ctx.json:
        ctx.emit(out)
    return 0


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #

def _post_from_args(a) -> publish.Post:
    if a.image:
        kind, media = "image", [a.image]
    elif a.reel:
        kind, media = "reel", [a.reel]
    elif a.story:
        kind, media = "story", [a.story]
    else:
        kind, media = "carousel", list(a.carousel or [])
    caption = a.caption or ""
    if a.caption_file:
        caption = Path(a.caption_file).expanduser().read_text(encoding="utf-8").strip()
    return publish.Post(kind=kind, media=media, caption=caption, cover_url=a.cover or "",
                        thumb_offset=a.thumb_offset, alt_text=a.alt or "",
                        location_id=a.location or "", share_to_feed=not a.no_share_to_feed,
                        collaborators=list(a.collaborator or []), trial=a.trial or "",
                        ai_generated=bool(a.ai_generated), variant_of=a.variant_of or "")


def cmd_post(ctx: Context) -> int:
    a = ctx.args
    account = ctx.registry.get(a.account)
    post = _post_from_args(a)
    problems = publish.check(post)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 2
    steps = publish.plan(post)
    ctx.warn_repeats(account.name, record_mod.fingerprint(
        post.caption, kind=post.kind, trigger=a.trigger or "", cta=a.cta or "",
        variant_of=post.variant_of))
    if a.at:
        when = queue_mod.parse_when(a.at, account.tz(), now=ctx.now())
        if a.dry_run:
            ctx.emit({"account": account.name, "when": when.isoformat(), "steps": steps},
                     lambda: print(f"would queue a {post.kind} for {account.name} at "
                                   f"{_fmt_when(when.isoformat())}"))
            return 0
        q = queue_mod.Queue(ctx.root)
        item = q.add(account.name, when, post, now=ctx.now())
        item["trigger"], item["cta"] = a.trigger or "", a.cta or ""
        q.save()
        ctx.emit(item, lambda: print(f"queued {item['id']}: {post.kind} for {account.name} at "
                                     f"{_fmt_when(item['when'])}  (ig run publishes it)"))
        return 0
    if a.dry_run:
        ctx.emit({"account": account.name, "steps": steps}, lambda: (
            print(f"would publish a {post.kind} to {account.name}"
                  + (f" (@{account.username})" if account.username else "") + ":"),
            [print(f"  {s['step']}: {json.dumps(s['params'], ensure_ascii=False)}") for s in steps]))
        return 0
    row = publish.publish(ctx.graph(account), account.ig_user_id, post, poll=a.poll,
                          timeout=a.timeout, sleep=ctx.sleep, clock=ctx.clock,
                          say=ctx.say if not ctx.json else (lambda line: None))
    if post.kind != "story":
        ctx.record(account.name, row, post, trigger=a.trigger or "", cta=a.cta or "")
    ctx.emit({"account": account.name, **row},
             lambda: print(f"published to {account.name}: {row.get('permalink') or row['id']}"))
    return 0


def cmd_queue_list(ctx: Context) -> int:
    q = queue_mod.Queue(ctx.root)
    rows = q.listing(ctx.args.state, ctx.args.account if ctx.args.account not in (None, "all") else None)

    def text():
        if not rows:
            print("queue is empty")
        for i in rows:
            post = i.get("post", {})
            tail = ""
            if i.get("state") == "done":
                tail = "  -> " + str((i.get("result") or {}).get("permalink")
                                     or (i.get("result") or {}).get("id"))
            elif i.get("error"):
                tail = f"  ({i['error']})"
            print(f"{i['id']}  {_fmt_when(i['when'])}  {i['account']:<12} "
                  f"{post.get('kind', '?'):<8} {i.get('state'):<8} "
                  f"{_one_line(post.get('caption', ''), 40)}{tail}")
    ctx.emit(rows, text)
    return 0


def cmd_queue_remove(ctx: Context) -> int:
    q = queue_mod.Queue(ctx.root)
    item = q.remove(ctx.args.id)
    q.save()
    ctx.emit(item, lambda: print(f"removed {item['id']}"))
    return 0


def cmd_queue_clear(ctx: Context) -> int:
    q = queue_mod.Queue(ctx.root)
    n = q.clear(ctx.args.state)
    q.save()
    ctx.emit({"removed": n}, lambda: print(f"removed {n} {ctx.args.state} item(s)"))
    return 0


def cmd_run(ctx: Context) -> int:
    a = ctx.args
    q = queue_mod.Queue(ctx.root)

    def publisher(name: str, post: publish.Post) -> dict:
        account = ctx.registry.get(name)
        row = publish.publish(ctx.graph(account), account.ig_user_id, post, sleep=ctx.sleep,
                              clock=ctx.clock, say=ctx.say if not ctx.json else (lambda l: None))
        if post.kind != "story":
            ctx.record(name, row, post)
        return row

    touched_all = []
    while True:
        now = queue_mod.parse_when(a.at, dt.timezone.utc, now=ctx.now()) if a.at else ctx.now()
        touched = queue_mod.run(q, now, publisher, dry_run=a.dry_run,
                                say=ctx.say if not ctx.json else (lambda l: None))
        touched_all.extend(touched)
        if not a.loop:
            break
        pending = len(q.listing("pending"))
        if not ctx.json:
            print(f"{pending} pending; sleeping {a.loop} s")
        ctx.sleep(a.loop)
        q = queue_mod.Queue(ctx.root)
    if ctx.json:
        ctx.emit(touched_all)
    elif not touched_all:
        print("nothing due")
    return 1 if any(i.get("state") == "failed" for i in touched_all) else 0


def cmd_reply(ctx: Context) -> int:
    account = ctx.registry.get(ctx.args.account)
    row = engage.reply(ctx.graph(account), ctx.args.comment_id, ctx.args.text)
    ctx.emit(row, lambda: print(f"replied: {row.get('id')}"))
    return 0


def cmd_comment(ctx: Context) -> int:
    account = ctx.registry.get(ctx.args.account)
    row = engage.comment(ctx.graph(account), ctx.args.media_id, ctx.args.text)
    ctx.emit(row, lambda: print(f"commented: {row.get('id')}"))
    return 0


def cmd_hide(ctx: Context) -> int:
    account = ctx.registry.get(ctx.args.account)
    row = engage.hide(ctx.graph(account), ctx.args.comment_id, hidden=not ctx.args.unhide)
    ctx.emit(row, lambda: print("unhidden" if ctx.args.unhide else "hidden"))
    return 0


def cmd_delete_comment(ctx: Context) -> int:
    account = ctx.registry.get(ctx.args.account)
    row = engage.delete(ctx.graph(account), ctx.args.comment_id)
    ctx.emit(row, lambda: print("deleted"))
    return 0


def cmd_caption(ctx: Context) -> int:
    a = ctx.args
    handle = ""
    if a.account:
        handle = "@" + (ctx.registry.get(a.account).username or a.account)
    brief = a.brief
    if brief == "-":
        brief = sys.stdin.read()
    earlier = record_mod.earlier(ctx.root, a.account) if a.account else []
    data = caption_mod.write(brief, goal=a.goal, handle=handle, kind=a.kind,
                             language=a.language or "", model=a.model, engine=ctx.engine,
                             house=ctx.house(a.account or ""), earlier=earlier)
    for line in data.get("repeats", []):
        print(f"warning: {line}", file=sys.stderr)

    def text():
        print(data["caption"])
        print()
        print(f"hook trigger: {data.get('hook_trigger', '?')}   cta: {data.get('cta_type', '?')}")
        print("alt text: " + data["alt_text"])
        print("hook variants: " + " | ".join(data["hook_variants"]))
        usage = data.get("usage") or {}
        if usage.get("calls"):
            print(f"cost: ${usage['cost_usd']:.4f}")
    ctx.emit(data, text)
    return 0


def cmd_top(ctx: Context) -> int:
    a = ctx.args
    account = ctx.registry.get(a.account)
    ranked = insights.top(ctx.graph(account), account.ig_user_id, limit=a.limit, metric=a.metric,
                          days=a.days, until=ctx.now())
    by_id = {r["media_id"]: r for r in record_mod.rows(ctx.root, account.name)}
    values = {r["id"]: r["value"] for r in ranked}
    for r in ranked:
        parent = (by_id.get(r["id"]) or {}).get("variant_of", "")
        r["variant_of"] = parent
        r["beat_parent"] = (parent in values and r["value"] > values[parent]) if parent else None

    def text():
        if not ranked:
            print(f"{account.name}: no posts in the last {a.days} days")
            return
        print(f"{account.name}: {len(ranked)} posts, last {a.days} days, by {a.metric} "
              f"(median {ranked[0]['median']:.0f}, winner at {insights.WINNER_RATIO:g}x)")
        for r in ranked:
            m = r["metrics"]
            tag = ""
            if r["variant_of"]:
                tag = f"  variant of {r['variant_of']}" + (
                    ", beat it" if r["beat_parent"] else (", did not beat it" if r["beat_parent"] is False else ""))
            print(f"{'*' if r['winner'] else ' '} {r['value']:>8.0f} {r['ratio']:>5.1f}x  "
                  f"{r.get('timestamp', '')[:10]} {str(r.get('media_product_type') or '')[:5]:<5} "
                  f"{r['id']}  {_one_line(r.get('caption', ''), 44)}"
                  + (f"  [reach {m.get('reach')} saved {m.get('saved')} shares {m.get('shares')}]"
                     if m else "  [no insights]") + tag)
    ctx.emit(ranked, text)
    return 0


def cmd_house(ctx: Context) -> int:
    house = ctx.house(ctx.args.account or "")
    block = house.for_stage(ctx.args.stage)
    ctx.emit({"account": ctx.args.account or "", **house.digest(ctx.args.stage),
              "block": block},
             lambda: (print("sources: " + (", ".join(house.sources) or "none")),
                      print(f"stage {ctx.args.stage}: {len(block)} characters, "
                            f"sections {', '.join(sorted(house.sections)) or 'none'}"),
                      print(), print(block or "(nothing: the house is off or empty)")))
    return 0


def cmd_record(ctx: Context) -> int:
    account = ctx.registry.get(ctx.args.account)
    if ctx.args.sync:
        media = insights.media(ctx.graph(account), account.ig_user_id, limit=ctx.args.limit)
        added, updated = record_mod.sync(ctx.root, account.name, media)
        if not ctx.json:
            print(f"{account.name}: {added} added, {updated} updated from the last "
                  f"{len(media)} media")
    rows = record_mod.earlier(ctx.root, account.name, limit=ctx.args.limit)

    def text():
        if not rows:
            print(f"{account.name}: no record yet (ig record {account.name} --sync)")
        for r in rows:
            print(f"{r.get('published_at', '')[:10]}  {r.get('kind') or '?':<9} "
                  f"{r.get('trigger') or '-':<22} {r.get('cta') or '-':<8} "
                  f"{_one_line(r.get('hook', ''), 60)}")
    ctx.emit(rows, text)
    return 0


# --------------------------------------------------------------------------- #
# the parser
# --------------------------------------------------------------------------- #

def _house_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--house", metavar="FILE", help="a house file layered on top of the account's")
    p.add_argument("--no-house", action="store_true", help="ignore every house file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Manage several Instagram professional accounts: publish, schedule, "
                    "read insights, answer comments. See docs/instagram.md.")
    parser.add_argument("--home", help="registry and queue directory (default $IG_HOME or "
                                       "~/.config/ig)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    # accounts
    p = sub.add_parser("accounts", help="the registry: list, add, discover, remove, verify, refresh")
    s = p.add_subparsers(dest="sub", metavar="<action>", required=True)
    s.add_parser("list", help="the accounts").set_defaults(fn=cmd_accounts_list)
    x = s.add_parser("add", help="register an account")
    x.add_argument("name")
    x.add_argument("--user-id", required=True, help="the Instagram professional account id")
    _token_args(x)
    x.add_argument("--timezone", help="IANA zone for scheduled times, e.g. Europe/Lisbon")
    x.add_argument("--expires-in", type=int, help="seconds the token lasts, if known")
    x.add_argument("--notes")
    x.add_argument("--no-verify", action="store_true", help="do not call the API now")
    x.set_defaults(fn=cmd_accounts_add)
    x = s.add_parser("discover", help="which professional accounts a token reaches")
    _token_args(x)
    x.set_defaults(fn=cmd_accounts_discover)
    x = s.add_parser("remove", help="forget an account (and its stored token)")
    x.add_argument("name")
    x.add_argument("--keep-token", action="store_true")
    x.set_defaults(fn=cmd_accounts_remove)
    x = s.add_parser("verify", help="call the API with each token")
    x.add_argument("account", nargs="?", default="all")
    x.set_defaults(fn=cmd_accounts_verify)
    x = s.add_parser("refresh", help="get a fresh 60-day token")
    x.add_argument("account", nargs="?", default="all")
    x.add_argument("--if-within", type=float, metavar="DAYS",
                   help="only when the token expires within DAYS")
    x.set_defaults(fn=cmd_accounts_refresh)

    p = sub.add_parser("status", help="profile, publishing quota and token age per account")
    p.add_argument("account", nargs="?", default="all")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("media", help="recent posts")
    p.add_argument("account", nargs="?", default="all")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_media)

    p = sub.add_parser("insights", help="account totals, or one media's metrics")
    p.add_argument("account", nargs="?", default="all")
    p.add_argument("--days", type=int, default=7, help="window, at most 30")
    p.add_argument("--media", metavar="MEDIA_ID", help="metrics of one post instead")
    p.set_defaults(fn=cmd_insights)

    p = sub.add_parser("comments", help="comments on recent posts")
    p.add_argument("account", nargs="?", default="all")
    p.add_argument("--media", metavar="MEDIA_ID", help="one post's comments")
    p.add_argument("--posts", type=int, default=10, help="how many recent posts to read")
    p.add_argument("--limit", type=int, default=50, help="comments per post")
    p.add_argument("--unanswered", action="store_true", help="only those you have not replied to")
    p.set_defaults(fn=cmd_comments)

    p = sub.add_parser("post", help="publish now, or queue with --at")
    p.add_argument("account")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--image", metavar="URL")
    g.add_argument("--reel", metavar="URL", help="video URL; also the feed video format")
    g.add_argument("--story", metavar="URL", help="image or video URL")
    g.add_argument("--carousel", metavar="URL", nargs="+", help="2 to 10 image/video URLs")
    p.add_argument("--caption")
    p.add_argument("--caption-file", metavar="PATH")
    p.add_argument("--alt", help="alt text (image)")
    p.add_argument("--cover", metavar="URL", help="reel cover image")
    p.add_argument("--thumb-offset", type=int, metavar="MS", help="reel cover frame")
    p.add_argument("--no-share-to-feed", action="store_true", help="reel in the Reels tab only")
    p.add_argument("--location", metavar="PAGE_ID")
    p.add_argument("--collaborator", action="append", metavar="USERNAME")
    p.add_argument("--at", metavar="WHEN", help="queue for later: 2026-10-28T09:00, +2h, +1d")
    p.add_argument("--trial", choices=sorted(publish.TRIALS),
                   help="post a reel as a trial: shown to non-followers first; auto graduates it "
                        "on performance, manual leaves that to the app")
    p.add_argument("--ai-generated", action="store_true", help="set Meta's AI label on the post")
    p.add_argument("--variant-of", metavar="MEDIA_ID",
                   help="the post this is a re-cut of, for the account record and ig top")
    p.add_argument("--trigger", choices=record_mod.HOOK_TRIGGERS,
                   help="the hook's curiosity trigger, for the account record")
    p.add_argument("--cta", choices=record_mod.CTA_TYPES, help="the CTA type, for the record")
    p.add_argument("--dry-run", action="store_true", help="show the requests, send nothing")
    p.add_argument("--poll", type=float, default=5.0, help="seconds between status checks")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for processing")
    p.set_defaults(fn=cmd_post)

    p = sub.add_parser("queue", help="the scheduled posts")
    s = p.add_subparsers(dest="sub", metavar="<action>", required=True)
    x = s.add_parser("list")
    x.add_argument("account", nargs="?", default="all")
    x.add_argument("--state", choices=queue_mod.STATES)
    x.set_defaults(fn=cmd_queue_list)
    x = s.add_parser("remove")
    x.add_argument("id")
    x.set_defaults(fn=cmd_queue_remove)
    x = s.add_parser("clear", help="drop done or failed items")
    x.add_argument("--state", choices=("done", "failed"), default="done")
    x.set_defaults(fn=cmd_queue_clear)

    p = sub.add_parser("run", help="publish every queued post that is due")
    p.add_argument("--loop", type=float, metavar="SECONDS", help="keep running, checking every "
                                                                 "SECONDS")
    p.add_argument("--at", metavar="WHEN", help=argparse.SUPPRESS)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("reply", help="reply to a comment")
    p.add_argument("account")
    p.add_argument("comment_id")
    p.add_argument("text")
    p.set_defaults(fn=cmd_reply)
    p = sub.add_parser("comment", help="comment on one of your posts")
    p.add_argument("account")
    p.add_argument("media_id")
    p.add_argument("text")
    p.set_defaults(fn=cmd_comment)
    p = sub.add_parser("hide", help="hide (or --unhide) a comment")
    p.add_argument("account")
    p.add_argument("comment_id")
    p.add_argument("--unhide", action="store_true")
    p.set_defaults(fn=cmd_hide)
    p = sub.add_parser("delete-comment", help="delete a comment")
    p.add_argument("account")
    p.add_argument("comment_id")
    p.set_defaults(fn=cmd_delete_comment)

    p = sub.add_parser("caption", help="write a caption, hashtags and alt text with the model")
    p.add_argument("brief", help="what the post is about and shows; '-' reads stdin")
    p.add_argument("--goal", choices=caption_mod.GOALS, default="saves")
    p.add_argument("--kind", choices=publish.KINDS, default="image")
    p.add_argument("--account", help="name the account's handle in the brief")
    p.add_argument("--language")
    p.add_argument("--model")
    _house_args(p)
    p.set_defaults(fn=cmd_caption)

    p = sub.add_parser("top", help="the account's posts ranked by a metric against its median")
    p.add_argument("account")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--limit", type=int, default=20, help="how many recent posts to read")
    p.add_argument("--metric", choices=insights.TOP_METRICS, default="views")
    p.set_defaults(fn=cmd_top)

    p = sub.add_parser("house", help="the standards a stage is held to, after layering")
    p.add_argument("account", nargs="?", default="")
    p.add_argument("--stage", choices=house_mod.STAGES, default="caption")
    _house_args(p)
    p.set_defaults(fn=cmd_house)

    p = sub.add_parser("record", help="the account record: what each post opened on and asked for")
    p.add_argument("account")
    p.add_argument("--sync", action="store_true", help="add rows from the API's media list")
    p.add_argument("--limit", type=int, default=record_mod.EARLIER_LIMIT)
    p.set_defaults(fn=cmd_record)
    return parser


def main(argv: list[str] | None = None, **ctx_kw) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ctx = Context(args, **ctx_kw)
        return int(args.fn(ctx) or 0)
    except (GraphError, LookupError, ValueError, NoNetwork, OSError) as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted", file=sys.stderr)
        return 130
