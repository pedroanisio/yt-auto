"""The registry of accounts and where each one's token lives.

`$IG_HOME/accounts.json` (default `~/.config/ig/`) keeps one row per
account: a short name you choose, the Instagram professional account id,
which login flow authorised it (`instagram` or `facebook`, graph.py), the
timezone its scheduled posts are read in, and a *reference* to the token,
never the token itself:

    env:IG_TOKEN_SHOP      the variable, loaded from the key files too
    file:/path/to/token    a file holding the token alone, mode 0600

`ig accounts add --token ...` writes the token to `$IG_HOME/tokens/<name>`
and stores the file reference, so a token typed once is never in the
registry, the shell history of later commands, or a log line. Refreshing
a token writes the new one back to its file; a token held in a variable
is moved to a file on refresh, since nothing can write your shell.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import zoneinfo
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ytstudio import env
from ytstudio.core import read_json, write_json

from .graph import Graph, GraphError, Transport

REGISTRY = "accounts.json"
TOKENS_DIR = "tokens"
LOGINS = ("instagram", "facebook")

#: What `ig status` and `verify` read off a professional account.
PROFILE_FIELDS = ("id", "username", "name", "account_type", "followers_count",
                  "follows_count", "media_count", "biography", "website",
                  "profile_picture_url")


def home() -> Path:
    raw = os.environ.get("IG_HOME", "").strip() or "~/.config/ig"
    return Path(raw).expanduser()


#: This checkout, whose key files are read wherever `ig` is run from:
#: ytstudio.env searches relative to the current directory, which is the
#: checkout for `yt` but any folder for `ig` (a post's folder, cron's home).
REPO_ROOT = Path(__file__).resolve().parents[1]


def load_keys() -> None:
    """Key files into the environment: the usual search, then this checkout's."""
    env.load()
    for name in env.CANDIDATES:
        if name.startswith("~"):
            continue
        path = REPO_ROOT / name
        if path.is_file():
            env.load(str(path))


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(when: dt.datetime | None) -> str:
    return when.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat() if when else ""


@dataclass
class Account:
    name: str
    ig_user_id: str
    login: str = "instagram"
    token: str = ""                 # env:NAME or file:PATH
    username: str = ""
    timezone: str = ""              # IANA name; empty = the machine's zone
    added_at: str = ""
    token_expires_at: str = ""      # ISO, when known
    verified_at: str = ""
    notes: str = ""

    def tz(self) -> dt.tzinfo:
        if self.timezone:
            try:
                return zoneinfo.ZoneInfo(self.timezone)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError):
                print(f"warning: {self.name}: unknown timezone {self.timezone!r}, "
                      "using the local one", file=sys.stderr)
        return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc

    def token_days_left(self, at: dt.datetime | None = None) -> float | None:
        if not self.token_expires_at:
            return None
        try:
            expires = dt.datetime.fromisoformat(self.token_expires_at)
        except ValueError:
            return None
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=dt.timezone.utc)
        return (expires - (at or now())).total_seconds() / 86400


class Registry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or home()
        self.path = self.root / REGISTRY
        data = read_json(self.path, warn=False) or {}
        rows = data.get("accounts") if isinstance(data.get("accounts"), dict) else {}
        self.accounts: dict[str, Account] = {}
        for name, row in rows.items():
            if not isinstance(row, dict):
                continue
            known = {k: v for k, v in row.items() if k in Account.__dataclass_fields__}
            known["name"] = name
            known.setdefault("ig_user_id", "")
            self.accounts[name] = Account(**known)

    # -- rows ----------------------------------------------------------------

    def names(self) -> list[str]:
        return sorted(self.accounts)

    def get(self, name: str) -> Account:
        try:
            return self.accounts[name]
        except KeyError:
            known = ", ".join(self.names()) or "none yet: ig accounts add"
            raise KeyError(f"no account named {name!r} (known: {known})") from None

    def select(self, spec: str | None) -> list[Account]:
        """`all`, a name, or names joined by commas; no spec means all."""
        if not spec or spec == "all":
            return [self.accounts[n] for n in self.names()]
        return [self.get(n.strip()) for n in spec.split(",") if n.strip()]

    def save(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        rows = {}
        for name in self.names():
            row = asdict(self.accounts[name])
            row.pop("name")
            rows[name] = row
        return write_json(self.path, {"accounts": rows})

    def add(self, name: str, *, ig_user_id: str, login: str = "instagram",
            token: str = "", token_env: str = "", token_file: str = "",
            timezone: str = "", username: str = "", expires_in: int | None = None,
            notes: str = "") -> Account:
        if not name or "," in name or name == "all" or "/" in name:
            raise ValueError(f"bad account name {name!r}: letters, digits, - and _ only")
        if login not in LOGINS:
            raise ValueError(f"login must be one of {LOGINS}, not {login!r}")
        if sum(bool(x) for x in (token, token_env, token_file)) != 1:
            raise ValueError("give exactly one of --token, --token-env, --token-file")
        if timezone:
            zoneinfo.ZoneInfo(timezone)   # ZoneInfoNotFoundError on a bad name
        ref = f"env:{token_env}" if token_env else f"file:{Path(token_file).expanduser()}"
        account = Account(name=name, ig_user_id=str(ig_user_id), login=login, token=ref,
                          username=username, timezone=timezone, added_at=_iso(now()),
                          notes=notes)
        if token:
            account.token = f"file:{self.store_token(name, token)}"
        if expires_in:
            account.token_expires_at = _iso(now() + dt.timedelta(seconds=int(expires_in)))
        self.accounts[name] = account
        return account

    def remove(self, name: str, *, delete_token: bool = True) -> None:
        account = self.get(name)
        del self.accounts[name]
        if delete_token and account.token.startswith("file:"):
            path = Path(account.token[5:])
            if path.parent == self.root / TOKENS_DIR:
                path.unlink(missing_ok=True)

    # -- tokens ----------------------------------------------------------------

    def store_token(self, name: str, token: str) -> Path:
        folder = self.root / TOKENS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass
        path = folder / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token.strip() + "\n")
        return path

    def token(self, account: Account) -> str:
        """The token itself. Key files are loaded first (ytstudio.env), so an
        `env:` reference finds a variable kept in `_tmp/do_not_delete.env`."""
        ref = account.token
        if ref.startswith("env:"):
            load_keys()
            value = os.environ.get(ref[4:], "").strip()
            if not value:
                raise LookupError(f"{account.name}: ${ref[4:]} is not set "
                                  "(export it or put it in a key file)")
            return value
        if ref.startswith("file:"):
            path = Path(ref[5:]).expanduser()
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise LookupError(f"{account.name}: cannot read token file {path} ({exc})")
            if not value:
                raise LookupError(f"{account.name}: token file {path} is empty")
            return value
        raise LookupError(f"{account.name}: token reference {ref!r} is neither env: nor file:")

    def graph(self, account: Account, *, transport: Transport | None = None, **kw) -> Graph:
        return Graph(self.token(account), login=account.login, transport=transport, **kw)

    # -- remote checks ----------------------------------------------------------

    def verify(self, account: Account, graph: Graph) -> dict:
        """The profile as the API reports it; username and verified_at updated."""
        profile = graph.get(account.ig_user_id, fields=PROFILE_FIELDS)
        if profile.get("username"):
            account.username = profile["username"]
        account.verified_at = _iso(now())
        return profile

    def refresh(self, account: Account, graph: Graph) -> dict:
        """A fresh long-lived token, written back where the old one lives.

        Instagram Login: `refresh_access_token` (the token must be over a day
        old and not expired; the new one lasts 60 days). Facebook Login: the
        `fb_exchange_token` exchange, which needs the app's id and secret in
        IG_APP_ID and IG_APP_SECRET.
        """
        if account.login == "instagram":
            data = graph.get("refresh_access_token", grant_type="ig_refresh_token",
                             access_token=graph.token)
        else:
            load_keys()
            app_id = os.environ.get("IG_APP_ID", "").strip()
            secret = os.environ.get("IG_APP_SECRET", "").strip()
            if not (app_id and secret):
                raise LookupError("a Facebook Login token is refreshed with the app's "
                                  "IG_APP_ID and IG_APP_SECRET; neither is set")
            data = graph.get("oauth/access_token", grant_type="fb_exchange_token",
                             client_id=app_id, client_secret=secret,
                             fb_exchange_token=graph.token)
        token = data.get("access_token")
        if not token:
            raise GraphError(f"refresh returned no access_token: {data}")
        account.token = f"file:{self.store_token(account.name, token)}"
        expires_in = data.get("expires_in")
        account.token_expires_at = _iso(now() + dt.timedelta(seconds=int(expires_in))) \
            if expires_in else ""
        return {"expires_in": expires_in, "token_file": account.token[5:]}


def discover(token: str, *, login: str, transport: Transport | None = None) -> list[dict]:
    """The professional accounts a token reaches, for `ig accounts discover`.

    Instagram Login: `/me` names the one account the user authorised, whose
    `user_id` is the id every other call takes. Facebook Login: each Page
    the user manages may have an `instagram_business_account`.
    """
    graph = Graph(token, login=login, transport=transport)
    if login == "instagram":
        me = graph.get("me", fields=("id", "user_id", "username", "account_type"))
        return [{"ig_user_id": str(me.get("user_id") or me.get("id")),
                 "username": me.get("username", ""), "via": "instagram login",
                 "account_type": me.get("account_type", "")}]
    found = []
    for page in graph.pages("me/accounts", fields=("id", "name",
                                                   "instagram_business_account{id,username}")):
        ig = page.get("instagram_business_account")
        if ig:
            found.append({"ig_user_id": str(ig["id"]), "username": ig.get("username", ""),
                          "via": f"page {page.get('name', page.get('id'))}"})
    return found
