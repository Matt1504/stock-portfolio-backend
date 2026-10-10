# Stock Portfolio Backend

## Docker stack

The [stock-portfolio-stack repository](https://github.com/Matt1504/stock-portfolio-stack) runs the frontend, this API, Redis and the hourly market-data worker together. The backend image uses Python 3.11 and Gunicorn; Graphene 2.1.9 / graphql-core 2.3.2 retain the existing GraphQL API while supporting the container runtime. The `/health` endpoint performs a read-only MongoDB ping. The stack's development configuration runs Flask with source hot reload.

Set `MONGODB_URI` and optionally `MONGODB_DATABASE` (default `stock_portfolio`) through the environment. Local launches still support `src/database/passwords.py` when no URI is provided, but that file is excluded from images. The Docker context also excludes `.env`, the local virtual environment, and investigation PDFs under `tmp`. No database setup or migrations run on container startup.

See the stack README for the complete setup. The Redis-only `compose.yaml` here remains available for existing local workflows. To run backend tests inside a container without connecting to Atlas:

```sh
docker build --target test -t stock-portfolio-backend-tests .
docker run --rm stock-portfolio-backend-tests
```

## Overview
This project is the code that runs the backend server for our Stock Portfolio application. It connects to a MongoDB database that stores our data. API requests are using GraphQL and handled with Graphene-Python. The web server application is hosted using Flask.

## Getting started
Set up Mongo DB [account](https://www.mongodb.com/cloud/atlas/register)

Set up your cluster

Create passwords.py in directory src/database/passwords.py and add your USER, PASSWORD, and CLUSTER variables in the following format
```python
USER = "USER_NAME"
PASSWORD = "<mongodb-password>"
CLUSTER = "CLUSTER_NAME"
```
Make sure you have your python virtual environment set up

Start your Virtual Environment
```bash
source src/bin/activate
```

Install the following libraries

```bash
brew tap mongodb/brew
brew install mongodb-community
pip install -r requirements.txt
```

Set up and Flask App
```bash
export FLASK_APP=app.py
```
## Redis caching

The first caching stage covers `accounts` (7 days) and
`transactionsByAccount` (1 day). Other GraphQL queries still read MongoDB.
MongoDB remains the source of truth; Redis is optional and disposable.

From the backend repository root, activate your existing virtual environment
and install the updated requirements. If Docker Compose is installed, you can
start the supplied local Redis service:

```bash
source src/bin/activate
pip install -r requirements.txt
docker compose up -d redis
export REDIS_URL=redis://127.0.0.1:6379/0
export APP_ENV=development
cd src
python3 app.py
```

An existing Redis service can be used instead by setting `REDIS_URL` to its
connection URL (`rediss://` for TLS). The local Compose service is bound to
loopback, limited to 128 MB, and does not persist cache entries across restarts.
No MongoDB container or database reset is involved.

Configuration is read from environment variables at process startup. See
`.env.example` for all options; the app does **not** automatically load that file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `REDIS_URL` | unset | Redis connection URL; caching is disabled without it |
| `CACHE_ENABLED` | true when a URL is set | Set to `false` to bypass caching |
| `APP_ENV` | `development` | Separates development/test/production cache keys |
| `CACHE_NAMESPACE` | `stock-portfolio` | Use a unique value for each MongoDB database/deployment |
| `CACHE_REFERENCE_TTL_SECONDS` | `604800` | Account cache lifetime (7 days) |
| `CACHE_TRANSACTION_TTL_SECONDS` | `86400` | Transaction cache lifetime (1 day) |
| `CACHE_REDIS_TIMEOUT_SECONDS` | `0.25` | Redis connection and command timeout |

On a miss, the backend reads MongoDB and stores BSON JSON document snapshots.
Nested references are materialized so a cache hit does not trigger lazy MongoDB
reads for stock, platform, account, activity, or currency details. Account filter
arguments have separate keys; Relay pagination is applied to the cached filtered
dataset, preserving `edges`, cursors, and `pageInfo`. Transaction keys include
the account ID. Empty results are cached too.

Successful account mutations invalidate accounts and transaction snapshots.
Transaction, stock, and platform mutations invalidate transaction snapshots.
Transfers also invalidate on partial failure. Invalidation changes namespace
generation tokens, making every previous query variant unreachable; those
entries expire normally. An overlapping read can only populate its old generation.

Redis errors fall back to MongoDB. If invalidation fails, the writing process
remembers it and retries before serving cached reads after Redis recovers.
This pending state is process-local: a restart or other workers can still serve
older entries until their TTL expires if invalidation was missed. The initial
implementation targets the existing single-process Flask app; reliable
invalidation across worker restarts would need a durable event/outbox mechanism.

Writes made directly in MongoDB or through scripts bypass mutation invalidation.
After manual MongoDB edits, use Refresh to reload the displayed queries from MongoDB and replace their cache entries. To retire every cached query variant, stop the app, rotate `CACHE_NAMESPACE`, and restart; old entries expire naturally.
Do not run `database_init.py` as a cache setup step: it deletes the MongoDB database.

For diagnostics, enable DEBUG logging for `cache.backend` to see cache hits and
misses. Redis failures are logged at WARNING without connection credentials.
The frontend uses Apollo caching for normal page loads. Refresh buttons issue fresh network requests with `X-Cache-Bypass: true`; cached backend queries read MongoDB and replace matching Redis entries.

### Cache tests

```bash
pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -v
```

The tests use fakeredis and mongomock, never production MongoDB credentials or
a live Redis service. They check TTLs, filters, pagination, nested references,
mutation invalidation, overlapping reads, and Redis failure/recovery.

## Profiles

Profiles represent the people whose investments you track, rather than login
accounts. `profiles` stores a required name and MongoDB `_id`. Platforms and
contribution limits have a required `profile` reference. Transactions inherit
ownership from their platform; stocks, account types, currencies and activities
remain shared metadata. A new profile starts with no platforms or transactions.

All personal GraphQL queries and mutations require `profileId`. This includes
platform/transaction/contribution-limit connections and filtered transaction
queries. Backend checks prevent editing another profile's transaction or moving
it to that profile's platform. Transfers must remain within one profile and use
the same account type and currency. Stock statistics come from only the selected
profile's transactions. Profiles organize records; they do not add authentication
or restrict which tracked person the application's operator can select.

Account cache entries remain shared. Transaction cache keys include both the
profile ID and account ID. Existing mutation invalidation retires all transaction
variants, including snapshots with embedded profile data.

### Upgrade existing records

Stop the backend before upgrading and take a MongoDB backup. From the backend
repository root, activate your usual environment, then audit existing records:

```bash
source src/bin/activate
cd src
python3 migrate_profiles.py --name "Matthew"
```

The command is a dry run by default. It reports how many existing platforms and
contribution limits lack ownership, and stops before writes if a transaction has
no valid platform, its account disagrees with the platform's account, or an
existing owner reference is broken. Fix reported records before applying.

Assign the records to their existing owner's profile, then restart:

```bash
python3 migrate_profiles.py --name "Matthew" --apply
export CACHE_NAMESPACE=stock-portfolio-profiles-v2
python3 app.py
```

Use the owner's preferred name in place of Matthew. You can instead pass
`--profile-id <id>` to use an existing profile. The migration is rerunnable: it
only assigns missing/null ownership, retains existing owners and transaction
IDs, and never deletes or reseeds records. If names are duplicated, select by ID.
Rotating the cache namespace retires snapshots created with the older schema.
Upgrade frontend and backend together: older clients omit the required profile
argument. **Do not run `database_init.py` to migrate an existing database.** That
script deletes the database; it is only for fresh installations and now seeds a
Default profile for its initial platforms.

To test profile isolation and migration with fake MongoDB/Redis:

```bash
python3 -m unittest discover -s tests -v
```

### Transaction date-range API

The API supports `transactionsByDateRange(profileId: ID!, startDate: Date,
endDate: Date)`. Both dates are inclusive. Either bound can be omitted; omitting
both returns all transaction history for the selected profile. Reversed ranges
are rejected. Results are ordered newest first. The dashboard no longer loads recent transactions; the dedicated Transactions page uses the paginated search API documented below. The older last-month query remains available for existing
callers.

## Initializing your Data
By default, your database should be empty with no collections. We will run database_init.py to seed your database with data from startup.json. Running this will delete your current database and create the necessary collections

```bash
python3 database_init.py
```

The startup.json file will be all your loading data to populate your collections. It is composed of the following data
- Currency (US, CAD currently)
- Accounts (Shared account-type definitions listed in `src/startup.json`, including Non-Registered Savings Account / `NRSA`)
- Platforms (The trading platforms that you use, ex: TD Direct Investing, Wealthsimple)
- Activities (All the possible transaction activities, ex: Contribution, Withdrawal, Buy, Sell, Dividends, etc.)

Below is a piece of the startup.json that shows the currencies model

```json
{
    "currencies": [
        {
            "name": "Canadian Dollar",
            "code": "CAD"
        },
        {
            "name": "United States Dollar",
            "code": "USD"
        }
    ],
}
```

## Running Web Server
Once you are fully setup you can start your local web server
```bash
python3 app.py
```
This will run your server on the URL [http://127.0.0.1:5002/](http://127.0.0.1:5002/) but since we are using GraphQL, our application is actually using [http://127.0.0.1:5002/graphql](http://127.0.0.1:5002/graphql). We use GraphiQL for our playground. Here we can test our GraphQL APIs and explore the documentation. For example, if we wanted to get all the activities, we can run the following in our GraphiQL playground.

Note that in order for you to be able to run requests successfully, you need to add your current IP address to the network access list on Mongo DB. Simply login to your mongo db account, click network access on the left panel, and click ADD IP ADDRESS.

```
{
  activities {
    edges {
      node {
        id
        name
      }
    }
  }
}
```

to produce the response
```json
{
  "data": {
    "allActivities": {
      "edges": [
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkOWI=",
            "name": "Contribution"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkOWM=",
            "name": "Transfer In"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkOWQ=",
            "name": "Transfer Out"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkOWU=",
            "name": "Buy"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkOWY=",
            "name": "Sell"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkYTA=",
            "name": "Dividends"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkYTE=",
            "name": "Withholding Tax"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkYTI=",
            "name": "Adjustment"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkYTM=",
            "name": "Stock Split"
          }
        }
      ]
    }
  }
}
```

## Transaction quantities and platform edits

Transaction shares accept decimal quantities with up to eight decimal places, including fractional buys and sells. Existing integer quantities remain readable without a data migration. GraphQL `TransactionInput.shares` uses `Decimal`, and transaction query results expose numeric share quantities. Restart the backend after changing the schema.

Editing a transaction can change its account type, platform, and platform currency, using an existing platform in the same profile. The account must match the destination platform. For currency changes, explicitly provide the total in the destination currency (except splits/spinoffs), confirm any previously nonzero fee, and re-enter spinoff cost allocations. Settlement currency defaults to the destination platform. Share price currency is preserved; same-currency trades reset FX to 1, while converted trades require an explicit new rate. Cash entries default their currency/FX to the destination. The API rejects incompatible changes and invalidates cached transaction lists after a successful edit.

### Force a fresh read

GraphQL requests with `X-Cache-Bypass: true` skip Redis reads for cached account and transaction queries, load MongoDB, and replace the matching cache entries. The application's refresh buttons send this header and bypass Apollo and browser caches. Normal page loads continue using the configured cache TTLs. Restart the backend to enable the header handling.

### Withdrawal activity

Fresh setups include Withdrawal. For an existing database, run `python src/add_withdrawal_activity.py --apply` from the backend project with its Python environment active. This only adds the missing activity, preserves existing IDs and transactions, and can be rerun. Without `--apply`, it only checks. Reload the frontend afterward to fetch the updated activity list.

Withdrawal is a positive cash amount that subtracts from net deposits. It has no stock and does not change holdings or book cost. Contribution-limit statistics continue to use gross contributions; withdrawals do not automatically restore contribution room. Withholding Tax accepts an optional stock so account-level tax can be recorded separately.

### Transaction validation and deletion

Contribution and Withdrawal transactions cannot reference a stock. Withholding Tax accepts a stock or no stock. The frontend clears inapplicable fields when switching activity and strips them before submission. Existing records are not automatically cleaned by upgrading.

`deleteTransaction(profileId: ID!, id: ID!)` checks profile ownership before deleting and invalidates cached transaction lists on success. The frontend asks for confirmation, refreshes active personal queries and statistics, and keeps the dialog open if deletion fails.

Net deposit history uses contributions + transfers in − transfers out − withdrawals. It excludes dividends, interest, and withholding tax. Buy totals already include acquisition fees, so book cost does not add those fees twice. Shares owned statistics use up to four displayed decimal places while calculations retain the stored precision.

Dividends/Interest Earned subtracts withholding tax only when the transaction references a stock. Withholding tax without a stock is treated as account-level tax and excluded from both lifetime and annual dividend/interest totals.

## Asset types

The shared `assets` collection contains Stock, Index Fund, Mutual Fund, and GIC. Each stock stores its reference in `asset_id`; GraphQL exposes `Stock.asset { id name }`, the `assets` connection, and `StockInput.assetId`. New stocks default to Stock when no type is supplied. Asset types are shared across profiles.

For an existing database, seed types and classify unclassified stocks without resetting the database:

```bash
cd src
python3 migrate_assets.py           # dry run
python3 migrate_assets.py --apply   # seed types and assign Stock where asset_id is missing
```

This migration preserves existing classifications and can be rerun safely. To reclassify a stock manually, set its `asset_id` to the corresponding `_id` from `assets` (an ObjectId, not a string); `updateStock` also accepts `assetId`. Restart the backend after updating the schema and reload the frontend to fetch asset metadata. Fresh database setup seeds the four types; do not run the destructive database initialization script against existing records.

The asset migration also splits the former Index/Mutual Fund type: existing references become Index Fund, and Mutual Fund is added separately. Reclassify any mutual funds afterward. If Index Fund already exists, references are moved to it before removing the obsolete combined type. Existing Mutual Fund classifications are preserved.

## Transaction validation and warnings

Create/update mutations validate Stock asset sales directly against MongoDB for the selected platform on the transaction date. Sales require a positive quantity and cannot exceed the shares held at that point: buys + share transfers in + split adjustments + shares received in spinoffs − previous sales − share transfers out. Spinoffs do not add shares to their source stock. Historical ledger edits are rejected if they would make a later sale exceed its available shares; messages identify the activity being saved and the later affected sale. Entries on the same date use record ID order, with new entries following existing ones. Missing legacy asset types are treated as Stock. Dividends, stock-linked withholding tax, and splits do not require an existing positive share balance; users are responsible for recording them correctly. Dividends and splits still require a stock. Dividend entitlement depends on actual ex-dividend dates, which the app does not store or estimate. Basic field, profile, currency, GIC contract, and spinoff cost-allocation validation remain in place.

Index Fund and Mutual Fund ownership checks are deferred (a backend TODO documents their amount-only tracking). Account-level withholding tax without a stock remains allowed.

Errors are returned through GraphQL `errors`, with a readable message and an `extensions.code` such as `STOCK_NOT_OWNED` or `INSUFFICIENT_SHARES`; rejected mutations do not save or invalidate the cache. Successful `createTransaction` and `updateTransaction` responses include `warnings { code message }` (an empty list normally). `CONTRIBUTION_LIMIT_EXCEEDED` is a warning after saving, comparing prospective lifetime contributions to all recorded contribution limits for the same profile/account type across platforms. It matches the current overview: no currency conversion, transfers/withdrawals excluded, edits count only the replacement value, and no warning if no limit exists. This is tracker validation against recorded limits, not a calculation of statutory contribution room.


### GIC purchases and maturity

Record a GIC using **Buy**, with principal, purchase date, maturity date, annual rate, and interest calculation (simple or annual compound). These estimates use actual days divided by 365; the amount actually paid by the institution is authoritative. GICs do not use shares or price per share.

Record **GIC Maturity** against an outstanding purchase in the same profile and platform. Enter the gross payout before tax and fees. The backend derives principal returned and interest earned. A purchase can mature only once; maturity before the purchase date or below principal is rejected. Early maturity and a payout different from the estimate save with warnings. Record associated withholding tax separately. Interest activity is for account interest, rather than GIC payouts.

Maturity removes the returned principal from outstanding book cost. Only interest contributes to income and realized profit; the payout does not count as a contribution or withdrawal. Delete the maturity before deleting its purchase or changing the purchase principal. Existing legacy GIC transactions are not rewritten automatically.

For an existing database, run from the backend `src` directory:

```sh
python3 add_gic_maturity_activity.py --apply
```

This adds the activity and a unique purchase-to-maturity index without changing transaction records. Fresh setup includes GIC Maturity in `startup.json`. Restart the backend after updating.


### Non-Registered Savings Account (NRSA)

`NRSA` is a shared account type named **Non-Registered Savings Account**. It can be selected when adding a platform for any profile. Fresh setup seeds it from `src/startup.json`; platform seeding resolves account types by their code.

For an existing database, use the backend's normal Redis environment variables and run from `src`:

```sh
python3 add_nrsa_account.py --apply
```

The script is safe to rerun, preserves existing account IDs, and invalidates account caches. It does not modify existing platforms or transactions. Refresh the frontend to load the updated account list.

Account types expose `has_contribution_limit` in MongoDB and `hasContributionLimit` in GraphQL. Existing account types default to `true`; NRSA is `false`. Unlimited accounts retain normal contribution/withdrawal/net-deposit tracking, but are excluded from contribution-limit setup and limit-chart selectors. The Contributions section includes their cards, displaying a dash for percentage and the contributed amount followed by `/ -` instead of a limit. The backend rejects new limits and skips over-limit warnings for these accounts, including when a legacy limit record exists. Existing limit records are preserved for inspection or deletion.

Run `python3 add_nrsa_account.py --apply` from the backend `src` directory with the normal Redis environment to update NRSA and backfill missing flags on existing account types. Explicit flags on other account types are preserved; fresh setup reads the flag from `startup.json`. Restart the backend and refresh the frontend after updating.


### Transaction currencies and exchange rates

A transaction stores `priceCurrency`, `totalCurrency`, and `exchangeRate`. Price currency defaults to the stock currency; total currency follows the selected platform. The exchange rate means units of total currency per one unit of price currency. Fees are entered in total currency. Buy totals are `price × shares × exchangeRate + fee`; Sell totals subtract the fee. The calculated total remains editable so the actual broker charge can be recorded. The backend allows calculated-versus-recorded differences of up to ±0.10 in the total currency, including exactly 0.10, to accommodate rounded statement prices. Larger differences return a warning; the entered total is always preserved. This applies to buys and sells in add, edit, and statement import flows. Different currencies require a positive exchange rate; matching currencies use 1.

Stocks can be selected across currencies, so a CAD platform can hold USD stocks. For Index Fund and Mutual Fund purchases with units, select **Enter price and shares**. Ownership validation applies to share-based funds; amount-only funds retain their existing behavior. Do not mix amount-only and share-based purchases for the same fund/platform. Stock statistics and transaction history separate recorded amounts by total currency instead of adding CAD and USD together.

For an existing database, run from the backend `src` directory with the normal Redis environment:

```sh
python3 migrate_transaction_currencies.py
python3 migrate_transaction_currencies.py --apply
```

The first command previews the changes. The migration fills missing currency metadata using the transaction platform currency and an exchange rate of 1. It preserves historical prices, totals, shares, fees, and explicit currency metadata; it does not convert historical amounts. Review historical transactions individually if their prices were originally entered in another currency. Fresh databases use the updated transaction model and need no backfill. Restart the backend and refresh the frontend after updating.


### Account service fees

Use **Service Fee** for a fee charged directly to an account. Select the account/platform and date, then enter the fee amount in Total (a positive expense in the platform currency). It does not require or accept a stock. It is separate from trading fees and does not count as a contribution, withdrawal, or stock dividend/interest. Total currency is inferred from the platform and is not shown as a form field.

Fresh setup includes this activity. For an existing database, run `python3 add_service_fee_activity.py --apply` from the backend `src` directory, then refresh the frontend. The script preserves existing activities and transactions and is safe to rerun.


### CSV transaction import

On **Add Transaction → Import Transactions**, paste Wealthsimple CSV into **CSV text** or click **Choose CSV** to read a file into that text box. Both inputs use the same CSV-only parser. The original PDF/statement text layout and Debit/Credit layout are no longer supported.

Required header (case-insensitive, with one of each column):

```csv
"date","transaction","description","amount","balance","currency"
"2025-11-04","DIV","ZFL - Bond ETF: Cash distribution","21.95","166.56","CAD"
"2025-11-04","ROC","ZFL: Return of Capital","0","166.56","ZFL"
```

Python's standard-library CSV reader handles quoted fields, commas/newlines inside descriptions, CRLF, and UTF-8 BOM. No external service or new dependency is needed.

- Dates use `YYYY-MM-DD`; execution dates in descriptions are retained as text.
- Codes: `BUY` → Buy, `TRFIN`/`EFT`/`CONT` → Contribution, `DIV` → Dividends, `NRT` → Withholding Tax, `FEE` → Service Fee, `REIMB` → ETF Rebate. `ROC` and `NCDIS` are explicitly skipped and listed in the preview. Other codes produce preview errors.
- BUY/FEE/NRT amounts become positive expense totals; negative or positive export amounts are accepted. Incoming activities require positive amounts. Actual recorded amounts settle in CAD; balance is ignored.
- Stock descriptions use `TICKER - Stock Name: ...`. Buys include `Bought <shares> shares at $<price> per share`. An `FX Rate: <rate>` identifies a USD stock/price; otherwise CAD is assumed. Trading fees default to zero. The recorded amount is preserved; calculated differences within ±0.10 do not warn.
- ROC and NCDIS are skipped before checking amount/currency because their currency field may contain a ticker. These entries affect tax adjusted cost base, which the current cash/purchase-based statistics do not track. NCDIS generally increases tax ACB; ROC generally reduces it. See [Wealthsimple’s ACB explanation](https://help.wealthsimple.com/hc/en-ca/articles/4409775037083-What-is-adjusted-cost-base-ACB).
- Existing tickers are reused with currency checks; missing stocks are created as asset type **Stock**, including share-based ETFs. Ambiguous tickers, name conflicts, unsupported GICs, and amount-only fund history block importing.
- Existing transactions are matched by posting date, activity, ticker, shares, price, total, fee, and currency/FX metadata. Identical duplicate occurrences are counted. Possible duplicates with different FX metadata require review.

**Preview** makes no portfolio writes. `previewStatementImport(profileId, platform, text)` returns parsed rows, the import plan, and a preview hash. `importStatement(profileId, platform, text, previewHash)` repeats preflight against current records and rejects stale previews before saving. Both operations validate platform ownership and require CAD settlement. Inputs are limited to 200,000 characters and 500 rows.

The browser reads a selected CSV into temporary text state and immediately clears the file input. It never uploads the File object or stores a source file. Only text is sent to Preview/Import. Text is cleared after successful import, via Clear text, or when the dialog closes. Partial imports retain it for another preview. File reading is limited to 800 KB. The backend processes the CSV in request memory and persists normal stock/transaction records only on Import.

Applying uses sequential existing GraphQL mutations and stops at the first failure; earlier successful saves remain. Results include saved IDs, warnings, and errors, with a downloadable JSON audit. Import operations are serialized within the backend process; other API/CLI clients should not concurrently import the same CSV. After a partial failure, preview again before retrying; saved entries are skipped. Requests are never automatically retried. Restart the backend after updating; no import-specific database migration is needed.

For the CLI, from the backend root:

```sh
# Offline parsing: no API calls or writes.
python3 src/import_statement.py activity.csv --report parsed-preview.json

# API preflight only.
python3 src/import_statement.py activity.csv --plan \
  --profile "Profile Name" --platform "Wealthsimple" --report import-plan.json

# Explicit apply, with an incremental audit report.
python3 src/import_statement.py activity.csv --apply \
  --profile "Profile Name" --platform "Wealthsimple" --report import-result.json
```

Use exact names (case-insensitive) or IDs. `--account` defaults to NRSA, and `--endpoint` defaults to `http://127.0.0.1:5002/graphql`. Input `-` reads CSV from stdin. `--apply` requires `--report` to record partial progress. PDF parsing is intentionally excluded.


CSV withholding tax: an unnamed `NRT` row inherits the ticker/name from the immediately preceding dividend only when its date matches. The preview labels this stock **From dividend**. Explicit stock labels are preserved, and intervening/error/skipped rows or different dates prevent inference; otherwise withholding stays account-level. Dividends and withholding without FX notes use the saved stock currency (or buy/FX information in the CSV for new stocks). A possible existing unlinked withholding transaction is flagged for review instead of importing a duplicate.

Transaction descriptions are used only to parse CSV imports and are not stored on transaction records. To clean up legacy descriptions, run `python src/remove_transaction_descriptions.py` to inspect the count, then add `--apply` to remove only that field. Configure Redis as for the API so the cleanup also invalidates and removes transaction cache snapshots.

**ETF Rebate** is an account-level cash reimbursement, parsed from `REIMB` as a positive total with no stock or trading fields. It increases account/homepage Realized Profit only; contributions, net deposits, book cost, and Dividends/Interest Earned are unchanged. Fresh setups include the activity in `startup.json`. For an existing database, run `python src/add_etf_rebate_activity.py --apply`; this idempotent script adds the activity without resetting data.

Account/homepage **Realized Profit** subtracts account **Service Fee** transaction totals in addition to separately recorded GIC fees. Trading fees are already included in recorded Buy/Sell totals and are not deducted again: purchase fees reduce realized profit as the corresponding shares are sold. Service Fees do not change Realized Gain/Loss, book cost, net deposits, or Dividends/Interest Earned.

**Stock Spinoff** records one atomic transaction document with `stock` as the received stock, `spinoff_source` referencing the original stock, `shares` as the received quantity, and `allocated_book_cost` in platform currency. The cash `total` is zero. Both must be Stock assets with the same quote currency. Enter the allocation from broker records; no market-data estimate is made. The event adds received shares, reduces source book cost by the allocation, and increases received book cost by the same amount. Source shares and combined account book cost are unchanged. This is excluded from purchase/sale cash flows, contributions, dividends, and realized profit on the event date; later sales use the adjusted cost basis.

`transactionsByStock` returns spinoffs involving the stock as either source or recipient so both stock pages can explain and apply the same action. Profile isolation and currency filtering are preserved. Mutation validation checks ownership and available source cost at the event date, replays later events when historical entries are edited, and prevents removing spinoff shares needed by later sales. Edits and deletes invalidate transaction caches. Older cache snapshots safely default missing source references to null.

For an existing database, run `python src/add_stock_spinoff_activity.py --apply` from the backend directory. This idempotent script only adds the activity. Restart the backend to load the schema changes. Fresh initialization includes it in `src/startup.json`. Existing transactions need no migration; enter historical spinoffs using your broker’s allocation. Do not rerun the destructive database initialization script against existing records.

### Bulk transaction updates and price precision

`bulkUpdateTransactions(profileId, transactions: [TransactionInput!]!)` accepts 1–200 changed rows with unique transaction IDs. It reuses single-edit validation and profile scope, returns `results { id success error code warnings { code message } }`, and invalidates transaction caches once after successful writes. Rows save independently in request order; this is not an atomic MongoDB batch. Failed rows can be corrected and retried without resubmitting successful rows. Dependent historical edits may require a second submission after the first change saves.

Transaction prices retain up to eight decimal places, and total calculation uses that precision before rounding the resulting monetary total to two decimals. CSV imports accept the same price precision. Existing prices need no migration. Restart the API after updating.

### SEC Fee

SEC Fee is an account-level expense available only for USD trading accounts. Enter its positive amount in Total without selecting a stock. It reduces Cash Balance and Realized Profit and increases Fees Paid, like Service Fee. It does not change stock holdings, book cost, Realized Gain/Loss, net deposits, or Dividends/Interest Earned. The backend validates the currency and account-only restriction for creation, individual edits, and bulk edits. Fresh setup includes the activity. Existing databases can add it idempotently from the backend directory with `python3 src/add_sec_fee_activity.py --apply`; restart the API afterward.

### Query performance diagnostics

GraphQL transaction queries batch related MongoDB documents to avoid repeated per-transaction reference reads. This preserves existing profile filters, results, and cache/refresh behavior; no migration is needed. See [the measurement report](docs/transaction-fetching-performance.md) for before/after results and the read-only `scripts/benchmark_transaction_queries.py` command. Recorded analytics are computed on demand by the shared backend ledger; paginated transaction search is documented below. No scheduled analytics engine is needed.

### Explicit transaction search and fetch metadata

`searchTransactions(profileId, account, platform, stock, activity, currency, startDate, endDate, first, after)` is an explicit search API. All filters are optional, intersect with profile ownership, and run in MongoDB. `first` defaults to 100 and is restricted to 1–100. The result contains `transactions` and `nextCursor`; pass the cursor as `after` to fetch the next batch. Pages use descending `(transaction_date, id)` keyset ordering and hydrate references only for the bounded batch. Missing/invalid profiles, invalid filters/cursors, and inverted date ranges return GraphQL errors. This avoids fetching the entire history to apply table filters. Search does not use Redis.

Redis cache entries now have a versioned envelope containing `last_updated` and the encoded payload. Legacy entries reload automatically. Successful GraphQL HTTP responses include `extensions.dataFreshness { lastUpdated, cacheHit }`. The timestamp is conservative: it is the oldest source fetch time used by that response, including cached reference data. Cold bypass requests still refresh the existing cache. No MongoDB migration or scheduled worker is required.

### Account transfers and platform closure

`previewAccountTransfer(profileId, transFrom, transTo, transferDate, closeOriginalAccount)` calculates the remaining recorded assets and cash. `transferAccount` also requires `marketValues: [{stockId, marketValue}]` for every asset in the preview (total market value in the account currency, including amount-only funds), and saves paired **Transfer Out / Transfer In** transactions, optionally setting the source platform’s nullable `closed_at` date. `closeOriginalAccount` defaults to true for compatibility; uncheck **Close Original Account** in the dialog to retain an open source platform. It preserves every historical transaction and requires open platforms in the same profile, account type and currency.

Asset rows carry stock, shares (when applicable), and remaining book cost in `total`; cash rows have no stock. `transfer_market_value` separately stores the asset value moved on the transfer date, identically on both legs. It does not change cash, book cost, realized gain or unrealized gain. Future transfers require a manually entered value (`transfer_market_value_source=manual`). Legacy values may be backfilled with unadjusted daily closing prices × shares (`yahoo_close`), retaining `transfer_market_price` for provenance; these are estimates, not broker execution prices. Existing documents may omit these nullable fields. `transfer_batch`, `transfer_pair`, and `transfer_counterparty` identify the linked entries. Add/edit/bulk/import paths reject transaction dates after platform closure; linked transfer rows cannot be changed or deleted individually. Existing platforms need no migration: missing `closed_at` means open.

Saving the pairs and closure uses a MongoDB transaction and requires Atlas or a replica set. There is deliberately no non-atomic fallback. Redis transaction snapshots are invalidated after success. Negative cash/holdings, undated source transactions, later source transactions when closing, unresolved amount-only fund sale costs, and outstanding GIC contracts block the transfer with an explicit error. When keeping the source open, balances are calculated through the transfer date; later transactions remain on the source, and a later sale without enough remaining shares blocks the transfer. GIC contract transfers remain unsupported because their maturity links must remain valid.

## Hourly market-price worker

Market-price fetching runs separately from Flask in the `market-data` Docker target,
using this repository's MongoDB models. The stack repository includes it as a Compose
service; there is no new repository. The worker reads MongoDB and writes only Redis.

- Runs immediately on startup, then every 3,600 seconds (configurable with
  `MARKET_DATA_INTERVAL_SECONDS`, minimum 3,600).
- Counts current shares per platform through today, including buys, sells, splits,
  spinoffs and asset transfers; fetches one quote per stock held in any profile/platform.
- Only share-based assets (`Stock`) with CAD/USD currency are supported initially.
  Amount-only Index/Mutual Funds and GICs are excluded. Share-based ETFs recorded as
  `Stock` are supported. A newly purchased stock becomes eligible next cycle.
- Uses XTSE/XNYS exchange calendars for regular trading sessions, holidays, daylight
  saving changes and early closes. During closed sessions it fills missing quotes and
  captures the latest completed session's close. It does not fetch extended-hours prices.
- Quotes have no short expiry: preserve the last successful observation over holidays
  and provider outages. Redis eviction/restart is recovered on the next worker cycle.
- After successfully reading the ledger, each cycle removes cached quotes for stocks
  no longer held in any profile/platform. A final sale removes its quote next cycle
  (within about an hour while the worker runs); selling in just one platform does not
  remove a quote needed elsewhere. MongoDB read failures skip cleanup entirely.
- Yahoo Finance through `yfinance` is unofficial, intended for personal use, and can be
  rate-limited or unavailable. Quotes are reference data, not a guaranteed live feed.

For a non-Docker launch (Python 3.11+):

```sh
pip install -r requirements-market-data.txt
# Configure MONGODB_URI, MONGODB_DATABASE and REDIS_URL like the backend.
PYTHONPATH=src python -m market_data.worker
PYTHONPATH=src python -m market_data.worker --once
```

Default provider symbols are `<ticker>.TO` for CAD and `<ticker>` for USD. Optional
`market_symbol` and `market_exchange` stock fields override these mappings (GraphQL
`StockInput.marketSymbol` / `marketExchange`, exchange `XTSE` or `XNYS`). Confirm the
provider symbol for non-TSX listings and dual listings; the worker checks quote currency
against the stock before caching. No migration is required for existing stocks.

GraphQL can read quotes on stock nodes without invoking Yahoo:

```graphql
{
  stocks {
    edges {
      node {
        id ticker
        marketQuote {
          price currency symbol exchange source priceKind
          quoteTime lastUpdated ageSeconds refreshOverdue
        }
      }
    }
  }
}
```

`marketQuote` is null for missing/unsupported quotes. `quoteTime` is the observation
(or completed session close); `lastUpdated` is the worker fetch time. `refreshOverdue`
means the last fetch was over two hours ago, not that a weekend closing quote is invalid.
Consumers should consider price kind, quote age and the exchange session before labeling
prices stale. Quote keys are `<cache-prefix>:market-quotes:v1:<stock-id>`, independent of
transaction-cache generations. Changing the stock mapping hides its old cached quote.

Market value is current shares × price; unrealized gain/loss is market value minus
remaining book cost (which already includes applicable historical trading fees).
Account value is market value plus cash, not unrealized gains plus cash. Live prices
must never change realized gain/loss or recorded book cost. Convert market value using
current FX for mixed-currency accounts; never sum CAD and USD directly. This worker
provides quotes; market-value UI and FX integration are separate from existing stats.

### Market valuation in GraphQL and React

`marketValuation(profileId: ID!, currency: String!, platform: ID, stock: ID, account: ID)`
calculates current holdings and recorded cash on the backend, using only Redis quotes.
All positions use their platform's recorded currency; dashboard CAD/USD tabs remain
separate. `stock` selects that stock's positions within the selected currency platforms.
Future-dated transactions are excluded. Profile and platform ownership are enforced.

The worker also caches one USD/CAD rate from Yahoo (`CAD=X`) and reads its reciprocal
for CAD/USD conversion. Current FX affects market value only; historical settlement
totals, book cost, and realized gains remain unchanged. FX lives at
`<cache-prefix>:market-fx:v1:USD:CAD`. Page refresh reads cached prices/rates and never
contacts Yahoo. The worker refreshes FX hourly during regular equity sessions and once
after the latest completed session, preserving the last successful rate on errors.

The React dashboard, account and share-based stock pages display a separate Market
Valuation section: account value (or native current stock price), market value,
unrealized gain/loss and unrealized return. Prices and fetch times are shown below it.
Existing recorded/realized statistics are unchanged. Open GICs and amount-only funds
are unpriced; incomplete valuations show dashes, a warning, and affected tickers rather
than presenting a partial sum as the complete portfolio value. `pricedMarketValue`
exposes only the available priced portion for future use; `marketValue`, `totalValue`
and unrealized metrics are null until the selected holdings can all be valued.

Example:

```graphql
query($profile: ID!, $platform: ID!) {
  marketValuation(profileId: $profile, platform: $platform, currency: "CAD") {
    currency complete marketValue bookCost cashBalance totalValue
    unrealizedGain unrealizedReturn quoteTime lastUpdated missingTickers warnings
  }
}
```

### Historical transfer market-value backfill

`scripts/backfill_transfer_market_values.py` runs with the market-data dependencies and configured MongoDB/Redis environment. `preview --profile ID --plan /private/plan.json` fetches exact-date Yahoo Finance daily **Close** (`auto_adjust=False`), validates linked pairs, and writes a private before-image backup and proposed values. `apply` with the same arguments rechecks the unchanged ledger and writes all pairs atomically, with a migration journal and cache invalidation. It never changes cash, basis, shares or dates. Missing trading-day prices, unsupported funds, foreign-currency assets and incomplete pairs require manual review. Keep plans outside Git. Yahoo’s historical Close can be split-adjusted; review later splits before applying to older transfer quantities. These transfer values are used for boundary transfers in the annualized-return calculation described below.

### Annualized return

`marketValuation` also returns `annualizedReturn` (percentage), `annualizedStartDate` (first recorded investment cash flow), and `annualizedReturnNote`. The backend calculates money-weighted XIRR with actual dates on a 365-day basis, following [Microsoft’s XIRR definition](https://support.microsoft.com/en-us/excel/functions/xirr-function). It does not divide unrealized return by account age. Account/portfolio scopes use contributions, withdrawals and transfers across the scope boundary, plus current holdings and cash as the ending value. Retained dividends, interest, sales and fees affect the ending value and are not counted again as external cash flows. Matched internal transfer pairs cancel; boundary asset transfers use `transfer_market_value`, and historical Yahoo-close estimates are disclosed in the note. CAD and USD remain separate scopes.

Stock scopes use dated buy totals, sale proceeds, stock-linked income/tax and boundary transfers, with current holdings as the ending value. Fees already in trade totals are not subtracted twice, and account-only fees are not attributed to a stock. Splits produce no cash flow. Stock-level spinoffs need an actual spinoff-date market value; cost allocations alone cannot establish that return, so affected stock returns remain null with an explanatory note. Account/portfolio spinoffs are internal movements.

Missing valuations, missing transfer values, insufficient dated cash flows, negative ending values, or no single stable bracketed solution return null (rendered as a dash). The bounded solver scans log(1+rate) from -20 to 20, bisects brackets, verifies the residual and rejects detected multiple roots; it does not claim an answer outside that numerical range. No market-provider requests or database writes occur in this calculator. Results use the complete scoped ledger through today in America/Toronto and the existing cached market valuation.

### Recorded financial analytics

`financialAnalytics(profileId, account, platform, stock)` returns separate CAD/USD
summaries: statistic values, book-cost distributions, account distributions, book-cost
and net-deposit history, buy/sell history, income/tax history, and ledger issues. The
account, platform and stock transaction queries can request analytics in the same
GraphQL operation. A request-local scope memo reuses the transaction read for both
fields; account reads retain Redis caching and `X-Cache-Bypass` behavior. Each page
selects only the summary/history fields it displays. Dashboard queries no longer
transfer all transactions just to calculate cards.

`schemas/analytics_ledger.py` is the shared Decimal ledger for recorded analytics and
market valuation. Average-cost purchase basis stays separate for each platform before
aggregation. Sales remove proportional basis; splits change quantity; spinoffs move
allocated basis; asset transfers carry basis without creating profit or moving cash.
Trading fees already included in trade totals are not deducted twice. GIC principal
and interest remain separate. Realized gains remain null when a disposal has missing
shares or an amount-only fund has no recorded disposal basis. Market valuation still
uses holdings through today; recorded analytics preserve the existing all-recorded
transaction scope, including future-dated entries.

`contributionAnalytics(profileId)` returns lifetime contributions, summed saved limits,
percentage used and cumulative history per account type. It matches the existing
contribution overview across platforms and currencies without FX conversion. NRSA
returns no limit or percentage. These are computed summaries, not new collections or
a scheduled analytics engine. Edits are reflected on the next query; the frontend
invalidates its analytics entries and refreshes active queries after mutations.
