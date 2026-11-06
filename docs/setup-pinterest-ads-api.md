# Getting Pinterest API access

A step-by-step guide for people who haven't used the Pinterest API before. You'll create a
Pinterest app, get it approved, connect it to your ad account and find the IDs the server
needs. Checked against Pinterest's developer docs in November 2026; Pinterest changes its
screens from time to time, so the names of buttons may differ slightly.

**What you need:** a Pinterest **business** account that owns an ad account (or has been
given access to one through Business Manager), a privacy policy page on a website you
control, and about 30 minutes of hands-on time. Then you wait for Pinterest's review.

**How long it takes:** Pinterest doesn't publish review times. Trial access is reviewed
before you can see your app secret, and you get an email with the decision. The Standard
access upgrade is reviewed separately ("reviewed regularly", per Pinterest). Pinterest's
docs ask you to allow a few days before chasing a request. Plan for a few days for each
step, longer if your app is turned down and you appeal.

## 1. Use a business account

1. Sign in to [pinterest.com](https://www.pinterest.com) with the business account that runs
   your ads. If you only have a personal account, convert it or create a business account
   (Pinterest Help: "Create a business account").
2. Verify the account's email address.
3. For a company, use an account several people can reach. The app, its secret and the
   approval are tied to it.

## 2. Create the app (Trial access)

1. Go to [developers.pinterest.com](https://developers.pinterest.com) and open
   [**My apps**](https://developers.pinterest.com/apps/).
2. Accept the Developer Terms of Service when asked. Read them first:
   [docs/terms-and-data.md](terms-and-data.md) summarises what they mean for this server.
3. Choose **Connect app** and fill in the request form:
   - **App name:** at least three Latin letters or digits, and it **must not contain the word
     "Pinterest"**. Something like "Example Shop Ads Assistant" works.
   - **Description:** say plainly what it does, for example: "A private tool our marketing
     team runs on their own computers to report on and manage our own Pinterest ad
     campaigns through the API. Changes are reviewed and approved by a person before they
     are sent." Vague descriptions are a common reason for refusal.
   - **Privacy policy URL:** a public page on your company's own domain. Pinterest refuses
     apps whose policy link doesn't load or isn't clearly yours.
4. Submit. Pinterest reviews the request and emails you. Until then you can't see the app
   secret.

Each Pinterest user can have up to five apps; ask Pinterest Support to delete one you don't
need.

## 3. Add the redirect URI

When the app is approved:

1. In **My apps**, choose **Manage** on the app, then the **Configure** tab.
2. Under **Redirect URIs**, add exactly this and save:

   ```text
   http://localhost:8765/callback
   ```

   It must match character for character, including the port and path. If you use another
   port, run `pinterest-ads-mcp auth --port <port>` and register that address instead.
3. On the same tab, note the **App id** and reveal the **App secret key**. Keep the secret in
   a password manager. Never paste it into chat or commit it.

## 4. Sign in once with the server

Put the two values where the server can read them, either in your MCP client's config (see
the README) or in `~/.pinterest-ads-mcp/.env`:

```text
PINTEREST_ADS_APP_ID=<your app id>
PINTEREST_ADS_APP_SECRET=<your app secret>
```

Then run, in your own terminal:

```shell
uvx --from git+https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp.git pinterest-ads-mcp auth
```

Your browser opens Pinterest's consent page. Sign in as the business account and choose
**Give access**. The server saves a refresh token to
`~/.pinterest-ads-mcp/secrets/pinterest-ads-token.json` (owner-only) and never prints it.

- If the browser can't reach `localhost` (for example on a remote machine), use
  `pinterest-ads-mcp auth --manual` and paste the address you land on. It must still contain
  the same `state` value, which the command checks.
- By default it asks for these scopes: `ads:read`, `ads:write`, `boards:read`, `pins:read`,
  `catalogs:read`, `catalogs:write`, `user_accounts:read` and `biz_access:read`. Ask for fewer
  with `--scopes ads:read,user_accounts:read` (reporting only). Add `pins:write` if you want
  `pinterest_ads_create_ad_pin`.
- Tokens: the access token lasts 30 days and stays in memory. The refresh token lasts 60 days
  and is renewed every time the server refreshes, so it keeps working as long as you use the
  server at least every couple of months. If it expires, run `auth` again.

## 5. Find your IDs

- **Ad account ID:** ask your agent "Which Pinterest ad accounts can you see?" (it calls
  `pinterest_ads_list_ad_accounts`), or in Pinterest open your name menu, **Businesses/Assets**,
  **Ad accounts**, and note the number next to **ID:**. Set it as
  `PINTEREST_ADS_AD_ACCOUNT_ID` so tools use it by default.
- **Business ID** (only for business access tools): your name menu, **Businesses/Assets**, the
  18-digit number under your business name, or `pinterest_ads_list_businesses`.

## 6. What Trial access allows

Trial access is enough to try everything with your own account:

- Reading and creating ads, audiences and catalogs works.
- **Rate limits are low:** 1,000 calls a day for the whole app, and per category (for example
  1,000 a day for ads reads and analytics, 300 a day for ads writes). A few busy sessions can
  use that up.
- Pins and boards created with Trial access are visible only to you (Sandbox entities), so
  `pinterest_ads_create_ad_pin` makes Pins that can't run as real ads yet.

To try writes with no risk at all, use Pinterest's Sandbox: generate a Sandbox token on the
app's **Configure** tab (**Generate access token**) and set `PINTEREST_ADS_SANDBOX=true` with
`PINTEREST_ADS_ACCESS_TOKEN=<sandbox token>`. A Sandbox token from that page lasts 30 days;
for longer testing, run `pinterest-ads-mcp auth` with `PINTEREST_ADS_SANDBOX=true` instead.

## 7. Upgrade to Standard access (for real use)

Standard access raises the limits to per-minute, per-user quotas (for example 1,000 ads reads
and 400 ads writes a minute, 300 analytics calls a minute) and lets Pins you create be public.

1. Make sure the app complies with the Developer Guidelines.
2. Record a short screen video of the app **completing the OAuth sign-in** and then making an
   API call. Pinterest requires this even if you are the only user; a terminal recording of
   `pinterest-ads-mcp auth` followed by your agent listing your ad accounts is the kind of
   thing they accept. Show the real Pinterest consent screen, not wireframes, and don't show
   your app secret or tokens.
3. In **My apps**, choose **Upgrade** on the app card (it only appears for eligible apps),
   check your details and privacy policy link, upload the video and submit.
4. Pinterest emails the decision. Common reasons for refusal: the video doesn't show the OAuth
   flow, or the app collects passwords or session cookies instead of using OAuth.

If you are refused, you can appeal through the Pinterest Help Center (search "API",
**API Technical Support**).

## Apps registered after 14 September 2026

New apps may get `HTTP 403 PINNER_DATA_ACCESS_DENIED` when reading a board or Pin they don't
own without extra approval. Reading your own business account's Pins works; for others,
Pinterest asks you to contact your Pinterest account manager. Don't retry these calls in a
loop.

## Agencies and several advertisers

- Use your agency's own app and sign in with your agency's business account. Clients give that
  account access to their ad accounts through Business Manager; shared ad accounts then appear
  in `pinterest_ads_list_ad_accounts`.
- Don't ask clients for their passwords or share your app secret with them.
- Read the agency section of [docs/terms-and-data.md](terms-and-data.md) before using the
  server for client accounts.

## Troubleshooting

- **"redirect_uri mismatch" or the consent page shows an error:** the redirect URI isn't
  registered exactly. Check the port and the `/callback` path.
- **`Authentication failed` (code 2) or HTTP 401:** the token expired or was revoked (for
  example after a password change). Run `pinterest-ads-mcp auth` again.
- **"Your token does not have sufficient permissions":** the token lacks a scope. Run
  `auth --scopes ...` with the scopes you need. `pinterest_ads_health_check` lists the
  granted scopes.
- **HTTP 429:** the rate limit is used up. The server waits and retries a few times; on Trial
  access the daily limit resets the next day.
- **No ad accounts listed:** the signed-in account doesn't own an ad account and hasn't been
  given access to one in Business Manager.
