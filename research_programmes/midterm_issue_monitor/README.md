# Midterm issue research programme

Separate implementation of issue attention, public execution flow, political-to-equity transmission and fundamental valuation research. **No imports or edits to the earlier research code.**

- Open [`report/index.html`](report/index.html) for the self-contained HTML research report.
- Read [`BLUEPRINT.md`](BLUEPRINT.md) for the complete research design, data transformations, models, confidence gates and portfolio decision logic.
- [`POLITICAL_EVIDENCE.md`](POLITICAL_EVIDENCE.md) records primary-source political context.

The study is frozen at **2026-09-08 15:07 UTC**. Prices through September 4 are the last completed equity sessions in the input vintage. It is not a live recommendation at today's prices.

## Reproduce

Python 3.11 or later. From this folder:

```powershell
python -m pip install -r requirements.txt
python run.py
python -m pytest tests -q
```

From the parent repository with its existing environment:

```powershell
.venv/Scripts/python.exe research_programmes/midterm_issue_monitor/run.py
.venv/Scripts/python.exe -m pytest research_programmes/midterm_issue_monitor/tests -q
```

The default run is offline and rebuilds every analytical table and the report from committed public-data snapshots. Numerical outputs use fixed seeds. The run takes several minutes because it includes source-data checks, event-level activity comparisons and block bootstraps. File timestamps are not an investment information timestamp.

```powershell
python run.py --collect-trades
python run.py --prepare
python run.py --report-only
```

- `--collect-trades`: obtain all public executions for the specified 13-contract, 28-day frozen window. No credentials required. It uses the documented historical partition, cursor pagination, request-signature caches, bounded retries and deduplication. Cached API pages remain under ignored `cache/trades/`; the compact normalized tape and page provenance are committed. A different future cutoff requires reviewing the frozen configuration and upstream inputs, not simply relabeling this report's date.
- `--prepare`: rebuild daily/session input panels from the parent repository's audited raw caches at `data/kalshi_full_20260908` and `data/midterm_research_20260908/kalshi_supplement`, with the prior evidence/financial snapshots. These bulk raw caches are not duplicated into the new folder. This stage requires the parent repository's extraction data; ordinary offline reproduction does not.
- `--report-only`: regenerate the HTML and chart files from existing derived outputs without refitting models.

`requirements-tested.txt` records the direct-library versions used for this release. The supported version ranges are in `requirements.txt`. The optional browser QA script requires Playwright and Chromium or Chrome; that does not affect report usage, which requires no network, JavaScript library or CDN.

## Folder map

| Path | Contents |
|---|---|
| `src/midterm_monitor/` | Standalone preparation, public tape, attention, politics, sensitivity, fundamental and report modules |
| `config/` | Transparent scenario list, fixed equity baskets, policy classes and analyst valuation cases |
| `inputs/` | Audited portable panels, 61,018-contract metadata inventory, 6,032-contract research index, public tape, source hashes and public evidence |
| `outputs/` | Political/financial tables, 1,794 sensitivity fits, diagnostics, trade statistics and audit manifests |
| `report/tables/attention/` | Continuing-panel activity, creation cadence, launch/lifecycle comparisons and exact OI accounting |
| `report/charts/` | Ten standalone PNG research figures |
| `report/index.html` | Offline single-file report, searchable sensitivity explorer and editable valuation calculator |
| `assets/` | Report prose, HTML styling and JavaScript source |
| `tests/` | Financial-unit, boundary, accounting, inference and scenario tests |

The report's source Markdown is generated with embedded figures so it is portable; the readable prose templates are under `assets/`. CSV exports preserve full precision even when the report rounds values.

`config/report_input_lock.json` locks the reviewed narrative to its factual inputs and model configurations. A new input vintage or changed scenario/basket configuration requires reviewing the prose and updating that release lock; the renderer refuses to silently reuse historical conclusions with changed data. Gzip inputs are hashed on decompressed CSV content, so compression headers do not affect this check. The on-page calculator is deliberately independent and accepts editable assumptions without changing the research baseline.

## Findings and interpretation

- Raw policy OI fell while continuing-contract OI rose: expirations can reverse an unadjusted signal.
- Fed activity is substantial but concentrated around scheduled decisions. Creation counts frequently reflect recurring listing cadence.
- Data-center opposition crosses parties; tariffs, permitting, advisory processes and bans have different company effects.
- Sparse policy tapes undermine several statistically attractive equity associations. None of 230 specified primary combinations passes all sizing diagnostics.
- Seven company scenarios separate short-run associations from the growth and multiple assumptions required for 12/24-month price returns. They are explicit analyst assumptions, not estimated forecasts; dividends are excluded.

## Coverage and limitations

The research cohort and the 13-contract tape are complete for their stated input windows. **The original much larger all-category extraction is separate and is not claimed complete by this report.** Historical website membership is not reconstructed exactly. Contract topics overlap, first-observed families are left-truncated, quote age differs from underlying quote-update age, and public execution data contain no investor identities or private positions.

The equity sample has only 43 dates. Retrospective basket selection, common-news confounding and sparse probability changes prevent a causal or investable-backtest interpretation. Current vendor-adjusted prices and later-retrieved public metadata are not a complete point-in-time archive. Missing data remain missing. No prices, large positions, market-depth histories, option quotes or policy probabilities are invented.

All source links are preserved with the relevant facts. Kalshi documentation: [trades](https://docs.kalshi.com/api-reference/market/get-trades), [direction](https://docs.kalshi.com/getting_started/order_direction), [historical data](https://docs.kalshi.com/getting_started/historical_data).
