# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-06

### Added

- First version: 81 tools (76 in 13 groups plus 5 server tools) for the Pinterest API v5,
  built on the same architecture as the other AI Shopping Feeds MCP servers (tool catalog with
  titles and annotations, tool groups, read-only mode, dry-run, JSONL audit log, stdio and
  streamable HTTP transports, progress notifications).
- OAuth 2.0: `pinterest-ads-mcp auth` runs the consent flow with a localhost callback (or
  `--manual` paste mode that checks `state`), asks for a continuous refresh token and saves it
  owner-only in `~/.pinterest-ads-mcp/secrets/`. Access tokens are cached in memory; renewed
  refresh tokens are written back. A static test token and Pinterest's Sandbox are supported.
- Ad accounts and business access, campaigns, ad groups (and bid floors), ads, product group
  promotions, the Pins and boards needed to build ads (including ad-only Pins), keywords and
  negative keywords with keyword research, targeting options and templates, audience sizing,
  audiences, customer lists (hashed locally), conversion tags, catalogs, feeds, items and
  product groups, synchronous analytics, async reports with a run-and-wait tool, bulk status,
  bid and budget changes, bulk upsert and export, and read/write escape hatches.
- Safety: new campaigns, ad groups, ads and product group promotions start paused; confirm
  words for archives (`ARCHIVE`), deletes (`DELETE`), and big batches, bid and budget rises,
  caps, bulk upserts and raw writes (`APPLY`); retiring campaign objectives are refused.
- Data handling: the audit log keeps write requests, a compact response summary and only the
  old values of changed fields; customer-list identifiers never reach it; entries are pruned
  after 30 days; `pinterest-ads-mcp purge` deletes local data. Reports are parsed in memory
  with a download cap. Only Pinterest hosts (and Pinterest's signed storage links) are called;
  rate-limit headers and `Retry-After` are honoured.
- Hardening: HTTP transports refuse non-loopback addresses unless
  `PINTEREST_ADS_ALLOW_REMOTE=true`; escape-hatch paths are validated and OAuth endpoints are
  blocked.
