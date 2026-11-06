# Pinterest Ads MCP Server (read + write)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

An [MCP](https://modelcontextprotocol.io) server for the
[Pinterest API v5](https://developers.pinterest.com/docs/api/v5/introduction/) (ads). Use it
from Claude, ChatGPT, Cursor, Codex or any MCP client to report on and manage Pinterest ad
accounts: campaigns, ad groups, ads, keywords, audiences, catalogs and product groups,
analytics and reports.

Maintained by [AI Shopping Feeds](https://github.com/AI-Shopping-Feeds). It offers:

- **the advertiser workflow on API v5**: ad accounts and business access, campaigns, ad groups, ads (promoted Pins) and catalog ads, keywords and negatives, targeting, audiences and customer lists, conversion tags, catalogs, feeds and product groups
- **analytics and reports**: quick synchronous metrics, and async reports with one call that waits with progress and returns the rows
- **bulk changes**: pause or activate many entities, adjust bids and budgets by a percentage, Pinterest bulk upsert and export
- **safety**: dry-run, confirm words, spend guards, new campaigns paused by default, an audit log, read-only mode and tool groups
- **every other endpoint** through a read-only and a confirm-gated raw request tool

Pinterest has announced its own hosted [Pinterest MCP Server](https://developers.pinterest.com/about-pinterest-mcp/)
but it isn't available yet (sign-up only). This one runs on your own machine with your own
Pinterest app, works now, and adds dry-run, confirm words, spend guards and an audit log.

Not affiliated with or endorsed by Pinterest.

Part of the AI Shopping Feeds MCP family: [Google Ads](https://github.com/AI-Shopping-Feeds/google-ads-mcp) · [Google Merchant Centre](https://github.com/AI-Shopping-Feeds/google-merchant-centre-mcp) · [Google Analytics](https://github.com/AI-Shopping-Feeds/google-analytics-mcp) · [YouTube Studio](https://github.com/AI-Shopping-Feeds/youtube-studio-mcp) · [Amazon Ads](https://github.com/AI-Shopping-Feeds/amazon-ads-mcp) · [Apple Ads](https://github.com/AI-Shopping-Feeds/apple-ads-mcp) · [Microsoft Ads](https://github.com/AI-Shopping-Feeds/microsoft-ads-mcp) · [TikTok Ads](https://github.com/AI-Shopping-Feeds/tiktok-ads-mcp) · **[Pinterest Ads](https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp)** · [Snapchat Ads](https://github.com/AI-Shopping-Feeds/snapchat-ads-mcp) · [OpenAI Ads](https://github.com/AI-Shopping-Feeds/openai-ads-mcp) · [AppLovin](https://github.com/AI-Shopping-Feeds/applovin-mcp).

## Let your agent set it up

Paste this into Claude Code, Codex, Cursor or any agent that can read web pages and edit files:

```text
Set up the Pinterest Ads MCP server for me by following https://raw.githubusercontent.com/AI-Shopping-Feeds/pinterest-ads-mcp/main/skills/pinterest-ads-mcp/SKILL.md
```

The agent checks whether it's already connected, installs the runner if you agree, walks you
through creating and approving a Pinterest app and signing in, adds the server to your
client's config without touching your other servers, and checks it works by calling
`pinterest_ads_health_check`.

The same file is an [Agent Skill](https://agentskills.io), so your agent can keep it as a guide to using the tools well. For Claude Code, copy the folder into your skills:

```bash
git clone --depth 1 https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp.git /tmp/pinterest-ads-mcp
cp -r /tmp/pinterest-ads-mcp/skills/pinterest-ads-mcp ~/.claude/skills/
```

Other agents that support skills load the same `skills/pinterest-ads-mcp` folder from their own skills directory.

## What it can do

81 tools, all prefixed `pinterest_ads_`. The full list is in [docs/tools.md](docs/tools.md).

### Read

| Area | Tools |
|---|---|
| Accounts | `list_ad_accounts` (owned and shared), `get_ad_account`, `get_user_account`, `list_businesses`, `list_business_assets` |
| Campaign structure | `list_campaigns`, `list_ad_groups`, `list_ads`, `list_product_group_promotions`, `get_bid_floors` |
| Pins for ads | `list_pins`, `get_pin`, `list_boards`, `list_board_pins` |
| Keywords | `list_keywords`, `get_keyword_metrics`, `suggest_keywords` |
| Targeting | `get_targeting_options`, `list_targeting_templates`, `estimate_audience_size`, `list_audiences`, `get_audience`, `list_customer_lists`, `list_conversion_tags`, `get_conversion_tag` |
| Catalogs | `list_catalogs`, `list_catalog_feeds`, `list_feed_processing_results`, `list_item_issues`, `get_catalog_items`, `list_product_groups`, `list_product_group_products`, `get_product_group_product_counts` |
| Analytics | `get_analytics` (account, campaign, ad group, ad or product group, with targeting breakdowns), `list_metrics`, `get_metrics_ready_state`, `get_audience_insights` |
| Reports | `run_report` (request, wait, return rows), `create_report`, `get_report`, `download_report`, `list_report_templates`, `bulk_export`, `get_bulk_request` |
| Anything else | `read_request` |

### Write

| Area | Tools |
|---|---|
| Campaigns, ad groups, ads | `create_campaigns`, `update_campaigns`, `archive_campaigns` and the same for `ad_groups` and `ads`; `create_product_group_promotions`, `update_product_group_promotions` |
| Budgets, bids, status | `set_statuses`, `adjust_ad_group_bids`, `adjust_budgets`, `bulk_upsert` |
| Pins for ads | `create_ad_pin` (ad-only Pin from an image URL) |
| Keywords | `create_keywords`, `create_negative_keywords` (ad group, campaign or account level), `archive_keywords` |
| Audiences | `create_audience`, `update_audience`, `delete_audience`, `create_customer_list`, `update_customer_list` |
| Catalogs | `batch_catalog_items`, `ingest_catalog_feed`, `create_product_group`, `update_product_group`, `delete_product_group` |
| Anything else | `request` |

### Safety

- **Dry-run** (`PINTEREST_ADS_DRY_RUN=true` or `pinterest_ads_set_dry_run`) records writes in the audit log and returns what would be sent, without sending it. Reads and reports still work.
- **Confirm words.** Archives need `confirm="ARCHIVE"` (Pinterest can't un-archive), deletes `confirm="DELETE"`. Batches over `PINTEREST_ADS_BULK_THRESHOLD` (10), bid or budget rises over `PINTEREST_ADS_MAX_INCREASE_PCT` (20%) or whose current value can't be read, values over `PINTEREST_ADS_MAX_BUDGET` / `PINTEREST_ADS_MAX_BID`, bulk upserts and raw writes need `confirm="APPLY"`. Without it nothing is sent and the reply shows the old and new values.
- **Paused by default.** New campaigns, ad groups, ads and product group promotions start `PAUSED`.
- **Audit log** of every write (`~/.pinterest-ads-mcp/logs/audit.jsonl`, owner-only), with only the old values of the fields a write changed. Entries are pruned after `PINTEREST_ADS_AUDIT_RETENTION_DAYS` (30); `pinterest-ads-mcp purge` deletes it and the stored token.
- **Read-only mode** (`PINTEREST_ADS_READ_ONLY=true`) and **tool groups** (`PINTEREST_ADS_TOOL_GROUPS`) limit what the agent can do.
- **Retiring objectives** (`CATALOG_SALES`, `WEB_CONVERSION`) are refused: Pinterest stops API creation on 1 February 2027.
- The HTTP transport refuses non-local addresses unless you set `PINTEREST_ADS_ALLOW_REMOTE=true`; customer-list identifiers are hashed locally; only Pinterest hosts are called.
- What Pinterest's terms require and how the server follows them: [docs/terms-and-data.md](docs/terms-and-data.md).

## Supported clients

| Client | Works? | How |
|---|---|---|
| Claude Code | ✅ | Local server, `claude mcp add` (below) |
| Claude Desktop | ✅ | Local server, `claude_desktop_config.json` |
| Cursor | ✅ | Local server, `~/.cursor/mcp.json` |
| VS Code (Copilot) | ✅ | Local server, `.vscode/mcp.json` (uses `servers` instead of `mcpServers`) |
| Codex | ✅ | Local server, `~/.codex/config.toml` |
| Gemini CLI | ✅ | Local server, `~/.gemini/settings.json` |
| Windsurf and other local clients | ✅ | Local server, same JSON entry |
| Claude.ai and ChatGPT | ❌ not yet | These need a remote server with sign-in. This server's HTTP transports have no sign-in, so don't expose them publicly. Use a local client. |

## Setup

### 1. Install uv

The server runs with `uvx` from [uv](https://docs.astral.sh/uv/getting-started/installation/)
(`brew install uv` on macOS) and needs Python 3.11 or later. It installs straight from this
repository:

```shell
uvx --from git+https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp.git pinterest-ads-mcp version
```

Pin a release by adding `@vX.Y.Z` to the URL.

### 2. Get credentials

You need your own Pinterest app. Step by step, with review times and the Standard access
upgrade: [docs/setup-pinterest-ads-api.md](docs/setup-pinterest-ads-api.md).

1. With a Pinterest **business** account, create an app at
   [developers.pinterest.com](https://developers.pinterest.com/apps/) (**Connect app**) and
   wait for Trial access approval. The app name can't contain "Pinterest".
2. On the app's **Configure** tab, add the redirect URI `http://localhost:8765/callback` and
   note the **App id** and **App secret key**.
3. Put `PINTEREST_ADS_APP_ID=...` and `PINTEREST_ADS_APP_SECRET=...` in
   `~/.pinterest-ads-mcp/.env` (so the secret stays out of your shell history), then sign in
   once. It opens a browser and saves a refresh token to `~/.pinterest-ads-mcp/secrets/`,
   owner-only:

   ```shell
   uvx --from git+https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp.git pinterest-ads-mcp auth
   ```

4. For real use, upgrade the app to **Standard access** (needs a short video of the sign-in).
   Trial access allows 1,000 API calls a day.

### 3. Add it to your client

Replace the placeholders. Keep your other servers in each file. If the app ID and secret are
already in `~/.pinterest-ads-mcp/.env`, you can leave them out of the client config.

**Claude Code**

```bash
claude mcp add --scope user pinterest-ads \
  --env PINTEREST_ADS_APP_ID=your-app-id \
  --env PINTEREST_ADS_APP_SECRET=your-app-secret \
  --env PINTEREST_ADS_AD_ACCOUNT_ID=123456789 \
  --env PINTEREST_ADS_DRY_RUN=true \
  -- uvx --from git+https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp.git pinterest-ads-mcp
```

**Claude Desktop, Cursor, Gemini CLI, Windsurf** (and VS Code under `servers`)

```json
{
  "mcpServers": {
    "pinterest-ads": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp.git", "pinterest-ads-mcp"],
      "env": {
        "PINTEREST_ADS_APP_ID": "your-app-id",
        "PINTEREST_ADS_APP_SECRET": "your-app-secret",
        "PINTEREST_ADS_AD_ACCOUNT_ID": "123456789",
        "PINTEREST_ADS_DRY_RUN": "true"
      }
    }
  }
}
```

Desktop apps often can't find `uvx` on the PATH; if the server doesn't start, use the full
path from `which uvx` as the `command`.

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.pinterest-ads]
command = "uvx"
args = ["--from", "git+https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp.git", "pinterest-ads-mcp"]
env = { PINTEREST_ADS_APP_ID = "your-app-id", PINTEREST_ADS_APP_SECRET = "your-app-secret", PINTEREST_ADS_AD_ACCOUNT_ID = "123456789", PINTEREST_ADS_DRY_RUN = "true" }
```

The refresh token stays in its file; the client config only needs the app ID and secret. If
you keep the token file somewhere else, add `PINTEREST_ADS_TOKEN_FILE=/abs/path/token.json`.

### 4. Check it works

Restart or reload the client, then ask: "Which Pinterest ad accounts can you see?" It should
call `pinterest_ads_health_check` and list them with their currency. Remove
`PINTEREST_ADS_DRY_RUN` when you're ready for changes to go live.

## Configuration

| Variable | Required | What it does |
|---|---|---|
| `PINTEREST_ADS_APP_ID`, `PINTEREST_ADS_APP_SECRET` | yes | Your Pinterest app's ID and secret |
| `PINTEREST_ADS_TOKEN_FILE` | no | Refresh-token file written by `auth` (default `~/.pinterest-ads-mcp/secrets/pinterest-ads-token.json`) |
| `PINTEREST_ADS_REFRESH_TOKEN` | no | The refresh token itself, instead of the file (the file is better: renewed tokens are saved there) |
| `PINTEREST_ADS_ACCESS_TOKEN` | no | A fixed access token (e.g. a test or Sandbox token) instead of OAuth; can't be refreshed |
| `PINTEREST_ADS_AD_ACCOUNT_ID` | no | Default ad account |
| `PINTEREST_ADS_SANDBOX` | no | `true` uses Pinterest's Sandbox (`api-sandbox.pinterest.com`) |
| `PINTEREST_ADS_DRY_RUN` | no | `true` previews writes without sending them |
| `PINTEREST_ADS_READ_ONLY` | no | `true` registers only read-only tools |
| `PINTEREST_ADS_TOOL_GROUPS` | no | Comma-separated groups: `accounts`, `campaigns`, `ad_groups`, `ads`, `pins`, `keywords`, `targeting`, `conversions`, `catalogs`, `analytics`, `reporting`, `bulk`, `request`. Unknown names stop the server at startup. Server tools are always on |
| `PINTEREST_ADS_BULK_THRESHOLD` | no | Items per write before `confirm="APPLY"` is needed (default 10) |
| `PINTEREST_ADS_MAX_INCREASE_PCT` | no | Bid or budget rise that needs `confirm="APPLY"` (default 20) |
| `PINTEREST_ADS_MAX_BUDGET`, `PINTEREST_ADS_MAX_BID` | no | Optional caps in the account currency (not micro-units) |
| `PINTEREST_ADS_AUDIT_LOG` | no | Audit log path (default `~/.pinterest-ads-mcp/logs/audit.jsonl`) |
| `PINTEREST_ADS_AUDIT_RETENTION_DAYS` | no | Days to keep audit entries, pruned at startup (default 30; 0 keeps them) |
| `PINTEREST_ADS_AUDIT_SNAPSHOTS` | no | `false` stops keeping the old values of changed fields |
| `PINTEREST_ADS_MAX_DOWNLOAD_MB` | no | Largest report or export file to download (default 200) |
| `PINTEREST_ADS_PAGE_SIZE`, `PINTEREST_ADS_TIMEOUT` | no | List page size (100, max 250) and request timeout in seconds (30) |
| `PINTEREST_ADS_TRANSPORT`, `PINTEREST_ADS_HOST`, `PINTEREST_ADS_PORT` | no | `stdio` (default), `streamable-http` (served at `/mcp`) or `sse` (deprecated), and the bind address (default `127.0.0.1:8000`) |
| `PINTEREST_ADS_ALLOW_REMOTE` | no | `true` (or `--allow-remote`) allows binding HTTP to a non-local address. There's no sign-in: only behind your own authenticating proxy |
| `PINTEREST_ADS_HOME` | no | Per-user folder for `secrets/`, `logs/` and an optional `.env` (default `~/.pinterest-ads-mcp`) |
| `PINTEREST_ADS_LOG_LEVEL` | no | `DEBUG`, `INFO`, `WARNING` or `ERROR` (logs go to stderr) |

Settings are read from the environment, then `--env FILE` (or `./.env`), then
`~/.pinterest-ads-mcp/.env`. CLI: `pinterest-ads-mcp run` (default), `auth`, `purge`,
`describe`, `version`.

## Example prompts

```text
Which Pinterest campaigns spent the most last week, and what were their CTR and checkout ROAS?
```

```text
Pause every ad group in the "Spring sale" campaign with no conversions in the last 14 days. Dry-run first and show me the list.
```

```text
Create a SALES campaign with a 20 per day budget, one ad group targeting the UK, and an ad from my latest promoted Pin. Keep everything paused.
```

```text
Run a daily report of spend and clicks by campaign for October and tell me which days spend dipped.
```

## More docs

- [All tools and confirm words](docs/tools.md)
- [Getting Pinterest API access](docs/setup-pinterest-ads-api.md): the app, review, Standard access, tokens and IDs
- [Terms and data handling](docs/terms-and-data.md): what Pinterest's terms allow and require, AI clients, agencies
- [Agent skill](skills/pinterest-ads-mcp/SKILL.md): setup and usage guide for agents
- [Changelog](CHANGELOG.md) and [security policy](SECURITY.md)

## Contributing and licence

Issues and pull requests are welcome on [this repository](https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp). See
[CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under the [MIT License](LICENSE).

---

Try out [AI Shopping Feeds](https://aishoppingfeeds.com/), our shopping feed management tool (including Google Shopping feeds).
