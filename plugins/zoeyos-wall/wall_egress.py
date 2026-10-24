"""kill-search for a Hermes agent (cloned from Crissa's zoeyos_egress, 2026-10-06).

One switch for what an agent's web tools may reach. The switch is `search.kill` in $KREW_HOME/zoeyos-wall.json
(the same file says what the LAN is, wall_net); `KREW_SEARCH_KILL`=1/0 in the environment outranks the file, for a
launcher or a test. OFF by default: then every function here is a no-op and every caller behaves as before.

While it is ON:
  - every URL this plugin opens (the Wall's player) must be on the LAN (`wall_net.is_lan`, the private ranges +
    localhost, plus names the install lists in `lan_hosts`). A host NAME is never resolved to decide: a lookup
    would itself tell a resolver outside where we meant to go. A redirect is checked again.
  - agents' search goes to the LAN relay (`search.relay_url`, a SearXNG `/search?format=json`) or nowhere,
    and page reads (firecrawl) go to `relay_extract_url` or nowhere (`search_route`).
  - an agent's browser tool may not open an address outside the LAN.
Crissa keeps its own switch for what its own modules open (its installers, its Brave): that one is Crissa's.
"""
from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from . import wall_net
except ImportError:          # loaded as a lone file beside wall_net.py
    import wall_net  # type: ignore[no-redef]  # noqa: E402

SEARCH_DEFAULTS = {"kill": False, "relay_url": "", "relay_extract_url": "", "timeout_s": 10}
ENV_KILL = "KREW_SEARCH_KILL"
_ON = ("1", "on", "true", "yes")
_OFF = ("0", "off", "false", "no")


class Refused(PermissionError):
    """kill-search is on and this call would leave the LAN. The message is for the person, never a key.
    An OSError on purpose: a caller that already treats an unreachable host as "not there" (a status mark,
    a probe) treats a refused one the same way, with no new except clause."""


def search(home: Path | None = None) -> dict:
    out = dict(SEARCH_DEFAULTS)
    got = wall_net.config(home).get("search")
    if isinstance(got, dict):
        out.update({k: v for k, v in got.items() if not str(k).startswith("_")})
    return out


def killed(home: Path | None = None, env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    v = str(e.get(ENV_KILL) or "").strip().lower()
    if v in _ON:
        return True
    if v in _OFF:
        return False
    k = search(home).get("kill")
    return k is True or str(k).strip().lower() in _ON


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""


def _refusal(url: str, what: str) -> str:
    h = host_of(url) or "an address"
    return "kill-search is on: %s would reach %s, outside your network" % (what or "this call", h)


def allowed(url: str, home: Path | None = None) -> bool:
    """Would kill-search let this URL out? (True for any URL while the switch is off.)"""
    if not killed(home):
        return True
    scheme = urllib.parse.urlsplit(str(url)).scheme.lower()
    if scheme in ("file", "data"):
        return True
    return wall_net.is_lan(host_of(url), home=home, resolve=False)


def check(url: str, what: str = "", home: Path | None = None) -> None:
    """Raise Refused if kill-search is on and `url` is not on the LAN."""
    if not allowed(url, home):
        raise Refused(_refusal(url, what))


class _LanRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, what: str, home: Path | None):
        self.what, self.home = what, home

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl, self.home):
            raise Refused(_refusal(newurl, self.what + " (a redirect)"))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(req, timeout: float, what: str = "", home: Path | None = None):
    """urllib.request.urlopen with the switch in front. OFF: exactly urllib's own call."""
    if not killed(home):
        return urllib.request.urlopen(req, timeout=timeout)
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    check(url, what, home)
    return urllib.request.build_opener(_LanRedirect(what, home)).open(req, timeout=timeout)


def search_route(home: Path | None = None) -> dict:
    """Where agents' search and page reads go (the kill x relay table).

    search:  "local" (this machine's own SearXNG), "relay" (the LAN relay) or "off".
    extract: "local" (this machine's own page reader, e.g. firecrawl), "relay" or "off".
    A relay URL that is not on the LAN is never used: under kill it is refused (off), with kill off it is
    reported in `refused` and the route falls back to local.
    """
    s = search(home)
    kill = killed(home)
    relay = str(s.get("relay_url") or "").strip()
    xrelay = str(s.get("relay_extract_url") or "").strip()
    refused = [u for u in (relay, xrelay) if u and not wall_net.is_lan(host_of(u), home=home)]
    relay = "" if relay in refused else relay
    xrelay = "" if xrelay in refused else xrelay
    out = {"kill": kill, "refused": refused, "timeout_s": s.get("timeout_s")}
    if relay:
        out.update(search="relay", search_url=relay)
    else:
        out.update(search="off" if kill else "local", search_url="")
    if xrelay:
        out.update(extract="relay", extract_url=xrelay)
    else:
        out.update(extract="off" if kill else "local", extract_url="")
    return out


# ---- the agents' side: search, page reads and the browser ----------------------------------------------------
# The agent framework that carries this plugin asks `agent_tool_verdict` before each tool
# call. None = let the tool run as it would. A dict = the framework's veto shape {"action": "block", "message"},
# whose message BECOMES the tool's result: that is how a search under kill-search is answered from the LAN relay
# without the framework's own search backend (local SearXNG, firecrawl) ever running.

AGENT_TOOL_PREFIXES = ("web_", "browser_")
_URL = re.compile(r"\bhttps?://[^\s\"'<>]+", re.I)


def _urls_in(x, out: list | None = None) -> list:
    out = [] if out is None else out
    if isinstance(x, str):
        out.extend(_URL.findall(x))
    elif isinstance(x, dict):
        for v in x.values():
            _urls_in(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _urls_in(v, out)
    return out


def _get_json(url: str, timeout: float, what: str, body: dict | None = None, home: Path | None = None):
    import json
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                 headers={"Accept": "application/json", "Content-Type": "application/json"})
    with urlopen(req, timeout, what, home) as r:
        return json.loads(r.read().decode("utf-8") or "null")


def relay_search(query: str, limit: int = 5, home: Path | None = None) -> str:
    """One search through the LAN relay (a SearXNG `/search?format=json`), as the framework's web_search JSON."""
    import json
    r = search_route(home)
    if r["search"] != "relay":
        return json.dumps({"success": False, "error": "search is off: kill-search is on and no relay on your "
                                                      "network is set (search.relay_url)"})
    url = r["search_url"].rstrip("/") + "/search?" + urllib.parse.urlencode({"q": str(query), "format": "json"})
    try:
        got = _get_json(url, float(r.get("timeout_s") or 10), "the search relay", home=home) or {}
    except (OSError, ValueError) as exc:
        return json.dumps({"success": False, "error": "the search relay did not answer: %s" % exc})
    rows = sorted(got.get("results") or [], key=lambda x: -float(x.get("score") or 0))[:max(1, int(limit or 5))]
    web = [{"title": str(x.get("title", "")), "url": str(x.get("url", "")),
            "description": str(x.get("content", "")), "position": i + 1} for i, x in enumerate(rows)]
    return json.dumps({"success": True, "data": {"web": web}, "via": "relay"}, ensure_ascii=False)


def relay_extract(urls, home: Path | None = None) -> str:
    """Page reads through the LAN relay's page reader (a firecrawl `/v1/scrape`), as web_extract JSON; with no
    relay every page answers that page reads are off. This machine never fetches the page itself."""
    import json
    r = search_route(home)
    out = []
    for u in [str(x) for x in (urls if isinstance(urls, (list, tuple)) else [urls]) if x]:
        if r["extract"] != "relay":
            out.append({"url": u, "title": "", "content": "", "error": "page reads are off: kill-search is on "
                        "and no page reader on your network is set (search.relay_extract_url)"})
            continue
        try:
            got = _get_json(r["extract_url"].rstrip("/") + "/v1/scrape", float(r.get("timeout_s") or 10) * 3,
                            "the page-read relay", {"url": u, "formats": ["markdown"]}, home) or {}
            d = got.get("data") or {}
            out.append({"url": u, "title": str((d.get("metadata") or {}).get("title") or ""),
                        "content": str(d.get("markdown") or ""), "error": None if got.get("success", True)
                        else str(got.get("error") or "the relay could not read the page")})
        except (OSError, ValueError) as exc:
            out.append({"url": u, "title": "", "content": "", "error": "the page-read relay did not answer: %s" % exc})
    return json.dumps({"results": out, "via": "relay"}, ensure_ascii=False)


def agent_tool_verdict(tool_name: str, args=None, home: Path | None = None):
    """What the agent framework does with one tool call.

    None                         run the tool as the framework would (always, while kill-search is off and no
                                 relay is set, and for any tool that is not search, a page read or the browser).
    {"action": "block", ...}     refuse it; the message says why (the framework's pre-call veto).
    {"action": "relay", ...}     search or page reads go to the relay: `relay_answer` produces the result in the
                                 framework's execution step, so the framework's own backend never runs.
    """
    name = str(tool_name or "")
    if not name.startswith(AGENT_TOOL_PREFIXES):
        return None
    a = args if isinstance(args, dict) else {}
    kill = killed(home)
    if not kill and not (search(home).get("relay_url") or search(home).get("relay_extract_url")):
        return None
    r = search_route(home)
    if name == "web_search":
        if r["search"] == "relay":
            return {"action": "relay", "kind": "search"}
        if kill:
            return {"action": "block", "message": "search is off: kill-search is on and no search relay on your "
                                                  "network is set (search.relay_url)"}
        return None
    if name == "web_extract":
        if r["extract"] == "relay":
            return {"action": "relay", "kind": "extract"}
        if kill:
            return {"action": "block", "message": "page reads are off: kill-search is on and no page reader on "
                                                  "your network is set (search.relay_extract_url)"}
        return None
    if kill:
        bad = [u for u in _urls_in(a) if not allowed(u, home)]
        if bad:
            return {"action": "block", "message": _refusal(bad[0], "the agent's " + name)}
    return None


def relay_answer(tool_name: str, args=None, home: Path | None = None):
    """The tool's result from the relay, or None when this call is not the relay's (the tool runs as usual)."""
    v = agent_tool_verdict(tool_name, args, home)
    if not v or v.get("action") != "relay":
        return None
    a = args if isinstance(args, dict) else {}
    if v["kind"] == "search":
        return relay_search(a.get("query", ""), a.get("limit") or 5, home)
    return relay_extract(a.get("urls") or [], home)
