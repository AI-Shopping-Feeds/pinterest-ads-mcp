# Terms and data handling

This page summarises what Pinterest's terms mean for this server, how the server is built to
fit them, and what stays your responsibility. It is a reading aid, not legal advice: the
documents linked below are what bind you, and they change. Checked in November 2026 against
the Developer and API Terms dated August 2026.

This project is not affiliated with or endorsed by Pinterest. "Pinterest" is used only to
say which API the server works with. No Pinterest logos are used.

## The documents that apply

You accept these when you create your own Pinterest app, which you need to use this server:

- [Developer and API Terms of Service](https://developers.pinterest.com/terms/) (the Terms).
- [Developer Guidelines](https://policy.pinterest.com/developer-guidelines) (Exhibit A to the
  Terms).
- For ads data: the [Ad Data Terms](https://policy.pinterest.com/ad-data-terms), the
  [Advertising Guidelines](https://policy.pinterest.com/advertising-guidelines) and your
  Pinterest Advertising Services Agreement.
- [Brand guidelines](https://business.pinterest.com/brand-guidelines) and the Business Terms
  of Service.

## What the terms allow

- **Advertising tools are an accepted use.** The Guidelines list "Advertising tools, like apps
  for managing campaigns across multiple channels".
- **Bring-your-own-key tools are allowed** if the app "stores the end user's API credentials
  locally (not server-side)". This server runs on your machine with your own app and stores
  the refresh token only in your home folder.
- **MCP servers are explicitly covered.** The Terms define "Intermediary Tools" to include
  "Model Context Protocol (MCP) servers", agents and connectors, and apply the Terms to them
  in full. Pinterest is also building its own MCP server (see below).

## What they require, and how this server handles it

| Requirement (Terms or Guidelines) | How this server handles it | Your part |
|---|---|---|
| Your app is reviewed and approved before use; changes in use need re-review. | Each user registers and gets approval for their own app. | Describe your use honestly in the app request; ask Pinterest again if you change how you use it. |
| Don't store data from the API, except campaign analytics about accounts you may access ("you may not store any information accessed through the Materials"). | Reports and read results stay in memory. The audit log keeps your own write requests, a compact response summary (IDs, statuses, errors) and only the old values of the fields a write changed. Customer-list identifiers never reach it. `PINTEREST_ADS_AUDIT_SNAPSHOTS=false` drops the old values too. | See "Open questions" below. Don't save report output elsewhere unless it is campaign analytics you may keep. |
| Delete Pinterest data within 30 days of it no longer being needed, on request, or when a client leaves. | Audit entries are pruned after `PINTEREST_ADS_AUDIT_RETENTION_DAYS` (30). `pinterest-ads-mcp purge` deletes the audit log and token file. | Run `purge` when you stop using the server or a client leaves; delete anything your AI client kept (see below). |
| Respect usage limits; don't try to get around them. | Honours `x-ratelimit-*` headers and `Retry-After`, backs off on 429 and 5xx, and pauses when the window is used up. No parallel request fan-out. | Don't run many copies against one app to multiply the limit. |
| Use industry-standard security; keep credentials private; report breaches of personal data promptly (72 hours for EEA, Swiss or UK data). | Token file 0600 in a 0700 folder; secrets redacted from logs, errors and the audit log; HTTP transport refused on non-local addresses unless you opt in. | Keep your machine and app secret safe; follow [SECURITY.md](../SECURITY.md). |
| Act on people's accounts only with authorisation, and take actions only with their "specific knowledge and consent"; no features that act "without specifically considering each action". | OAuth only, never passwords. Writes can be rehearsed in dry-run; archives, deletes, big batches and bid or budget rises need a confirm word; new campaigns, ad groups and ads start paused. The server tells the agent to show each change and get approval. | Don't run the agent unattended on write tools. Approve each change yourself. |
| "Don't combine someone's account information with information from other people's accounts", or with other services' data beyond what the service needs. | The server tells the agent not to mix accounts or sources beyond what you asked for. | Don't ask for cross-account or cross-client comparisons or pooled analysis. |
| Don't share or sell API data with third parties, including other advertising services. | Only Pinterest hosts are called: the API host and Pinterest's signed links for report files (served from Pinterest's storage on Amazon S3). No telemetry. The escape hatch can't reach other hosts and blocks OAuth endpoints. | Data you see goes to your AI provider; see below. |
| No using API data to "train, fine-tune, or otherwise improve or develop any artificial intelligence" model without written consent. | The server doesn't train anything. | Use an AI provider and plan whose terms say your inputs aren't used for training. |
| No platform insights, benchmarking or competitor research without written authorisation. | The server tells the agent not to make benchmark or competitor claims. | Don't ask for them. |
| Don't copy, modify or create derivative works of the API, or redistribute it. | No Pinterest specifications, documentation text, SDK or sample code are included; tool descriptions are our own words and link to Pinterest's docs. | Keep it that way in contributions ([CONTRIBUTING.md](../CONTRIBUTING.md)). |
| Ad Data Terms: only send data you have rights and consent for; no sensitive categories or children's data; don't use Pinterest ad data outside Pinterest; delete it within six months. | Customer-list identifiers are trimmed, lower-cased and SHA-256 hashed on your machine before sending, and kept out of the audit log. | Only upload people who agreed to this use. Don't upload third-party audience data about EEA, Swiss or UK residents without Pinterest's written consent. |

## Using it with AI clients such as Claude

Allowed in principle, with care:

- The Terms explicitly cover MCP servers and agents, so using the API through this server is a
  recognised pattern, and all the rules above apply to it.
- **Training:** Pinterest data must not train AI models. Use business or API terms where the
  provider doesn't train on your inputs, and check the setting on consumer plans.
- **Your AI client stores what it sees.** Chat histories, logs and memories in your AI client
  hold Pinterest data returned by the tools. That is storage the Terms restrict (only campaign
  analytics may be kept) and it counts towards the 30-day deletion duty. Prefer clients and
  settings with short or no retention, and delete conversations you no longer need.
- **Sharing:** sending data to your AI provider so it can answer you is how the tool works,
  but the Guidelines forbid sharing API data with third parties. Treat the AI provider as your
  processor under a data processing agreement, and don't use providers that resell or reuse
  inputs.
- **Each action needs a person's approval.** The confirm words and dry-run exist for this;
  don't set up agents that change ads without a person approving each change.

## Agencies using it for clients

Allowed, with extra duties:

- Use your agency's own app and business account; clients grant access through Business
  Manager (or agency-owned ad accounts). Never collect client passwords, and don't share your
  app credentials with clients.
- **Customers must agree to the Terms.** If you give a client access to the server or its
  output as part of your service, you must make sure they have agreed to Pinterest's terms
  ("Responsibility for Your Customers").
- **Keep clients apart.** Don't combine one client's data with another's (no cross-client
  benchmarks or pooled reports), and don't use one client's data for another.
- **Advertising service rules** (Guidelines, "Audience & advertiser services"): if you report
  last-click attribution, show multi-touch metrics next to it "with equal prominence"; show
  your fees separately from Pinterest's; use API data only to serve and evaluate ads on
  Pinterest.
- **Audience data:** upload customer lists only on the advertiser's instructions and with
  their consent basis; follow the data sharing terms in the Advertising Services Agreement.
- When a client leaves or revokes access, delete their data within 30 days (`purge`, plus
  anything your AI client kept).

## Making this repository public under a name that includes "Pinterest"

Things the maintainers should settle before publishing:

- **Brand guidelines:** for apps and services built to work with Pinterest, the guidelines say
  "Don't use 'Pin', 'Pinterest' or any variations" in the name or domain name, and to build
  your own branding. The project name, package, command and README title all use
  "Pinterest". Pinterest also refuses developer app names containing "Pinterest". Options:
  rename (for example a neutral name with "for Pinterest" only in the description), or get
  Pinterest's written permission.
- **Publicity:** the Terms forbid "any public statement related to the Materials" without
  advance written permission ("email is sufficient"), including promotional material. A
  public README and announcements about a Pinterest API tool may fall under this.
- **Intermediary Tools:** whoever distributes an Intermediary Tool is responsible for its
  compliance and must ensure third parties using it have agreed to terms "at least as
  protective" as Pinterest's. Every user needs their own approved app, which means accepting
  Pinterest's Terms directly; the README and skill say so. The MIT licence itself doesn't pass
  Pinterest's terms on.
- What helps: descriptive use of the name only, the "Not affiliated with or endorsed by
  Pinterest" line, no logos, no claims of partnership, and no Pinterest materials in the repo.

## Open questions

- **Before-snapshots.** A strict reading of the storage rule covers even the old values of a
  changed budget. They are kept so you can review or undo a change, are minimised and pruned
  after 30 days, and can be turned off with `PINTEREST_ADS_AUDIT_SNAPSHOTS=false`. If you want
  certainty, ask Pinterest or turn them off.
- **Report download host.** Pinterest returns signed links for report and bulk files without
  documenting their host. The server accepts HTTPS links on `*.pinterest.com`,
  `*.pinimg.com` and `*.amazonaws.com` only, never sends your token with them, and caps the
  download at `PINTEREST_ADS_MAX_DOWNLOAD_MB`.

## Pinterest's own MCP server

Pinterest has announced a [Pinterest MCP Server](https://developers.pinterest.com/about-pinterest-mcp/)
that isn't available yet (sign-up for updates only, November 2026). Its announcement shows a
hosted endpoint at `https://api.pinterest.com/mcp` used with a bearer token from your own
approved app, and describes ads data, analytics, audience and keyword insights, trends and,
where authorised, creating and updating assets, with no separate fee. Compared with it, this
server runs on your own machine, is available now, covers the ads API in depth including
writes, and adds dry-run, confirm words, spend guards, an audit log, read-only mode and tool
groups. When Pinterest's server launches, compare its terms, coverage and data retention
before choosing.
