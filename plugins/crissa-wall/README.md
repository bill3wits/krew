# crissa-wall — the Crissa Wall for your agent

krew's plugin. Gives an agent eight tools that drive the avatar on this machine: `wall_state`, `wall_keys`,
`wall_cue`, `wall_home`, `wall_comms`, `wall_speak`, `wall_ask`, `wall_stop` (toolset `crissa`). Each is one HTTP
call to the Wall's player (`CRISSA_PLAYER`, default `http://127.0.0.1:8100`) and nothing else: the plugin reads no
Crissa file. With no player answering, every tool says Crissa is not installed.

## kill-search

The plugin also carries kill-search for the agent's web tools (`search.kill` in `$KREW_HOME/crissa-wall.json`, or
`KREW_SEARCH_KILL=1` in the gateway's environment; template: `config/crissa-wall.example.json`). While it is on,
the agent's `web_search` goes to the search relay on your network (`search.relay_url`, a SearXNG with the JSON
API) or answers that search is off, `web_extract` goes to the relay's page reader (`search.relay_extract_url`, a
firecrawl `/v1/scrape`) or answers that page reads are off, and a `browser_*` tool may not open an address outside
your network. Off, and with no relay set, it changes nothing. Crissa's own switch covers what Crissa opens; set
both when you want both.

## Install

    hermes plugins install bill3wits/krew/plugins/crissa-wall --enable

or, from a krew checkout, `python3 scripts/krew-install.py --only crissa-wall --enable`. Any other agent: use the
MCP server Crissa ships (`scripts/crissa-mcp.py` in the Crissa Wall).
