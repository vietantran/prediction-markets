# Midterm issue research: runbook and data definitions

This implementation screens Kalshi's public category-union census, builds an explicit research cohort, collects its missing history, combines it with dated public evidence and company/price data, and renders a cited report with original charts and Excel tables.

The published case study is frozen at **2026-09-08 15:07 UTC**. US equity observations end at the completed **September 4** session. The narrative is specific to that date and deliberately refuses a different cutoff without analyst review.

## Delivered case study

- [Research report (Markdown)](../research/midterms_2026_09_08/Midterms_Issue_Momentum_Research.md)
- [PDF report](../research/midterms_2026_09_08/Midterms_Issue_Momentum_Research.pdf)
- [HTML report](../research/midterms_2026_09_08/Midterms_Issue_Momentum_Research.html)
- [Excel research tables](../research/midterms_2026_09_08/Midterms_Research_Data.xlsx)
- [Validation manifest](../research/midterms_2026_09_08/validation.json)
- [Public evidence registry](../config/midterm_public_evidence.json)

The cohort has 6,032 contracts: 1,637 issue-target mention contracts, 1,228 election/control contracts, 918 policy/proxy contracts, and 2,249 contracts in the named macro panel. Every contract has a common-window collection artifact. There are 1,276,909 retained hourly observations across 4,856 contracts. The 1,176 empty histories include 1,108 settlements before the common window and 68 other API-empty cases with timing/availability limitations. Unusable quotes remain explicit.

The local history workbooks are indexed by `data/midterm_research_20260908/excel_history/INDEX.xlsx`. The index links to hourly shards of at most 250,000 rows and daily summaries, and includes all contract metadata, coverage reasons, UTC creation/settlement times, source-artifact hashes and field definitions. Open interest and payout-face exposure are included where returned. These large files remain outside Git; the code regenerates them from cached/downloaded public histories. `export_manifest.json` must say `complete` before using an export.

This is distinct from the larger 532,847-record census and ongoing full-category historical extraction. Exact historical membership of all website subcategory paths is not reconstructible from API metadata alone. Private trader positions, complete large-player holdings, and equity investor crowding are not available through this public-data workflow.

## Installation and offline reproduction

From the repository root, using Python 3.11 or later:

```console
python -m pip install -e ".[research,dev]"
python scripts/run_midterm_study.py --offline
python -m pytest -q
python -m ruff check pmresearch scripts tests
```

The offline command reads the committed `research/midterms_2026_09_08/inputs` snapshot. It rebuilds all nine charts, CSV exhibit tables, Excel workbook, Markdown, HTML, PDF and validation output without API requests. PDF/Excel container metadata can vary between builds; analytical values and figure inputs should reconcile.

No Kalshi key, SEC account, paid data or proprietary holdings are required. Raw full-census/history downloads are intentionally not stored in Git. The smaller derived research snapshot and original report are included for review and reproducibility.

## Collect and analyze again

An existing frozen full-census cache can be used without downloading its metadata again:

```console
python scripts/run_midterm_study.py --source-root data/kalshi_full_20260908 --run-root data/midterm_research_reproduction --report-out research/midterms_2026_09_08
```

To start with no census cache, add `--collect-census`. This is a large public API enumeration; it retains cursor pages and resumes completed series. Use a separate source directory for a different frozen configuration. `--rps` is an aggregate rate budget for each collector process, not permission to multiply request load across processes.

The pipeline performs these steps:

1. Validate or create the frozen common and eligibility windows; collect/resume the public category-union census if requested.
2. Screen every census record with the explicit topic, geography, cycle and creation/open-time rules. Preserve the excluded inventory and counts.
3. Select a named cohort independent of observed price changes or equity returns. High-frequency oil-direction ladders do not become an electoral-attention proxy merely because they list frequently.
4. Reuse complete matching-window source histories and collect missing hourly/daily histories into an isolated supplement. Changed cutoffs or cohort fingerprints require a new supplement.
5. Compute same-contract changes and matched mention cohorts, with separate coverage, quote-quality and participation diagnostics.
6. Export the retained common-window hourly observations and UTC daily summaries to typed Excel shards, checking every contract against analytical coverage.
7. Fetch the declared 25-stock/11-ETF price panel and eight-issuer SEC fact panel, retaining raw responses and provenance. Withhold unavailable/stale metrics.
8. Build the report from frozen derived tables and the dated, manually verified public-evidence registry.

The evidence registry and written investment judgments are analyst-reviewed inputs, not outputs of an automated news classifier. A new date or changed policy regime requires renewed evidence and narrative review. Re-running against current APIs is not a guarantee of reconstructing a point-in-time historical market listing universe.

The modules can also be used individually:

```console
python -m pmresearch.issue_research --source-root data/kalshi_full_20260908 --out data/my_study/kalshi_analysis --stage inventory
python scripts/select_midterm_cohort.py --index data/my_study/kalshi_analysis/market_index.csv --out data/my_study/cohort.csv --asof 2026-09-08T15:07:00Z
python scripts/collect_midterm_research.py --source-root data/kalshi_full_20260908 --inventory data/my_study/cohort.csv --out data/my_study/kalshi_supplement
python -m pmresearch.issue_research --source-root data/kalshi_full_20260908 --supplement-root data/my_study/kalshi_supplement --out data/my_study/kalshi_analysis --stage analyze --market-list data/my_study/cohort.csv
python scripts/export_midterm_history.py --source-root data/kalshi_full_20260908 --supplement-root data/my_study/kalshi_supplement --analysis-root data/my_study/kalshi_analysis --out data/my_study/excel_history
python -m pmresearch.equity_research --out data/my_study/equities --asof 2026-09-08T15:07:00Z --start 2026-07-08
python scripts/build_midterm_report.py --run-root data/my_study --out research/my_study
```

## Definitions and interpretation

| Field or output | Definition |
|---|---|
| Probability | Valid two-sided USD midpoint divided by the contract's stated face value. It is a market price, not a calibrated physical probability. |
| Spread | Ask minus bid divided by face value. It is not a statistical confidence interval. |
| Price change | Current minus earlier quote for the identical contract, expressed in percentage points. Earlier quotes must meet the age/spread checks. |
| Mention observation | Expected occurrence of a literal target at a fixed lead to the earliest known event-contract close, an explicitly retrospective event-time proxy. |
| Matched cohort | Same series, speaker/company and normalized target, at least three distinct events in each comparison period. At least two cohorts are required for an issue-wide summary. |
| Comparison periods | Latest 14 versus preceding 28 completed UTC days. Event rates are reported per day where denominators differ. |
| Daily volume | Sum of observed interval contract counts. Partial boundary intervals are flagged and are not prorated using invented trades. |
| Open interest | Last observed outstanding-contract stock per market/day. It is not summed through time or interpreted as net directional positioning. |
| Face exposure | OI multiplied by contractual face amount; not capital invested, cash paid, turnover, or an equity-style market capitalization. |
| Equity return | Ratio of current vendor adjusted closes minus one, over common completed sessions. Sector excess is a simple arithmetic difference. |
| Financial TTM | Identical-tag annual plus current fiscal YTD minus comparable prior fiscal YTD where available; reconstructed EPS is approximate when diluted share counts change. |
| Cash FCF | Operating cash flow less cash PPE purchases; excludes finance-lease additions and acquisitions. Not calculated as an industrial-style metric for a bank. |
| Valuation stress | Explicit hypothetical exit multiple and EPS growth arithmetic, not a fair-value estimate, consensus forecast or scenario probability. |

Mention quotes are not actual transcripts or voter salience. Poll questions, populations and fieldwork are kept distinct. Election-control markets are not mechanically converted into policy probabilities. The February 2027 joint control contracts retain their organizational-control scope and are not normalized into artificial weights.

## Scope files and code

- `config/midterm_issues.json`: transparent issue dictionary and quality thresholds.
- `config/midterm_analysis_scope.json`: named research universe rules.
- `config/midterm_equity_universe.json`: declared equities, economic cohorts and benchmarks.
- `config/midterm_public_evidence.json`: dated primary-source observations, counterevidence and uncertainty.
- `pmresearch/issue_research.py`: inventory, matched-cohort and market-price analysis.
- `pmresearch/equity_research.py`: public prices, SEC facts, return comparisons and financial proxies.
- `pmresearch/research_report.py`: typed, formula-safe Excel and portable HTML/PDF rendering.
- `scripts/build_midterm_report.py` and `research/templates/midterms_20260908.md`: original exhibit generation and reviewed investment interpretation.

Tests cover unit conventions, invalid/missing quotes, cycle selection, matching, frozen-cache invalidation, completed equity sessions, financial staleness, entity continuity, numeric Excel output and formula-safe text. Coverage audits and reviewer findings accompany the report. This remains descriptive research rather than an out-of-sample strategy backtest.

Release validation: 106 tests and 18 subtests passed; Ruff and the staged-diff check passed. An independent audit reconciled all 6,032 source artifacts, 494 reported Kalshi numerical comparisons and 32 final coverage checks. A clean export of the staged Git snapshot rebuilt the report offline, with identical hashes for all 15 analytical exhibit CSVs, the Markdown report and validation JSON. The PDF was visually reviewed and checked for content extending beyond page boundaries. `.gitattributes` preserves frozen artifact bytes across checkout platforms.

## Broader extraction and historical extension

The separate `kalshi_full_census.py`, `kalshi_full_candles.py`, `kalshi_full_books.py`, `kalshi_full_finalize.py`, `kalshi_full_export.py` and QA helpers implement the larger category extraction. Its completion must be determined from its own manifests, not from this study's success.

`collect_midterm_research.py --presettlement` additionally requires the final two calendar months before settlement for eligible contracts settled before the common window. Use a new supplement directory when adding that scope. Those histories are stored separately under `candles_presettlement`; they must not be spliced into a current-period attention trend.
