# Stock Portfolio Backend

## Overview
This project is the code that runs the backend server for our Stock Portfolio application. It connects to a MongoDB database that stores our data. API requests are using GraphQL and handled with Graphene-Python. The web server application is hosted using Flask. 

## Getting started
Set up Mongo DB [account](https://www.mongodb.com/cloud/atlas/register)

Set up your cluster

Create passwords.py in directory src/database/passwords.py and add your USER, PASSWORD, and CLUSTER variables in the following format 
```python
USER = "USER_NAME"
PASSWORD = "PASSWORD_VALUE"
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

### Recent transactions date range

The dashboard uses `transactionsByDateRange(profileId: ID!, startDate: Date,
endDate: Date)`. Both dates are inclusive. Either bound can be omitted; omitting
both returns all transaction history for the selected profile. Reversed ranges
are rejected. Results are ordered newest first. The frontend defaults to today
and the preceding 29 calendar days, and sends updated bounds when the user
changes the date picker. The older last-month query remains available for existing
callers.

## Initializing your Data
By default, your database should be empty with no collections. We will run database_init.py to seed your database with data from startup.json. Running this will delete your current database and create the necessary collections

```bash
python3 database_init.py
```

The startup.json file will be all your loading data to populate your collections. It is composed of the following data
- Currency (US, CAD currently)
- Accounts (Shared account-type definitions listed in `src/startup.json`)
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
This will run your server on the URL [http://127.0.0.1:5000/](http://127.0.0.1:5000/) but since we are using GraphQL, our application is actually using [http://127.0.0.1:5000/graphql](http://127.0.0.1:5000/graphql). We use GraphiQL for our playground. Here we can test our GraphQL APIs and explore the documentation. For example, if we wanted to get all the activities, we can run the following in our GraphiQL playground. 

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

Editing a transaction can change its account type and platform, using an existing platform in the same profile and currency. The account must match the destination platform. The API rejects incompatible changes and invalidates cached transaction lists after a successful edit.

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
