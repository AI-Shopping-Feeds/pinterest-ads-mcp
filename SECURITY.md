# Security

This server can create, change, pause and archive Pinterest campaigns, ad groups, ads,
keywords, audiences, customer lists and catalog items, and so can spend real money, using
the OAuth permissions a Pinterest user granted to your own Pinterest app.

## Protect your credentials

- Keep the app secret and tokens out of git, chat and screenshots. `.env`, `secrets/` and
  `*token*.json` are gitignored.
- `pinterest-ads-mcp auth` saves the refresh token to
  `~/.pinterest-ads-mcp/secrets/pinterest-ads-token.json` with owner-only permissions (file
  0600, folder 0700). The audit log in `~/.pinterest-ads-mcp/logs/` is owner-only too.
- Tokens and the app secret are redacted from logs, error messages and the audit log.
- Pinterest scans public GitHub repositories for leaked tokens and revokes them. If a token or
  the app secret leaks anyway:
  1. Reset the app secret at developers.pinterest.com (My apps, Manage, Configure, Reset app
     secret). Existing access tokens keep working until they expire, so also do step 2.
  2. Remove the app's access from the Pinterest account (Settings, Security and logins, Apps)
     and run `pinterest-ads-mcp auth` again.
  3. Run `pinterest-ads-mcp purge` on any machine you no longer trust.
- Use a dedicated Pinterest app for this server, and request only the scopes you need
  (`pinterest-ads-mcp auth --scopes`).
- Start with `PINTEREST_ADS_DRY_RUN=true`, and use `PINTEREST_ADS_READ_ONLY=true` for
  reporting-only setups.

## Never expose the HTTP transport

The `streamable-http` and `sse` transports have no sign-in. Anyone who can reach them can use
your ad accounts. The server refuses to bind to anything other than `127.0.0.1`, `localhost`
or `::1` unless you set `PINTEREST_ADS_ALLOW_REMOTE=true` (or pass `--allow-remote`). Only do
that behind your own authenticating proxy on a private network.

## Reporting a vulnerability or misuse

Report security problems privately through a
[GitHub private security advisory](https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp/security/advisories/new)
on this repository. Don't open a public issue, and don't include working exploits, tokens or
real account data.

Use the same private channel to report suspected misuse of Pinterest data through this
project, such as data leaking from the audit log or being sent somewhere it shouldn't. If
Pinterest data that includes personal data may have been exposed, the Pinterest Developer and
API Terms require the developer who holds it to notify Pinterest promptly (within 72 hours
for data from the EEA, Switzerland or the UK). If you run this server with your own app, that
is you.

This project is not affiliated with or endorsed by Pinterest. For problems with the Pinterest
API itself, contact Pinterest through its Help Center.
