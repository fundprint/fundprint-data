"""One HTTP GET for the acquire layer, with a fallback for TLS fingerprinting.

## The problem

A growing number of sites sit behind a WAF that classifies clients by their **TLS
handshake fingerprint** (JA3), not by anything the client says about itself. To
such a WAF, ``httpx`` is a bot and ``curl`` is not, and no combination of headers
changes that. Two sources Fundprint needs behave exactly this way: Caravel Autism
Health's center pages and the law-firm page documenting the ABS Kids acquisition
both return **403 to httpx and 200 to curl, with the identical User-Agent**.

That is a fingerprint block, not a policy. Neither site disallows us in
robots.txt, and Caravel publishes its addresses as schema.org markup precisely so
that machines will read them.

## What this does, and what it deliberately does not do

On a 403 (or 429), the same request is retried once through ``curl``, **carrying
the same FundprintBot User-Agent and the same contact email**. We do not change
who we say we are, we change which TLS stack does the handshake.

That distinction is the whole point, and it is a line worth keeping:

* We do **not** impersonate a browser. No spoofed Chrome User-Agent, no
  browser-impersonating TLS library, no cookie or header games to look human.
* We still identify ourselves and give a contact address on every request, so any
  operator who wants us to stop can find us and say so.
* We still respect robots.txt. A source that disallows us stays unfetched, and a
  403 is never treated as permission.

A site that wants Fundprint gone can say so in robots.txt or email the address in
our User-Agent, and we will comply. Being blocked by an automated fingerprint
heuristic that cannot read either of those is not the same as being told no.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

import httpx

logger = logging.getLogger(__name__)

FUNDPRINT_UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"

# Statuses worth retrying through the other client. A 403 from a WAF and a 403
# from an access-control rule look the same over the wire, so a retry that also
# fails is simply a failure: we never treat a block as consent.
_FINGERPRINT_STATUSES = frozenset({403, 429})

_CURL_TIMEOUT_SEC = 45


class FetchError(RuntimeError):
    """A GET that failed through every client available."""


def _curl_get(url: str, timeout: int = _CURL_TIMEOUT_SEC) -> bytes:
    """GET *url* via curl, with the same identity we send everywhere else."""
    curl = shutil.which("curl")
    if not curl:
        raise FetchError(f"{url}: blocked, and curl is not installed to retry with")
    proc = subprocess.run(
        [
            curl,
            "--silent",
            "--show-error",
            "--location",
            "--max-time",
            str(timeout),
            "--user-agent",
            FUNDPRINT_UA,
            "--write-out",
            "%{http_code}",
            url,
        ],
        capture_output=True,
        timeout=timeout + 15,
    )
    if proc.returncode != 0:
        raise FetchError(f"{url}: curl failed: {proc.stderr.decode(errors='replace')[:200]}")
    body, status = proc.stdout[:-3], proc.stdout[-3:].decode(errors="replace")
    if status != "200":
        raise FetchError(f"{url}: curl also got HTTP {status}")
    return body


def get(url: str, *, client: httpx.Client | None = None, timeout: float = 45.0) -> bytes:
    """GET *url* and return its body, retrying a fingerprint block through curl.

    Raises FetchError if the URL cannot be fetched by any client. Callers should
    let that propagate: a source we cannot fetch is a source we cannot snapshot,
    and a claim we cannot snapshot is a claim we do not publish.
    """
    headers = {"User-Agent": FUNDPRINT_UA}
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = client.get(url, headers=headers)
        if resp.status_code == 200:
            return resp.content
        if resp.status_code not in _FINGERPRINT_STATUSES:
            resp.raise_for_status()
        logger.info(
            "%s returned %d to httpx; retrying via curl with the same user-agent",
            url,
            resp.status_code,
        )
    except httpx.RequestError as exc:
        logger.info("%s: %s; retrying via curl", url, type(exc).__name__)
    finally:
        if own:
            client.close()

    return _curl_get(url)
