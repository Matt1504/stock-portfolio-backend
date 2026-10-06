# Analytics and transaction-history redesign

Status: scheduled/persisted analytics deferred after the measured performance improvement; original design retained for reference. Updated October 5, 2026. Initial measurement and reference batching are complete; persistent analytics implementation has not started. The small single-platform selection improvement is implemented separately in the frontend.

## Current direction: on-demand summaries

Do not introduce scheduled jobs or analytics collections at current volumes. Keep transaction records authoritative, with batched reference reads. The dedicated transaction search now uses bounded MongoDB keyset pages, and the dashboard no longer displays recent transaction rows.

A future deterministic Python calculator can serve `dashboardSummary(profileId)` and account/stock summary fields directly. Return totals, holding counts, realized results, contribution totals/limits, and book-cost distribution, separated by currency, with fetch metadata. Run the calculator on demand; optional cached summaries must invalidate with transaction/platform/stock/contribution-limit changes and support cold bypass. No scheduled rebuilding is needed.

The dashboard then needs summary data only. Account and stock pages can request summary fields plus bounded history for charts/tables; those rows should not be used to infer all-time totals. Aggregate histories can later be returned as chart points if payloads warrant it. Preserve current calculations with fixture parity tests (partial sales, spinoffs, GICs, amount-only funds, fees, withholding, and currency handling) before migrating any cards. This UI phase retains the existing frontend financial formulas.

## Intended behavior

- Account/platform and stock cards and Book Cost Distribution describe the complete recorded history, regardless of the selected history range.
- A single History range control on each account/stock page governs its charts and transaction table: 1M, 6M, YTD, 1Y; default 1M, maximum one year.
- Dashboard cards use complete-history analytics; its Recent Transactions table is locked to the last month.
- A new `/transactions` page searches older records explicitly, with contained infinite scrolling and bulk editing.
- MongoDB transactions remain authoritative. Analytics are derived, rebuildable records. Redis remains an optional cache, not the only place keeping analytics or pending work.
- Every personal request requires profileId. Stock entities remain shared; stock analytics are per profile and recorded currency.

## 1. Measure and remove the immediate bottleneck

Measurement confirmed repeated MongoEngine reference reads as the main delay. Query-local batch materialization now serves transaction lists, scoped connections, and stock metadata. Cold GraphQL execution for FHSA Wealthsimple decreased from 11.2 seconds to 0.17 seconds and XEQT from 22.4 seconds to 0.17 seconds, with identical response data. See [the performance report](transaction-fetching-performance.md) for methodology, read counts, frontend timings, and reproduction commands.

Date bounds, real database pagination, and index analysis remain subsequent work. ProfileConnectionField still materializes its filtered dataset before Relay pagination, and platform/stock pages still download full history. This initial fix changes neither financial formulas nor the planned analytics API.

Do not introduce a transaction-count cutoff initially. Use the same analytics API for every scope; instrument performance before deciding whether any scope needs different treatment.

## 2. Backend calculation model

Build one deterministic Python calculator. It consumes transactions in stable `(transaction_date, id)` order and produces analytics, positions and daily histories. Port the current frontend rules with parity tests before replacing them.

Use Python Decimal for arithmetic and Decimal128 for new monetary/quantity storage. Preserve up to four displayed share decimals and the existing higher precision for price/FX inputs; round currency at the established transaction/payment boundaries, not at every intermediate cost-basis calculation. Serialize decimal values explicitly at the GraphQL boundary.

Rules to preserve:

- Cash uses recorded totals, in-kind transfers do not move cash, and buy/sell totals already include trade fees.
- A sell removes the average book cost of its disposed shares; another unsold buy does not reduce realized gain.
- Buy fees enter cost basis and affect realized profit when their shares are sold. Sell fees must not be deducted twice.
- Service Fee and SEC Fee reduce account realized profit. Stock-level fees exclude unrelated account fees.
- Stock-linked withholding reduces stock income; account-withdrawal withholding does not reduce Dividends/Interest Earned. Preserve the separate current realized-profit rules during the parity phase.
- Stock Split quantities represent additional shares; Stock Spinoff allocates recorded cost between its destination and source holdings.
- Amount-only Index/Mutual Funds and linked GIC maturity transactions retain their existing special rules and unresolved disposal limitations. No inferred market values or gains.
- Largest/Smallest Holding include only current positive holdings by remaining book cost. Counts are recomputed from holdings rather than summed across platforms.
- Keep calculations separate for CAD and USD. No sum of CAD and USD dollar figures without an explicit conversion policy.

## 3. Proposed MongoDB collections

| Collection | Logical key | Purpose |
|---|---|---|
| platform_analytics | profileId + platformId | Complete-history cash, contribution/withdrawal/transfer totals, income, fees, realized results, counts and dates |
| stock_analytics | profileId + stockId + recordedCurrencyId | Complete-history stock totals, counts, dates and realized results aggregated from its platform positions |
| platform_stock_positions | profileId + platformId + stockId + generation | Shares, remaining book cost, realized gain and relevant position totals; source for both distribution views |
| platform_history_daily | platformId + generation + date | Daily closing book cost/net deposits plus cumulative money movement anchors |
| stock_history_daily | profileId + stockId + recordedCurrencyId + generation + date | Daily buy totals, sale proceeds, shares bought/sold and stock income for bounded charts |
| contribution_analytics | profileId + accountId + periodEnd + reportingCurrency | Contributions within a configured period, independently of whether a limit exists |
| analytics_changes | mutation/event ID | Durable old/new change information, affected scopes and processing progress |
| analytics_jobs | scope + target revision | Durable, deduplicated rebuild/replay work and error/retry status |

`platform_stock_positions` is the proposed book_cost_distribution_platforms_stocks collection with a shorter descriptive name. Each document still has its own MongoDB ID. Unique indexes enforce its logical key. Include zero/closed positions if necessary for realized results; the distribution query only returns positive current shares or book cost.

The platform determines currency and ownership, but storing profileId and reporting currency on derived records makes scoping/indexes explicit. Stock analytics cannot be keyed only by stockId because different profiles own the same stock and a stock can be recorded in multiple account currencies.

Every analytics generation includes sourceRevision, calculationVersion, computedAt, generation ID, status and any incomplete-data issues. New fields on transactions include a write revision/updated timestamp for conflict detection and durable change tracking.

A nightly rebuild may publish a new generation containing all affected projections. Readers must use the published generation; they must not combine new positions with older totals. Old generations are retired after publication. A profile-level publication manifest identifies the platform revisions used for stock/dashboard aggregation.

## 4. Updates after transaction mutations

A purely incremental plus/minus approach is correct for some fields, but not for all statistics.

Example: buy 10 shares for $1,000, then sell 5 for $600. Realized gain is $100. If the historical buy becomes $800, realized gain becomes $200 and remaining book cost becomes $400. Updating only buy totals leaves both results wrong.

Use a hybrid pipeline:

1. Validate with the existing ownership, currency, GIC and spinoff rules.
2. Save the transaction and its durable change event atomically. Record old and new platform, stock, source stock, activity, dates, amounts and revisions. A delete needs its old record too.
3. Apply additive changes safely (cash, contributions, transfers, income, fee totals) once per event. An update reverses the old effect and applies the new one.
4. Apply append-only position changes when the event is strictly later than the last processed ledger position. Recalculate counts and largest/smallest from the affected positions.
5. For historical edits, replay only the affected platform-stock ledger from the earliest affected date/checkpoint. Splits, spinoffs, GIC changes and moves may involve multiple positions.
6. Rebuild affected platform/stock/day projections and publish the consistent generation. Until then, serve the previous complete generation marked updating rather than mixing revisions.

Initially prefer replaying the affected ledger in the worker for any position-changing mutation. Add the append-only fast path once its parity and idempotency are tested. This keeps the first version understandable and avoids incorrect incremental cost basis.

The mutation request must not run an unrestricted calculator. Durable work is handled by the worker. The mutation returns saved-record results plus analytics status/target revision so the frontend can show Saved / Updating statistics and check for completion without holding a dialog open.

Bulk edits/imports keep their existing partial-success contract. Only successful rows produce events. Deduplicate affected scopes and replay from their earliest changed date, rather than starting a full job per row. Moving a transaction invalidates both old/new platform and stock scopes, both applicable contribution periods, and dashboard aggregates. Renaming stocks/platforms changes labels; changing asset type requires relevant calculation rebuilds. Editing contribution deadlines/limits invalidates the corresponding contribution periods.

A retry must not apply the same money delta twice. Use durable event IDs and conditional revision/generation publication. If transactions change during a rebuild, do not publish a result as current unless its source revision still matches; queue the newer revision.

MongoDB supports multi-document transactions on supported replica-set/sharded deployments. Verify the existing deployment and refactor mutation persistence to pass sessions explicitly; separate MongoEngine saves and later queue publication cannot guarantee atomicity. Keep the database transaction short: persist the record, event and revision, not the expensive computation. The worker can recover pending events after any process restart.

Direct MongoDB edits bypass application events. A full rebuild/Refresh reconciliation is required; scheduled rebuilds must read source transactions rather than trusting cached snapshots or only updated timestamps.

## 5. Worker and scheduling

Recommended first version: one additional `analytics-worker` Docker Compose service using the backend image and a Python worker entry point, with MongoDB-backed durable jobs. It processes/coalesces mutation work promptly and performs a full nightly reconciliation of every profile's scopes.

Starting defaults, adjustable after measurement:

- Poll for pending/coalesced work every 1–2 seconds.
- Process bounded batches, retry failed jobs with backoff, and expose failures/freshness metadata.
- Use expiring job leases with renewal and revision checks so restarts or overlapping workers cannot corrupt projections.
- Run one scheduler leader for periodic rebuilds; coalesce repeated refresh/mutation requests for the same scope.
- Publish rebuilt records only after success, retaining the previous complete generation on failure.

This avoids adding a queue framework before it is needed. Celery worker + beat is a viable later alternative for higher concurrency, but the durable MongoDB events still remain necessary. Do not put durable queue work in the existing disposable, memory-limited Redis cache. If using Redis as a broker later, give it separate persistence/memory configuration. Periodic jobs can overlap; scheduler leadership alone does not replace job leases/idempotency.

Redis may cache published analytics by generation and query scope. Losing Redis must not lose analytics or work.

## 6. GraphQL contracts

Proposed names; retain old fields during migration:

- `platformAnalytics(profileId, platformId)` returns values + freshness/revision/issues.
- `stockAnalytics(profileId, stockId, recordedCurrencyId)` returns values per recorded currency.
- `bookCostDistribution(profileId, platformId?, stockId?)` returns IDs, stock/platform labels, shares, book cost and currency. Both filters intersect. Neither filter means all positions belonging to the selected profile, not every user's positions.
- `dashboardAnalytics(profileId)` aggregates platform results on the backend and returns CAD/USD summaries, correctly deduplicated stock counts and holdings.
- `contributionOverview(profileId, periodYear)` returns account, actual period dates, contributions, saved limit, hasLimit and freshness.
- `platformHistory(profileId, platformId, startDate, endDate)` returns bounded daily book-cost/net-deposit series and an opening anchor.
- `stockHistory(profileId, stockId, recordedCurrencyId, startDate, endDate)` returns bounded buy/sell/income buckets.
- `transactionSearch(profileId, filters, first, after, expectedRevision?)` returns actual cursor-paginated transactions and pageInfo, with optional total count.
- `refreshAnalytics(profileId, scope)` bypasses cached source data and queues/deduplicates a rebuild, returning its job/revision status.

Fetch fast cards/distribution together. Fetch history and table pages separately in parallel so a slow table query cannot delay the cards. One GraphQL operation may select multiple summaries; this does not require downloading full transactions.

Server-side transaction filters include account, platform, stock, activity, currency, start/end date and optional amount/text criteria. Keep payloads limited to the fields displayed/edited. The stock history includes relevant source-stock corporate actions for book-cost calculation, but tables identify the actual transaction owner.

Indexes should cover platform/date/id and stock/date/id. Stock queries across profiles first select only owned platform IDs. Include spinoff_source indexes for relevant ledger work and verify query plans; add more indexes only when actual filters justify them.

## 7. History controls and graph correctness

Use one compact segmented control aligned right at the start of the history section: 1M | 6M | YTD | 1Y. It becomes sticky under the existing app header while the user scrolls that section. Use a solid theme-aware background, small label and z-index that stays below dialogs/tooltips. Do not make it a detached floating overlay that covers table rows.

Define explicit inclusive calendar-date boundaries in the application's timezone: rolling one/six/twelve months ending today; YTD starts January 1. Show the resolved date interval. YTD can overlap 1M/1Y; no special omission is necessary. Exclude future-dated records unless explicitly requested in the all-history search. Keep the range in the URL alongside existing profile/account/stock/currency IDs.

The control changes only histories and their table. Label cards and distributions All-time/current holdings so the user does not mistake them for period totals. Stock Transaction History becomes **Buy & Sell History**. Income charts are shown only for applicable assets/activity, and shareless funds do not show zero-share labels.

Cumulative line charts need the pre-range state. A last-month-only transaction query cannot reconstruct current book cost or net deposits: buying years ago still contributes to today's holdings. Use daily materialized balances and an opening anchor at range start, then return points within the selected period. If there is no activity, show a flat balance through the range rather than a misleading zero/empty series.

For stock bars, return only activity in the range: Buy and Sell both positive; Buy red, Sell green. Dividends/Interest remain green. No missing events are inferred.

Requests are keyed by profile, scope, currency, range and data revision. Retain a per-key in-flight promise so toggling views does not abort useful requests. Reuse a loaded larger range for a subset where possible. Responses for an old key may populate its cache but must never replace the active selection. Preserve both CAD/USD queries while switching tabs, show scoped skeletons, and invalidate affected keys after writes.

The one-year limit bounds time, not row count. Use server pagination even within 1M for large accounts; graph buckets do not require every table row.

## 8. Contributions and dashboard

Current implementation sums lifetime contributions against all saved limits. Switching to current-year amounts changes its meaning and the over-limit warning; update both together.

Keep saved contribution limits as configuration. Recommend a separate contribution_analytics collection instead of only contributions_in_year on a limit row:

- NRSA must still show contributions despite having no limit row.
- Actual contributions are derived; limit/deadline settings are user input.
- Prior periods and calendar/deadline changes can be recalculated without overwriting user input.

For each profile/account type, aggregate across its platforms. A period runs from the day after the previous configured deadline through the current deadline. Store the actual boundaries and resolve gaps/overlaps explicitly. If old deadlines do not define a prior boundary, require/configure one rather than guessing from a missing record. NRSA can use an explicit calendar-year period with a null limit.

Open decision: whether the displayed current-period limit is the saved amount for that period or includes carried-forward room. Existing limits currently act as summed lifetime allowances; do not silently treat them as a verified current-year budget. Preserve records and migrate only after confirming their intended meaning.

The existing overview adds recorded CAD and USD amounts without conversion. Do not imply this is a CAD-normalized budget. Preserve and label the convention during parity, or introduce an explicit CAD contribution amount/recorded FX policy before displaying a converted limit comparison. Do not use today's exchange rate for historical contributions.

Prefer horizontal account bars: account codes are easy to scan. Plot contributions and a per-row dotted tick for its saved limit, rather than one universal reference line when each account has a different limit. NRSA has no marker and displays contribution / —. Surface amounts and over-limit state in text/tooltips as well as color.

Dashboard analytics sum additive platform measures separately by currency. Unique Shares Owned and Largest/Smallest Holding require merging positions by stock rather than summing platform counts. Derived ratios such as average cost likewise require combined numerators/denominators. Preserve current money/fee timing rules; do not sum FX-incomparable stock amounts.

## 9. New /transactions page

- Initial state shows filters and guidance; it sends no transaction query until Search.
- Filters can be combined; not every filter is mandatory. All time is an explicit choice, always scoped to the selected profile.
- Editing filters does not refetch; Search commits an immutable filter snapshot and resets its cursor.
- Fetch 50 rows at a time using descending `(transaction_date, id)` keyset cursors; impose a maximum page size of 100.
- Use a fixed-height, internally scrolling table with virtualized rendering, Load more fallback, loading/error/end-of-results states and no visible page-number pagination.
- Bind cursors to the filter/snapshot revision. Concurrent writes must not cause silent duplicates/skips; deduplicate IDs and restart stale searches with an explanatory message.
- Bulk editing applies to loaded rows. Retain drafts while scrolling, disable Refresh/Search/filter changes during editing, and prevent background responses overwriting drafts. Initially freeze additional page loading during bulk editing to keep the editable set clear.
- Current backend bulk update limit is 200 changed rows. Keep that explicit, or add chunked submission with per-row results; do not send an unbounded edit payload. Preserve partial failures/warnings and conflict detection. Never imply unloaded rows were edited.
- No sticky Total or visible Status column; retain existing conditional column hiding.

Existing account/stock tables can keep 25/50/100 pagination while fetching pages server-side. Infinite scrolling is only required for the new search page.

## 10. Delivery phases and verification

1. **UX quick win:** auto-select a sole eligible platform in Add Transaction, CSV Import and transfer forms. Single/bulk edit already select existing or compatible platforms. Keep multiple-option fields user-selectable and clear stale choices when their scope changes.
2. **Performance baseline:** add request/query metrics, bulk reference loading, bounded transaction search and true server pagination; validate against representative scopes.
3. **Calculator parity:** extract backend Decimal calculation service; add fixtures for stock buys/sells, cost/fee timing, splits, spinoffs, in-kind transfers, CAD/USD recording, GICs and amount-only funds. Compare complete outputs to current frontend rules; log deliberate limitations.
4. **Derived storage/worker:** non-destructive schema/index migration, durable mutation events/jobs, generation publication, rebuild command, nightly schedule, cold-refresh behavior and health/error metadata. Backfill from source records without altering transactions.
5. **Shadow rollout:** calculate and compare results in parallel with current displays; resolve mismatches before changing displayed data. Test replay versus full rebuild, retries, concurrent writes, failed jobs, bulk partial success, deletions and moves across every scope.
6. **Account/stock pages:** switch cards/distributions to analytics, introduce shared range control and server-backed history/table requests, remove duplicated frontend financial calculations.
7. **Dashboard/contributions:** backend aggregation, last-month table, agreed period/limit semantics and per-account contribution markers.
8. **Transactions search:** explicit search, contained infinite scrolling, existing single/bulk edits, conflicts and loading states.
9. **Cleanup:** retire unused all-history queries/calculators only after parity and rollback checks. Update both repository READMEs, fresh setup scripts, migration instructions and stack Compose.

Initial performance targets, to validate on the deployed connection: analytics response below 500 ms once materialized; table/history below one second for typical bounded requests; a saved mutation should close its dialog immediately after persistence, with analytics completing shortly afterward. These are goals, not benchmark results or guarantees.

Acceptance criteria: no history fetch on search-page load; no full-history transaction download for cards; consistent all-time cards across ranges; anchored cumulative charts; CAD/USD requests finish without tab-switch cancellation; profile isolation; correct results after past edits and repeated retries; no fee double counting; current contribution warnings and overview agree; no reseeding/deleting existing source data.

## Official references

- MongoDB multi-document transactions: https://www.mongodb.com/docs/manual/core/transactions/
- Celery periodic-task overlap/scheduler considerations (optional future queue): https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html
