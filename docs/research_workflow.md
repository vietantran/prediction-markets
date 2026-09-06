# US midterm, policy and macro research workflow

The scripts build an auditable research universe and identify contracts worth investigating. Their output is a set of research leads with keyword evidence, prices, timestamps and data-quality flags. It does not establish which issues are gaining traction with voters or predict stock returns by itself.

## Start with the 2026 midterms

Run a bounded initial discovery and inspect the run's `coverage.json`, `manifest.json`, `markets.csv` and `research_report.json`:

```powershell
python -m pmresearch discover --topic midterms_2026 --max-pages 3 --out data/midterms
```

The command covers relevant 2026 Senate/House races and congressional control, with gubernatorial and redistricting markets as related state-policy channels. US politics markets outside that election cycle are classified separately. An undated reference to “midterms” uses the configured 2026 research focus; an explicit different year does not.

Discovery is bounded by search pagination and the selected-series limit. Increase `--max-pages` and `--max-series` when the manifest reports a cap. Use `--catalog-scan` periodically to sample listings beyond search vocabulary. For a known Kalshi series, use repeated `--kalshi-series` arguments; a manually chosen series still needs enough market/event context to pass the topic filter.

Save and review a smaller research universe before downloading larger datasets. Filtering a CSV is convenient for inspection; collection expects the corresponding normalized JSONL rows. The `market_id`, Polymarket `condition_id`/`token_id`, and Kalshi `series_id` fields provide the joins needed for downstream API requests.

```powershell
python -m pmresearch collect --universe data/midterms/<RUN>/markets.jsonl --datasets books history trades --max-markets 20 --start 2026-09-01T00:00:00Z --end 2026-09-07T00:00:00Z --out data/extracts
```

Keep dates and run paths explicit. A collection run preserves platform-native history and book payloads. It does not turn old historical trades into contemporaneous order books, and it does not replace a new discovery snapshot for the comparison command.

## Extend the universe to issues with possible equity implications

The editable taxonomy is in `config/topics.json`; the installed package's default copy is `pmresearch/topics.json`. Pass `--topics-config config/topics.json` to use an edited local file. Run `python -m pmresearch topics` to see topic IDs and seeds. `--topic` is repeatable.

| Topic ID | What to investigate | Illustrative exposure hypotheses |
| --- | --- | --- |
| `data_centers_ai_power` | Data center bans or moratoria, zoning and permits, water demand, power bills, grid connections | Data center REITs, hyperscalers, semiconductors, utilities and electrical equipment |
| `energy_climate` | Permitting, fossil production, LNG exports, renewables, nuclear power, EV rules | Oil and gas, utilities, renewables, autos and industrials |
| `trade_tariffs` | Tariff levels, exemptions, trade agreements, export restrictions | Retail, autos, semiconductors, manufacturers and agriculture |
| `immigration_labor` | Visa restrictions, deportation policy, wage rules and workforce availability | Agriculture, construction, hospitality, staffing and technology services |
| `fiscal_taxes_debt` | Corporate taxes, spending, reconciliation, shutdowns and debt limits | Broad equities, government contractors, defense and banks |
| `healthcare_drug_pricing` | Coverage/subsidies, Medicaid, Medicare, drug price rules and PBMs | Managed care, hospitals, pharmaceuticals and intermediaries |
| `financial_regulation` | Bank capital, CFPB, crypto legislation, stablecoins and consumer credit | Banks, exchanges, payment companies and consumer finance |
| `antitrust_tech` | Merger enforcement, platform remedies and AI/platform rules | Internet platforms, software, media and advertising |
| `fed_monetary_policy` | FOMC decisions, chair/leadership, balance sheet policy and independence | Banks, real estate, utilities and equities sensitive to discount rates |
| `us_macro` | Inflation, growth, labor, activity and housing releases | Broad equities, consumers, banks and homebuilders |
| `us_politics` | Executive action, legislation, courts, governance and other elections | Depends on the actual policy and firms affected |

These are mapping hypotheses to guide investigation. An issue's appearance in a market title is not evidence of support, opposition, electoral importance or a policy's eventual effect on earnings. Geography matters: a municipal data center moratorium can directly affect permits even when federal congressional control changes little.

```powershell
python -m pmresearch discover --topic data_centers_ai_power --topic energy_climate --topic antitrust_tech --catalog-scan --out data/issues
python -m pmresearch discover --topic fed_monetary_policy --topic us_macro --out data/macro
```

Generic terms such as “GDP” and “interest rates” need US context. Titles, event metadata, series titles and verified US statistical settlement sources can supply it. This reduces foreign-market false positives but can miss poorly described markets. Inspect `candidates.jsonl`, including rejected candidates, to tune vocabulary. The word-boundary matching prevents “Fed” from matching “Federer”. Foreign-only elections and sports are excluded by the default classifier.

## Identify changes without mistaking coverage changes for traction

Repeat discovery with the same configuration and compare two actual snapshots:

```powershell
python -m pmresearch analyze --current data/midterms/<NEW_RUN>/markets.jsonl --previous data/midterms/<OLD_RUN>/markets.jsonl --out data/comparisons
```

The report compares identical platform/market/outcome identities, requires increasing timezone-aware observation timestamps, and records the source snapshot time ranges. A rise from 40% to 46% is reported as **+6 percentage points**, not +6% or +15 percentage points. It skips comparisons when token identity, title/rules or the price source changed. Duplicate identities are flagged rather than used to generate changes. The CLI also attaches available source manifests so collection limits and errors remain visible.

The watchlist prioritizes midterm exposure and large absolute price changes, and shows issue keyword evidence plus possible sector exposure. New/disappeared rows describe *collected coverage*. They may result from a different query, pagination cap, API failure, listing or resolution; they are not automatically new markets or growing voter attention. Topic counts count distinct venue markets rather than both Yes and No outcome rows. Separate markets resolving the same question can still inflate counts, and cross-venue duplicates need manual review.

To assess whether an issue is gaining investment relevance, investigate several distinct observations:

1. **Persistence in market data.** Check whether a price change persists across snapshots, trades and executable bids/asks. Compare a consistent contract universe and equivalent observation windows. Inspect spreads, trade size and activity before attaching importance to a move.
2. **Electoral relevance.** Establish which races or jurisdictions the issue affects and whether campaign platforms, ballot measures or legislative proposals substantiate the market wording. Prediction markets cover only a subset of potential issues.
3. **Policy transmission.** Identify whether the mechanism is local permitting, state law, congressional legislation, an agency rule, a court decision or executive action. Distinguish winning an election from passing and implementing a policy.
4. **Equity transmission.** Map the proposal to named operating assets, affected revenue/costs, capital spending, financing and timing. Check how much is already reflected in equity prices and company guidance before developing a trade thesis.

For example, a local data center ban contract and a congressional race contract are separate observations. Review the ban's legal scope, grandfathering, utility cost allocation and election timetable before linking it to a utility or data center operator. Do not multiply their probabilities as though they were independent or infer that a named candidate supports the ban from a shared topic label. Comments and wallet activity may help explain market participation, but neither is a representative survey of voters.

## Price and volume interpretation

Polymarket's Gamma response maps outcome names and prices by position; the normalizer decodes both ordinary arrays and JSON-encoded arrays, retains token IDs as strings, and flags malformed lengths. Its `probability` is labelled `gamma_outcome_price`; it is a displayed estimate, not necessarily a price at which a position can be traded. The official description notes that display behavior can switch between midpoint and last trade when the spread is wide. Token-specific CLOB books are the source for executable depth. [Gamma market data](https://docs.polymarket.com/market-data/overview) and [prices and order books](https://docs.polymarket.com/concepts/prices-orderbook) explain these conventions.

For a standard Polymarket `[Yes, No]` market, the normalizer can retain Gamma's primary Yes top-of-book fields. It never copies those quotes to the No token. A valid two-sided primary book is a fallback if the outcome estimate is unavailable. Missing, one-sided and crossed books have no invented midpoint. API zero values remain zero; missing values remain null.

The fallback also excludes inactive, closed, archived or disabled markets and unverified boundary quotes with bid 0 or ask 1. Live Gamma metadata can contain placeholder outcomes with those boundary quotes and no outcome price, even when `acceptingOrders=true`; they remain unpriced and flagged. `ready` and `funded` alone are not reliable exclusion criteria because active markets can also report them as false.

Kalshi's normalizer emits a Yes row per binary market. It prioritizes a valid bid/ask midpoint, otherwise a separately labelled last trade that may be stale. A settled result is labelled explicitly. Scalar contracts are flagged because their prices do not have the same binary-probability interpretation. `_dollars` fields are dollar prices, legacy fields are cents, and `_fp` fields retain fractional contract counts. The code determines units by field names, never by numerical size; a legacy price of `1` is one cent while `yes_bid_dollars="1"` is one dollar. [Kalshi's fixed-point migration guide](https://docs.kalshi.com/getting_started/fixed_point_migration) documents the formats.

If only Kalshi's opposite-side bid is available, Yes ask = 1 − No bid, subject to the side actually being present. A quoted size of zero marks the side absent. The public book is bid-based; it does not return a standalone ask ladder. [Kalshi order book responses](https://docs.kalshi.com/getting_started/orderbook_responses) documents this relationship. If no sizes are supplied, the normalizer cannot establish executable quantity from top prices alone.

Polymarket normalized volume is labelled USD notional; Kalshi volume is contract count. Neither is an interchangeable cross-venue liquidity measure. A row's cumulative volume is not daily turnover, voter interest or unique trader count, and repeating market volume on outcome rows does not double market turnover. Kalshi's deprecated `liquidity` fields return zero; normalized liquidity is null and flagged, while raw data remains available. Use current order-book depth instead. [Kalshi's changelog](https://docs.kalshi.com/changelog) records the deprecation.

The default watchlist marks a spread of at least 0.10 (10 probability points) as wide and cumulative volume below 1,000 in each venue's own units as low. These are editable research heuristics through `analyze(..., wide_spread=..., thin_volume={...})`; they do not calibrate confidence or equate dollars to contracts. A high-volume contract can still have a poor current book. Discovery observation time records collection time; it does not prove the underlying last trade or displayed price is fresh.

## Fed distributions and cross-venue comparisons

Keep each Fed meeting and each release's definition separate. Exact outcome buckets for one meeting may form a distribution only after confirming that they are mutually exclusive, exhaustive, contemporaneous and share resolution rules. Threshold contracts such as “rate above X” overlap and cannot be added. A rate expectation needs the verified rate associated with each bucket; a vague “cut” probability alone does not specify the expected rate or number of cuts. The provided report deliberately leaves these event-specific calculations to a researched, explicit mapping.

Likewise, compare Polymarket and Kalshi contracts only after reading both definitions: outcome orientation, deadline/time zone, source, recount or revision handling, resolution contingencies and settlement timing. A price difference alone does not prove arbitrage. Fees, executable depth and legal/platform access affect whether a comparison is useful. The toolkit preserves these distinctions and does not synthesize a combined topic probability.
