# Contributing

Thanks for helping improve the Pinterest Ads MCP server.

## Development setup

```bash
git clone https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp.git
cd pinterest-ads-mcp
uv venv && uv pip install -e ".[test,dev]"
```

Or with plain Python 3.11 or later:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[test,dev]"
```

To try it against a real or Sandbox ad account, follow
[docs/setup-pinterest-ads-api.md](docs/setup-pinterest-ads-api.md) and run
`.venv/bin/pinterest-ads-mcp auth`. Pinterest's Sandbox (`PINTEREST_ADS_SANDBOX=true`) is the
safest place to try writes.

## Checks

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/pinterest_ads_mcp
.venv/bin/pytest
```

Tests run offline: HTTP is mocked with `respx`, and no Pinterest credentials are needed.
`pre-commit install` runs the same checks before each commit.

## Pull requests

- Keep secrets, tokens, ad account IDs, customer lists and report data out of commits, issues
  and test fixtures. Use obvious placeholders such as `123456789`.
- New tools go in the matching module under `src/pinterest_ads_mcp/tools/`, use the
  `pinterest_ads_` prefix, and need an entry in `tool_catalog.py` (title and annotations) and a
  row in `docs/tools.md`. `tests/test_mcp_client.py` fails otherwise.
- Writes must go through `PinterestAdsClient.request` (dry-run, audit, redaction) and use
  `safety.assess` / `safety.refusal` for confirm words. Archives need `ARCHIVE`, deletes
  `DELETE`, and big batches or bid and budget rises `APPLY`.
- Never paste Pinterest's API documentation, OpenAPI specification, SDK or sample code into the
  repository. Write your own code and descriptions and link to the official docs instead.
- Update the README, `CHANGELOG.md` and the skill when tools, scopes or setup change.
