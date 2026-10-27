"""igstudio -- manage several Instagram professional accounts from one place.

`ig` is the command; `python -m igstudio` is the same entry point. It speaks
the official Instagram API (Meta's Graph API, with either the Instagram
Login or the Facebook Login flavour), so it works only for professional
(Business or Creator) accounts that you own and have authorised an app for.
It never drives the app or the website and never stores a password.

Modules:

    graph      the HTTP client: one call shape, errors, retries, paging
    accounts   the registry of accounts and where each one's token lives
    publish    the two-step container flow for images, videos, reels,
               stories and carousels, with the publishing quota checked
    queue      posts scheduled for later, published by `ig run`
    insights   profile, media list, account and media insights
    engage     comments: list, reply, hide, delete
    caption    a caption, hashtags and alt text written by the model
    cli        the command line
"""

__all__ = ["accounts", "caption", "cli", "engage", "graph", "insights", "publish",
           "queue"]
