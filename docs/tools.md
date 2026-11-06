# Tools

**81 tools**, all prefixed `pinterest_ads_`, in 13 groups plus 5 server tools that are
always on. They cover the Pinterest API v5 (REST API 5.31 at the time of writing).

## Conventions

- **Ad account.** Most tools take `ad_account_id`; without it they use
  `PINTEREST_ADS_AD_ACCOUNT_ID`. `pinterest_ads_list_ad_accounts` lists the ones the token
  can use, including accounts shared through business access.
- **Money** is in micro-units of the ad account's currency: 1.00 = `1000000`. That applies
  to `daily_spend_cap`, `lifetime_spend_cap`, `budget_in_micro_currency` and
  `bid_in_micro_currency`. The bulk adjust tools take plain currency amounts instead
  (`set_bid: 0.85`). Caps (`PINTEREST_ADS_MAX_BUDGET`, `PINTEREST_ADS_MAX_BID`) are plain
  currency amounts too.
- **New things start paused.** Campaigns, ad groups, ads and product group promotions created
  without a `status` are created `PAUSED`. Turn them on with `pinterest_ads_set_statuses`.
- **Archiving is final.** Pinterest has no delete for campaigns, ad groups and ads, and an
  archived one can't be turned back on.
- **Kind** is the tool's MCP annotation: `read` changes nothing in Pinterest; `write` changes
  the account; `destructive` can archive, delete or remove; `local` only touches this server.
- **Confirm** lists the confirm word a call may need. Without it the tool sends nothing and
  replies with the reasons and the old and new values. In dry-run nothing is sent, so no
  confirm is needed, and the reply says which word a real run would need. The "batch limit" is
  `PINTEREST_ADS_BULK_THRESHOLD` (10 items) and "rises" means bid or budget increases above
  `PINTEREST_ADS_MAX_INCREASE_PCT` (20%), or increases whose current value couldn't be read.
- **Batches.** Create and update tools take up to 500 items and send them 30 at a time
  (Pinterest's limit), with progress notifications. ID filters take up to 250 IDs.
- **Groups.** `PINTEREST_ADS_TOOL_GROUPS` takes the group names below (the headings);
  `PINTEREST_ADS_READ_ONLY=true` keeps only `read` and `local` tools.

## server (always on)

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_describe_server` | Show which API host, default ad account and safety settings this server uses. | local |  |
| `pinterest_ads_health_check` | Check the setup: refresh the access token, read the signed-in account, then list the ad accounts it can use. | read |  |
| `pinterest_ads_audit_lookup` | Look up an audit log entry by ID: the request, response summary and old values of a write. | local |  |
| `pinterest_ads_audit_tail` | Show the most recent audit log entries (writes and dry-run rehearsals). | local |  |
| `pinterest_ads_set_dry_run` | Turn dry-run on or off for this session. | local |  |

## accounts

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_list_ad_accounts` | List the ad accounts this token can use: owned ones and, by default, ones shared through business access (agency and partner access). | read |  |
| `pinterest_ads_get_ad_account` | Get one ad account: name, currency, country, time zone, owner and permissions. | read |  |
| `pinterest_ads_get_user_account` | Get the Pinterest account behind the token (username, account type, business name). | read |  |
| `pinterest_ads_list_businesses` | List the businesses the signed-in user belongs to (Business Manager), with their IDs and a summary of their assets. | read |  |
| `pinterest_ads_list_business_assets` | List a business's assets: ad accounts (default), profiles, catalogs or asset groups, with the permissions the user holds on each. | read |  |

## campaigns

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_list_campaigns` | List campaigns in an ad account. | read |  |
| `pinterest_ads_create_campaigns` | Create campaigns (up to 500 per call, sent 30 at a time). | write | `APPLY` over caps or the batch limit |
| `pinterest_ads_update_campaigns` | Update campaigns (up to 500 per call). | write | `APPLY` for rises or big batches; `ARCHIVE` to archive |
| `pinterest_ads_archive_campaigns` | Archive campaigns by ID. | destructive | `ARCHIVE` |

## ad_groups

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_list_ad_groups` | List ad groups in an ad account. | read |  |
| `pinterest_ads_create_ad_groups` | Create ad groups (up to 500 per call, sent 30 at a time). | write | `APPLY` over caps or the batch limit |
| `pinterest_ads_update_ad_groups` | Update ad groups (up to 500 per call). | write | `APPLY` for rises or big batches; `ARCHIVE` to archive |
| `pinterest_ads_archive_ad_groups` | Archive ad groups by ID. | destructive | `ARCHIVE` |
| `pinterest_ads_get_bid_floors` | Get the minimum bids Pinterest accepts for ad groups. | read |  |

## ads

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_list_ads` | List ads in an ad account. | read |  |
| `pinterest_ads_create_ads` | Create ads (up to 500 per call, sent 30 at a time). | write | `APPLY` over caps or the batch limit |
| `pinterest_ads_update_ads` | Update ads (up to 500 per call). | write | `APPLY` for rises or big batches; `ARCHIVE` to archive |
| `pinterest_ads_archive_ads` | Archive ads by ID. | destructive | `ARCHIVE` |
| `pinterest_ads_list_product_group_promotions` | List product group promotions: the catalog product groups an ad group promotes. | read |  |
| `pinterest_ads_create_product_group_promotions` | Promote catalog product groups in an ad group (catalog ads). | write | `APPLY` over caps or the batch limit |
| `pinterest_ads_update_product_group_promotions` | Update product group promotions in one ad group. | write | `APPLY` for rises or big batches; `ARCHIVE` to archive |

## pins

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_list_pins` | List Pins you could promote. | read |  |
| `pinterest_ads_get_pin` | Get one Pin: title, link, media, board and whether it has been promoted. | read |  |
| `pinterest_ads_list_boards` | List boards (yours, or the ad account owner's with ad_account_id), to find Pins. | read |  |
| `pinterest_ads_list_board_pins` | List the Pins on a board, for example the protected Ad-only Pins board. | read |  |
| `pinterest_ads_create_ad_pin` | Create an ad-only Pin from an image URL, to use as an ad's pin_id. | write |  |

## keywords

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_list_keywords` | List keywords and negative keywords for an ad group, several ad groups or a campaign. | read |  |
| `pinterest_ads_create_keywords` | Add targeting keywords to an ad group. | write | `APPLY` over the batch limit |
| `pinterest_ads_create_negative_keywords` | Add negative keywords so ads don't show on those searches. | write | `APPLY` over the batch limit |
| `pinterest_ads_archive_keywords` | Archive keywords or negative keywords by ID (find IDs with pinterest_ads_list_keywords). | destructive | `ARCHIVE` |
| `pinterest_ads_get_keyword_metrics` | Monthly search volume on Pinterest for keywords in one country. | read |  |
| `pinterest_ads_suggest_keywords` | Keyword ideas for a seed term: popular suggested terms and related terms. | read |  |

## targeting

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_get_targeting_options` | Valid values for an ad group targeting_spec field. | read |  |
| `pinterest_ads_list_targeting_templates` | List saved targeting templates (reusable targeting for ad groups). | read |  |
| `pinterest_ads_estimate_audience_size` | Estimate how many people an ad group's targeting would reach. | read |  |
| `pinterest_ads_list_audiences` | List audiences (customer list, visitor, engagement, actalike and persona audiences). | read |  |
| `pinterest_ads_get_audience` | Get one audience: type, rule, size and status. | read |  |
| `pinterest_ads_create_audience` | Create an audience for targeting. | write |  |
| `pinterest_ads_update_audience` | Rename an audience or change its description or rule. | write |  |
| `pinterest_ads_delete_audience` | Remove an audience. | destructive | `DELETE` |
| `pinterest_ads_list_customer_lists` | List customer lists (uploaded customer identifiers) with their size and status. | read |  |
| `pinterest_ads_create_customer_list` | Upload a customer list (emails or mobile ad IDs) for targeting. | write |  |
| `pinterest_ads_update_customer_list` | Add people to, or remove people from, a customer list. | destructive |  |

## conversions

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_list_conversion_tags` | List the ad account's conversion tags (Pinterest Tag), with their IDs and status. | read |  |
| `pinterest_ads_get_conversion_tag` | Get one conversion tag: name, status, last fired time and configuration. | read |  |

## catalogs

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_list_catalogs` | List catalogs (retail, hotel or creative assets) and their IDs. | read |  |
| `pinterest_ads_list_catalog_feeds` | List product feeds (data sources): location, format, schedule, status, country. | read |  |
| `pinterest_ads_list_feed_processing_results` | Recent processing runs of a feed: status, item counts, and error and warning totals. | read |  |
| `pinterest_ads_list_item_issues` | Item-level errors and warnings from one feed processing run. | read |  |
| `pinterest_ads_get_catalog_items` | Look up catalog items by item ID (your feed's id column). | read |  |
| `pinterest_ads_ingest_catalog_feed` | Ask Pinterest to fetch and process a feed now instead of waiting for its schedule. | write |  |
| `pinterest_ads_batch_catalog_items` | Create, update, upsert or delete catalog items in one batch (up to 1000). | destructive | `DELETE` for DELETE; `APPLY` over the batch limit |
| `pinterest_ads_get_catalog_items_batch` | Status of an item batch: whether it finished, and any per-item errors. | read |  |
| `pinterest_ads_list_product_groups` | List catalog product groups (the sets of products catalog ads promote). | read |  |
| `pinterest_ads_create_product_group` | Create a catalog product group. | write |  |
| `pinterest_ads_update_product_group` | Rename a product group or change its description, filters or featured flag. | write |  |
| `pinterest_ads_delete_product_group` | Delete a catalog product group. | destructive | `DELETE` |
| `pinterest_ads_get_product_group_product_counts` | How many products a product group contains. | read |  |
| `pinterest_ads_list_product_group_products` | List the products (product Pins) in a product group. | read |  |

## analytics

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_get_analytics` | Get ads metrics right away (synchronous) for the ad account, or for up to 250 campaigns, ad groups, ads or product groups. | read |  |
| `pinterest_ads_list_metrics` | Definitions of the metrics (columns) reports can return, with Ads Manager names. | read |  |
| `pinterest_ads_get_metrics_ready_state` | Whether conversion and other metrics for a date (YYYY-MM-DD) are final yet. | read |  |
| `pinterest_ads_get_audience_insights` | Interests and demographics of the account's audience over the last 30 days. | read |  |

## reporting

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_create_report` | Request an ads report. | read |  |
| `pinterest_ads_get_report` | Check a report's status: IN_PROGRESS, FINISHED, FAILED, CANCELLED or EXPIRED. | read |  |
| `pinterest_ads_download_report` | Download a FINISHED report and return its rows (up to max_rows), held in memory only. | read |  |
| `pinterest_ads_run_report` | Request a report, wait for it with progress updates, and return its rows. | read |  |
| `pinterest_ads_list_report_templates` | List custom report templates saved in Ads Manager, to re-run them by template_id. | read |  |

## bulk

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_set_statuses` | Pause or activate many campaigns, ad groups or ads at once, with progress. | write | `APPLY` over the batch limit |
| `pinterest_ads_adjust_ad_group_bids` | Change many ad group bids at once, with progress. | write | `APPLY` for rises or big batches |
| `pinterest_ads_adjust_budgets` | Change many campaign or ad group budgets at once, with progress. | write | `APPLY` for rises, caps or big batches |
| `pinterest_ads_bulk_upsert` | Create and/or update many ad entities in one asynchronous Pinterest bulk job. | destructive | always `APPLY` (`ARCHIVE` if it archives) |
| `pinterest_ads_get_bulk_request` | Status of a bulk upsert or bulk export: RUNNING, SUCCEEDED or FAILED. | read |  |
| `pinterest_ads_bulk_export` | Export ad entities in bulk (one background job), wait with progress, return rows. | read |  |

## request

| Tool | What it does | Kind | Confirm |
|---|---|---|---|
| `pinterest_ads_read_request` | Read from any Pinterest API v5 endpoint the other tools don't cover. | read |  |
| `pinterest_ads_request` | Call any Pinterest API v5 endpoint the other tools don't cover (lead forms, labels, schedules, ad previews...). | destructive | `APPLY` for writes |

## Retired or retiring API features this server avoids

- `CATALOG_SALES` and `WEB_CONVERSION` objectives: no new API campaigns from 1 February 2027,
  archived on 30 April 2027. The campaign tools refuse them; use `SALES`, `LEADS` or
  `CONSIDERATION` with `intended_promotion_type`. `VIDEO_VIEW` and `WEB_SESSIONS` are
  refused too.
- Legacy (365-day) refresh tokens: `auth` always asks for a continuous refresh token.
- Keyword bids (ignored by Pinterest), the custom audience endpoint, the followed-interests
  endpoint and `GET` catalog item lookups are not used.

## Left to the escape hatch

`pinterest_ads_read_request` and `pinterest_ads_request` reach any other v5 endpoint, for
example: lead forms and lead exports, ad previews, labels, schedules, media library assets,
agency-owned ad account requests, business access invites and members, audience sharing,
customer segments, targeting template writes, conversion events (Conversions API), conversion
deletion requests, advertiser-defined events, Media Planner delivery estimates, MMM and
brand/category/SKU reports, catalog reports, local inventory, billing and order lines, and
organic Pin, board and user analytics.
