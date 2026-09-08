# Midterm issues, political pricing and equity implications

Complete research blueprint and implementation specification · Frozen information cutoff: **8 September 2026, 15:07 UTC**.

## 1. Mandate and decision standard

Serve a US long-only fundamental equity portfolio within a global MSCI ACWI investment framework over 12–24 months. Shorts and options can be considered as hedges, but this research does not execute trades. Use free public Kalshi data, adjusted equity prices, SEC/company financial information and dated primary public evidence. Do not assume proprietary portfolio weights or consensus forecasts.

The three research questions are:

1. Which issues are gaining attention in prediction markets, and which are demonstrably rising in the electoral agenda?
2. Where do electoral probabilities, policy developments and equity prices imply different narratives?
3. Which state-level issues are spreading, which attract concern across parties, and what return exposures are observable?

The investment chain is **public concern → electoral incentives → institutional authority → implementable policy → company earnings/cash flow → valuation → portfolio decision**. Every arrow requires separate evidence. Contract creation is not voter concern; a governor probability is not a policy enactment probability; a contemporaneous equity association is not a causal return forecast.

## 2. Architecture and deliverables

This programme lives entirely in `research_programmes/midterm_issue_monitor/`. It has no imports from the earlier research package. Frozen normalized inputs were prepared from the previously audited public extraction. Raw-source preparation remains available when the parent repository's extraction caches exist. Compact inputs, configurations, all analysis code, statistical outputs, report source, single-file HTML, charts, source registry and tests are checked in together.

| Layer | Implementation | Output |
|---|---|---|
| Source normalization | `data.py` | Daily market panels, equity-session quotes, per-source SHA256 |
| Public executions | `trades.py` | Paginated 28-day tape for 13 scenarios, trade sizes/direction/concentration |
| Issue activity | `attention.py` | Creation cadence, continuing-family activity, OI decomposition, launch comparisons |
| Politics and diffusion | `political.py` | Comparable poll trends, cross-party evidence, state remedies, joint institutional scenarios |
| Return sensitivities | `sensitivity.py` | Raw and market-conditioned regressions, uncertainty, robustness, holdout diagnostics |
| Company underwriting | `fundamentals.py` | Financial exposures and explicit 12/24-month earnings/multiple stress cases |
| Integration | `reporting.py`, `run.py` | Comprehensive HTML report, charts, tables, executable reproduction |

The historical report is a completed, reproducible study, not a claim that an unattended live production service is deployed. The original much larger all-category historical extraction continues separately; this report's completed cohort must not be described as that complete census.

## 3. Time, universe and provenance

### Frozen windows

- Market eligibility and original category request: active, or settled within three months of the cutoff, subject to the source extraction's rules.
- Common hourly observation request: 8 July 2026 15:07 UTC to 8 September 2026 15:07 UTC.
- Research core: 6,032 contracts across elections, policy, macro and mentions; 1,276,909 normalized hourly rows.
- Wider issue-classified creation inventory: 61,018 contracts. This is not an exact historical snapshot of website category membership, and does not include every contract in the larger extraction.
- Attention estimation: full UTC days, 9 July–7 September. Latest 14 days are 25 August–7 September; the comparator is the preceding nonoverlapping 28 days. Seven-day alternatives use the corresponding preceding 28 days.
- OI decomposition: fresh observed values at 24 August and 7 September, maximum two-day age. Missing endpoints remain missing.
- Public tape: 11 August inclusive to 8 September exclusive, 13 explicitly specified contracts; every pagination cursor exhausted.
- Equity history: 43 daily dates, 8 July–4 September, hence at most 42 adjacent returns. No unfinished US session is used.
- Separate pre-settlement price history from the original extraction is not mixed into this study's common calendar panel.

Prices and financial data were retrieved after the information cutoff. Public announcements and financial periods are required to be available before it, but the datasets are not a historical archive of all vendor revisions. Adjusted equity prices are the retrieval-date vendor series. This study is exploratory and not a point-in-time investable backtest.

### Provenance and normalization

Preserve ticker, event, series, category, channel, issue tags, lifecycle timestamps, rule text and face value. ISO settlement timestamps are parsed explicitly, including mixed fractional-second formats. Record request windows, retrieval time, source tier and hashes. Refuse wrong identities, unsuccessful artifacts or inconsistent source bounds.

Prefer explicit dollar fields. Legacy live plain price OHLC is cents; historical plain OHLC is dollars. Normalize by actual contract face value. Reject crossed, missing or out-of-range two-sided quotes. Do not use last trade as if it were a current executable midpoint. Hour-end bars at or before opening and at/after settlement are excluded; boundary-partial bars are retained and flagged. Source row counts reconcile per contract to the audited inventory.

Daily intervals use the date of `bar_end − 1 second` so an hour ending at midnight belongs to the preceding day. Preserve missing volume and OI; zero is valid only if explicitly observed. Record observed volume-hour counts and partial boundaries. A daily total is the sum of available hourly volume, not an invented full-day estimate.

For equity regressions, take the latest preceding hourly bar at 16:00 New York time on each observed SPY date. Require at most three hours of bar age and valid quotes at both ends of a consecutive equity-session interval. The underlying quote's last update time is unavailable: a fresh bar can carry an old quote. This unresolved risk is reported.

## 4. Taxonomy and issue detection

Use an interpretable, versioned dictionary for affordability/inflation, energy/Iran, electricity/data centers, AI/jobs/regulation, healthcare, housing, immigration, trade/tariffs, taxes/fiscal, jobs/growth and the Fed. A contract may have several issue tags; consequently issue totals overlap and must not be added into an exchange-wide total.

Keep **mentions, policy, macro and electoral outcomes separate**. A repeated gas-price threshold is a macro distribution contract; a statement saying “gas” is a mention contract; a gasoline-tax cut is a policy contract. Identical words do not make identical economic events. Report channel and economic proposition beside every statistic.

### Creation and product-cadence diagnostics

For each issue/channel/window calculate new contracts, distinct new events, and archive-first-observed series. Compare event rates per day across unequal windows. Count a multi-threshold event once in the event measure. Display contributions by series, especially hourly, 15-minute, daily and threshold ladders. Archive-first-observed does not prove a series was newly created across Kalshi; left-truncation is explicit.

Creation is a **supply diagnostic**. It can alert an analyst to a new tradable proposition, but must not independently raise an electoral-salience score. A product launch may simply be a platform listing decision. Show novel policy events separately from recurring market issuance.

### Continuing-contract trading activity

For a given recent/comparator pair, hold the contract set fixed to contracts with at least 80% observed-day coverage in both periods. Recalculate at 70% and 90%. Within each series family, calculate daily traded contracts per covered contract to limit mechanical ladder-size effects. Summarize changes across families with equal family weight, a median volume-rate ratio, a log(1+activity) change, breadth and persistence. Do not let one liquid contract family define an entire issue.

Require at least three qualifying families before a broad activity classification. Display the largest family's volume share. A concentrated or coverage-unstable result is labeled accordingly even if its headline growth is large. Robust z-scores use medians/MAD where available; zero-MAD or short-history cases stay missing rather than generating extreme scores.

Interpret repeated-event adoption separately: compare the first two **full UTC days after creation**, only with complete coverage, and at least three events per family in each period. This deliberately does not claim a speech-aligned 48-hour observation. Launch selection, right censoring and different event importance remain confounders. Report days-to-resolution or event-calendar concentration where the repeated-event sample permits it.

### OI accounting

Let C denote contracts observed at both endpoint dates, E entries into the observed panel and X exits. Then:

`OI_latest − OI_prior = Σ_C(OI_latest − OI_prior) + Σ_E OI_latest − Σ_X OI_prior`.

Verify the identity numerically. Split entrants with corroborated new-listing dates from previously existing contracts gaining coverage; split exits with observed settlement from unexplained coverage loss. The continuing-contract percentage uses continuing prior OI as its denominator. OI is outstanding contracts, not net bullish positions or a cash-inflow estimate. Every matched YES exposure has an opposing NO exposure. Multiplying OI by contract face gives maximum contractual payout scale, not invested capital or institutional assets.

## 5. Trading direction, concentration and liquidity

Use public `GET /markets/trades`, and historical trades when the documented cutoff requires it. Query the cutoff rather than assuming a fixed retention period. Request 1,000 rows per page, cache each request by signature, follow opaque cursors to exhaustion, reject repeated cursors and deduplicate by trade ID. Overlap requested time boundaries and enforce the desired half-open interval client-side. Retry rate limits and transient errors with bounded backoff.

Use the canonical `taker_outcome_side`; legacy fallback is marked. For non-block executions with known direction:

`YES-taker share = YES-initiated contracts / all direction-classified contracts`.

`Taker imbalance = (YES-initiated − NO-initiated contracts) / classified contracts`.

Show the denominator, execution count, active dates, median/largest trade, top-five execution share and block/unknown-block shares. All are execution statistics. One investor can split orders; one large print need not identify an institution; taker direction can represent opening or closing risk. An OI increase plus YES initiation is consistent with demand for YES exposure but does not identify ownership or information quality.

Reconcile public tape quantities with candle volume on full, nonpartial 24-hour observed days. Preserve fractional contract quantities. Face-traded notional is distinct from taker premium: repeated turnover can count the same economic risk several times. Private positions, trader identity, market-wide average entry prices and historical book depth are unavailable and are never fabricated.

Liquidity gates are analyst diagnostics, not calibrated laws: fewer than 100 prints over 28 days, fewer than 10 active dates, or more than half of volume in five prints warrants a numerical-sizing veto. Show raw estimates even when vetoed, with the reason, and do not remove them after seeing their statistical significance.

## 6. Electoral and institutional research before equities

### Public opinion

Use dated primary polls and original questionnaires. Compare only compatible pollster, question, response alternatives and population. Keep prompted cost concern, open-ended priority, policy approval, registered-voter intention and likely-voter intention separate. Display both level and change. Without comparable survey design and earlier-wave uncertainty, report percentage-point changes, not invented significance.

Test counterevidence explicitly: high concern can be stable; an issue can attract widespread opposition yet rank low as the most important issue; national concern can coexist with a shrinking slice of campaign mentions. Do not infer voter motivation from a governor contract moving after a primary.

### Elections and policy feasibility

Review House, Senate and competitive governors separately. Hold the presidency fixed through the midterms. Distinguish election-win contracts from February composition contracts and preserve their different resolutions. Use direct joint control markets where available; report bid/ask inconsistency and do not manufacture independent chamber probabilities or silently normalize incoherent midpoint sums.

For every policy contract document the deadline, exact legal trigger and institution. A contract ending by 1 January 2027 cannot price enactment by the Congress seated afterward. Congressional majorities affect oversight, appropriations and legislation, but vetoes, Senate procedures, courts, agency powers and state authority constrain implementation. No party label directly determines the Fed.

### State-to-national diffusion

Build a small evidence ledger with state, dated action, authority, remedy, status, election link, public support and company cash-flow channel. Separate binding statewide construction bans, utility tariffs, executive permitting conditions, advisory bodies, municipal actions and sector-specific AI obligations. Do not equate all of them to Kalshi's moratorium-count definition.

Cross-party concern is demonstrated only when observed party subgroups respond to the same question. It does not establish support for one legislative remedy. Nationalization requires comparable evidence in multiple jurisdictions or a federal action; the five deliberately selected cases are not a 50-state prevalence estimate. Crucially, ratepayer protection can enable projects if clearer contracts and customer-funded infrastructure remove obstacles to connection.

## 7. Equity return sensitivities

### Basket construction

Use fixed, transparent sector ETFs and small thematic stock baskets. Recompute simple daily returns from adjusted closes; do not use a return field whose first observation includes an out-of-window price. Within each complete basket, equal weight constituent returns each day. Do not reweight around missing members. Label one-stock proxies, daily rebalancing and analyst selection made with knowledge of the period's performance. Three arithmetic return contrasts are included in the full testing family and are not funded portfolio returns.

### Estimation

For each scenario k, define `x_t = (p_t − p_(t−1)) / 0.10` using exact adjacent equity dates. Fit:

`r_b,t = a + β_b,k x_t + e_t` (raw association)

`r_b,t = a + γ_b r_SPY,t + β_b,k x_t + e_t` (market-conditioned association).

The coefficient is a decimal return associated with a **10 percentage-point** probability change. Ten points is a reporting scale, not permission to extrapolate outside observed support. Market conditioning can remove part of a real policy transmission channel, so both specifications matter. Neither identifies causality; common news, rates, oil, earnings and reverse information flows remain.

Require at least 20 valid adjacent changes and five dates with a probability move of at least one point. Count actual dates, not contracts or hourly rows, as the estimation sample. The end-2027 Fed path and some policy contracts may fail these gates even when their snapshot price is interesting.

### Uncertainty and robustness

- HC3 heteroskedasticity-robust and three-equity-session-lag HAC standard errors, with missing-session structure preserved.
- Baseline 500-draw, five-session moving-block pairs bootstrap; retain missingness and fixed seed.
- Omit the largest probability-move date and compare the sign and magnitude.
- Repeat with maximum spreads of 5, 10 and 15 probability points.
- Report probability range, meaningful shock dates, and moves exceeding `max(3pp, sum of endpoint spreads)`.
- Benjamini–Hochberg across the full estimable primary baseline family, including the paired contrasts. Context scenarios remain separately labeled. Dependent tests and small samples limit formal discovery claims.
- Compare HAC and HC3 results; conservative uncertainty and strict-spread failures take priority over an attractive p-value.
- Apply the execution-liquidity veto separately; do not select a significant thin market and call it an investable factor.

Chronological train/holdout residuals are exploratory discrepancies, not proven market mispricing. Algorithmic largest-odds-move tables are not independently selected event studies. Join dated primary releases/actions, identifying date-only time uncertainty and overlapping news. Do not assume unlisted dates were news-free.

For a reliable factor, a portfolio approximation would be `Σ_i w_i β_i,k × Δp_k/0.10`, with uncertainty and covariance. No portfolio weights are supplied, so no actual portfolio loss or optimal hedge ratio is reported. Correlated scenario coefficients must not be summed into an independent shock stack. The present short sample does not support an optimized multi-factor hedge.

## 8. Fundamental 12–24 month underwriting

Use matching-period SEC/company facts with source URLs and filing dates. Reconstruct TTM only from compatible annual/YTD periods, maintain reorganization and share-split continuity, and withhold stale observations. Separate reported GAAP EPS, adjusted management metrics and analyst normalizations. MSFT's starting EPS removes the disclosed $0.67 OpenAI investment gain; the adjustment is explicit. AEP stale financial inputs are excluded from valuation. Bank FCF is not treated as industrial CFO minus capex.

For seven issuers, configure lower/central/upper earnings-growth and exit-multiple cases with an economic rationale. They are analyst-designed stress assumptions, not consensus or statistically estimated probabilities. For horizon T:

`Terminal price = starting EPS × (1 + EPS CAGR)^T × exit P/E`.

`Price return = terminal price / observed entry price − 1`.

`EPS CAGR required for annual price hurdle h = [entry price × (1+h)^T / (starting EPS × exit P/E)]^(1/T) − 1`.

`Maximum entry price for hurdle h = terminal price / (1+h)^T`.

Report 12 and 24 months separately. Dividends, taxes and transaction costs are excluded: these are price returns, not total returns. Do not probability-weight overlapping Kalshi outcomes into expected stock prices. Margin sensitivity at constant revenue is `revenue × Δmargin`; for UNH this is group EBIT margin, not its medical cost ratio. For Walmart it is not a tariff-rate shock.

Use scenario ranges to ask what must be true, what current price tolerates and what operational evidence would invalidate the thesis. A rich multiple can offset an attractive thematic outlook; a cheap multiple can reflect cyclical earnings risk. Translate state rules into project delivery, power costs, reimbursement, order conversion, incentive expense or net interest income before adjusting a multiple.

## 9. Portfolio decision rules

The report produces a ranked research/action ledger, not mechanical buy/sell signals. Each row contains observation, competing interpretation, cash-flow mechanism, valuation gate, implementation and invalidation.

- **Stock selection:** compare exposed companies within an industry; favor evidence of funded projects, cost recovery, earnings conversion and a sufficient valuation margin.
- **Rotation:** distinguish energy supply hedges, regulated utility exposure, merchant generation and equipment; do not trade a single “AI power” factor.
- **Risk management:** identify co-exposures to higher rates, input costs and financing needs. Use scenario stress to decide where concentration needs review.
- **Options:** describe put spreads/collars or index/sector protection conditionally. Obtain a current option chain and evaluate carry/skew/liquidity before choosing strikes or claiming cost effectiveness. No option prices are estimated here.

Action requires adequate evidence, company materiality and acceptable valuation together. Thin tape, stale quotes, primary confounding or unstable coefficients mean “research/watch,” not “ignore the issue” and not “trade the estimated beta.”

## 10. Validation, reproducibility and extension

The checked-in runner recomputes all derived outputs and the report from committed inputs. `--collect-trades` refreshes the frozen requested execution window through public endpoints; `--prepare` rebuilds normalized inputs from the audited parent-repository caches. Hash manifests identify the exact input vintage. Tests cover units and boundaries, missingness, OI identities, deduplication, matched returns, robustness statistics and scenario algebra. Browser QA checks desktop/mobile layout, chart rendering, table controls and scenario-calculator behavior.

Do not describe a pipeline that ran as proof its inference is sound. Release validation separately states data completion, statistical reliability and limitations. Never claim the entire original extraction has finished unless its own complete manifests, error counts and final exports pass audit.

For a subsequent live production version, collect fresh metadata/OI/books prospectively, retain delisted items, extend session history and release calendars, validate taxonomy on random samples, and lock analyst decisions before an out-of-sample period. Add rates/oil controls only with adequate observations and a clearly stated causal estimand; controlling for a transmission channel can remove the effect of interest. Validate information value and portfolio turnover net of costs before deploying alerts or trading rules.

## 11. Primary technical references

- [Kalshi public trades and pagination](https://docs.kalshi.com/api-reference/market/get-trades)
- [Kalshi outcome and book direction](https://docs.kalshi.com/getting_started/order_direction)
- [Kalshi historical-data partitioning](https://docs.kalshi.com/getting_started/historical_data)
- [Federal Reserve research on actions, statements and asset prices](https://www.federalreserve.gov/econres/feds/do-actions-speak-louder-than-words-the-response-of-asset-prices-to-monetary-policy-actions-and-statements.htm)

The companion `POLITICAL_EVIDENCE.md`, committed public-evidence JSON and company provenance tables contain the dated primary-source ledger used in the report.
