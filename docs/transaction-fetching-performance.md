# Transaction fetching performance

Measured October 4, 2026, before the analytics redesign. Repeated MongoEngine lazy reference reads were the main source of the reported delays. GraphQL resolved the same platform, account, stock, currency, and asset documents again for each transaction.

## Cold GraphQL execution

These are single-run measurements against the existing MongoDB database using the host Python 3.9 environment and the frontend's actual transaction query selections. Redis was disabled, database connection startup was excluded, and no portfolio records or indexes were changed. Durations exclude HTTP transfer and browser rendering; they are observations rather than latency guarantees.

| Scope | Transactions | Before | After | Mongo commands before → after | Data JSON bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| FHSA TD Multi-Holding | 8 | 675 ms | 110 ms | 70 → 12 | 6,597 |
| FHSA Wealthsimple | 144 | 11,198 ms | 170 ms | 1,122 → 13 | 112,076 |
| XEQT | 250 | 22,355 ms | 172 ms | 2,254 → 11 | 215,845 |

Commands count reads (`find` and `getMore` in these runs). Data JSON size is the serialized GraphQL data object, not compressed HTTP transfer size. Parsed before/after response objects matched exactly for all three scopes, including ordering, nested references, and numerical values.

## Implementation

`src/query_loading.py` materializes references in batches per model and traversal level, reusing documents within one query. It loads nested asset/currency/account/profile references and linked GIC purchases and spinoff sources, handles cycles, and preserves missing-reference errors. Transaction list queries, scoped connections, and stock metadata connections use this loader. Existing ownership filters, Relay pagination behavior, Redis cache behavior, and financial formulas remain in place.

This removes repeated reads without introducing stored analytics. Account and stock queries still return complete transaction history. Bounded history requests, persistent analytics, background rebuilding, and mutation-driven analytics updates remain subsequent phases.

## Frontend calculation and browser diagnostics

The existing core statistics functions were benchmarked in Node with 10 warmups and 100 samples. Median/p95 calculation time was 0.009/0.013 ms for the 8-row account, 0.124/0.149 ms for the 144-row account, and 0.300/0.416 ms for XEQT. These measure the existing statistics helpers, not the complete component/chart pipeline or browser rendering.

Add `performance=1` to an account or stock page URL to log timings in browser developer tools. Logs are silent by default and contain phase, label, row count where available, and duration only. They do not log GraphQL variables or financial records.

- `network`: elapsed Apollo network-link time through receipt of a result, including HTTP and server work; cached Apollo results do not pass through it.
- `calculation`: the account/stock history-and-statistics effect, plus account card values separately.
- `render-to-layout`: component render through its layout effect, including subtree work/commit; not browser paint or exclusive React CPU time.

The production Docker stack was rebuilt and the XEQT page was verified to load its statistics, distribution, history charts, and 250-row transaction table. One browser reload measured the `transaction_stock` network operation at 222 ms, the complete stock history/statistics effect at 5.9 ms, and individual stock-details render-to-layout measurements at 10.5–49.7 ms. These browser observations include HTTP and actual UI work and are separate from the cold host GraphQL benchmark above; they are not a total page-load or paint measurement.

## Reproduce

From the backend repository, using an environment with its dependencies and database configuration:

```sh
python scripts/benchmark_transaction_queries.py --profile "Profile Name" \
  --output /tmp/transaction-performance.json \
  --responses /tmp/transaction-responses.json
```

The representative scopes currently target FHSA TD Multi-Holding, FHSA Wealthsimple, and XEQT. `--frontend` overrides the default sibling frontend directory. Response snapshots contain portfolio data; keep them local and outside version control. Summary output contains counts and timings only.

From the frontend repository:

```sh
node scripts/benchmark-statistics.cjs /tmp/transaction-responses.json
```

## Regression verification

168 backend tests passed, including exact GraphQL output comparison and bounded reference reads for a large transaction set, nested GIC/spinoff references, shared identity, ordering, empty results, dangling references, cycles, and stock metadata pagination. 29 focused frontend tests passed across diagnostics, refresh, account navigation, and stock statistics; TypeScript checking passed. The production compose rebuild completed with backend, frontend, and Redis healthy.
