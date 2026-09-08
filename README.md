# US election and macro prediction-market research

Python scripts for discovering and extracting **Polymarket and Kalshi** markets about the 2026 US midterms, Federal Reserve decisions, US macroeconomic releases and US politics. Policy filters include data-center restrictions, AI infrastructure, electricity affordability, energy permitting, tariffs, immigration, taxes, healthcare and financial regulation.

The package is designed for **data collection and research triage**. It preserves raw data, resolution rules, source identifiers and timestamps. It does not place orders. A new policy market or keyword match is a research lead; it does not establish that an issue is gaining voter support or will affect a stock price.

## Start here

For the implemented investment study, open the [September 2026 midterms research report](research/midterms_2026_09_08/Midterms_Issue_Momentum_Research.md), its [PDF](research/midterms_2026_09_08/Midterms_Issue_Momentum_Research.pdf), or the [Excel research tables](research/midterms_2026_09_08/Midterms_Research_Data.xlsx). It analyzes a 6,032-contract Kalshi cohort, 25 stocks and 11 ETFs, with nine original charts, dated primary evidence and explicit coverage limits. The [research runbook](docs/midterm-issue-research.md) explains collection and offline reproduction with `python scripts/run_midterm_study.py --offline` after installing `.[research,dev]`.

For a self-contained introduction, open [the standalone API tutorial notebook](polymarket_kalshi_api_tutorial.ipynb). It uses ordinary Python HTTP requests to explore Polymarket Gamma, CLOB, Data and streaming APIs, plus Kalshi discovery, books, trades, candles and historical data. Public examples need no credentials or installation of this package.

Requires Python 3.11+. From this directory:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev,graphql]"

# Bounded first run: congressional control and Fed decisions on both venues.
.venv\Scripts\python.exe -m pmresearch discover --query "2026 Senate control" --query "Fed interest rates" --max-pages 2 --max-series 20
```

On this Windows workspace, `.venv` is already installed and tested. Use `.venv\Scripts\python.exe` if `python` opens the Microsoft Store. On macOS/Linux use `.venv/bin/python` instead. `uv pip install --python .venv/Scripts/python.exe -e ".[dev,graphql]"` is an alternative when using `uv`.

Every collection prints its output directory. Reuse its `markets.jsonl` path below:

```powershell
# Replace the path with the directory printed by discover.
.venv\Scripts\python.exe -m pmresearch collect --universe "data/<run>/markets.jsonl" --datasets books history trades holders open_interest comments --max-markets 20 --max-pages 5

# Repeat discovery later, then compare two snapshots of the same search scope.
.venv\Scripts\python.exe -m pmresearch analyze --current "data/<new-run>/markets.jsonl" --previous "data/<old-run>/markets.jsonl"
```

`collect` defaults to seven days of history and hourly observations. Its limit counts contracts, including all saved Polymarket outcomes for each contract. It interleaves venues while preserving each venue's discovery order, so a small limit can sample both. `discover` is a fresh metadata/price snapshot; rerun it to create the inputs for `analyze`. `collect` saves supplementary datasets in `extracted.jsonl`; it does not overwrite the discovery snapshot.

## Scripts and commands

| Script / command | Purpose |
|---|---|
| `scripts/extract_polymarket.py` | Discover Polymarket events, classify markets and export CSV/JSONL |
| `scripts/extract_kalshi.py` | Discover relevant series/events and normalize Kalshi markets |
| `scripts/collect_market_data.py` | Selected-market books, history, public trades and analytics |
| `scripts/stream_market_data.py` | Finite WebSocket capture, with heartbeats and reconnect/gap records |
| `python -m pmresearch request …` | Extract a documented GET endpoint directly, with arbitrary supported query parameters |
| `python -m pmresearch catalog` | Refresh an official-schema inventory of API operations and parameters |
| `python -m pmresearch analyze …` | Compare price snapshots and rank research leads with quality flags |
| `python -m pmresearch topics` | Show topic IDs, keyword rules and search queries |

Run any command with `--help`. Install the package before running the standalone scripts.

## API coverage

Verified against the official documentation and schemas on **2026-09-07 Singapore time**. API schemas can change independently of this repository. [docs/api_inventory.json](docs/api_inventory.json) records the source URLs, retrieval time, versions, hashes and 311 operations found across eight OpenAPI schemas. That inventory includes state-changing operations for completeness; it is **not a claim that all 311 are implemented or executable here**.

| Platform / API family | Implemented research data | Access and limits |
|---|---|---|
| Polymarket **Gamma** | Events, markets, search, tags/related tags, series, comments, public profiles, rules/descriptions, keyset/offset pagination | Public; search recall depends on query vocabulary |
| Polymarket **CLOB REST** | Outcome books, batch books, prices, midpoints, spreads, trades' last prices, single/batch history, condition metadata, fee/tick/negative-risk data, activity, rewards | Public market data; account/builder trade reads require credentials |
| Polymarket **Data API** | Public trades, wallet activity/positions/closed positions/value, holders, market positions, open interest, event volume, leaderboards, builder metrics and rule revisions | Public wallet data; offset/holder/history limits vary by endpoint |
| Polymarket **market WebSocket** | Books, price changes, executions, tick-size and lifecycle messages | Public, subscribed by outcome token ID |
| Polymarket **RTDS** | Event comments, equity and crypto price subscriptions | Optional contextual feeds; distinct from prediction-market books |
| Polymarket **Bridge** | Supported assets, transaction status/history and non-executing quote | Funding metadata, not election signals; no deposits/withdrawals |
| Polymarket **Relayer / Combos RFQ** | Transaction/deployment/nonce reads; combo-market discovery | Some reads need caller credentials; no transaction or quote execution |
| Polymarket **Perps** | Instruments, tickers, books, trades, funding and klines | Optional separate product; values are not election probabilities |
| Polymarket **on-chain / subgraphs** | Read-only GraphQL query helper with AST validation and caller-supplied endpoint/schema | Requires `graphql` extra and provider access; no presumed permanent hosted subgraph URL |
| Kalshi **Trade API REST** | Series, events, markets, resolution metadata, books, trades, candles, batch/event candles and forecast percentile history | Public market data works without a key |
| Kalshi **historical REST** | Cutoffs, archived markets, trades, candles and authenticated archived portfolio records | Separate archival tier; current endpoints alone are insufficient for older data |
| Kalshi **reference / exchange APIs** | Status, schedule, fees, tags, milestones, structured targets, live event data, incentives, multivariate collections | Some endpoints/data sources require access; raw responses retained |
| Kalshi **portfolio/account reads** | Positions, fills, orders, settlements, balance, historical positions, account limits and endpoint costs | Optional RSA-PSS authentication; account owner data only |
| Kalshi **WebSocket** | Ticker, trade and orderbook updates; configurable documented channels | Authentication required for the connection, including public-data channels |

More methods and Python examples: [Polymarket guide](docs/polymarket.md), [Kalshi guide](docs/kalshi.md), [research workflow](docs/research_workflow.md), [API cookbook](examples/api_cookbook.py).

Preview actual output in [sample_markets.csv](examples/sample_markets.csv) or [sample_markets.jsonl](examples/sample_markets.jsonl). These are 12 selected timestamped public observations, including state data-center moratorium markets; they are illustrative snapshots, not current recommendations.

Scope boundaries: order placement/cancellation, API-key creation, RFQ execution, transfers, redemption and bridge execution are outside this extraction toolkit. Polymarket's user/sports/perpetual streaming protocols are documented separately and are not wired into the default CLI. Kalshi's separate perpetual product is outside the election-contract workflow. Authenticated and niche endpoints have unit coverage or documented wrappers, not a claim of successful live access. The Polymarket ZIP accounting export is binary and is not handled by the JSON transport.

## Discover election issues and macro markets

```powershell
# All configured topics; potentially hundreds of requests.
.venv\Scripts\python.exe -m pmresearch discover --max-pages 5 --max-series 200

# Election-first research universe.
.venv\Scripts\python.exe -m pmresearch discover --topic midterms_2026 --max-series 100

# Data-center opposition, electricity prices and energy policy.
.venv\Scripts\python.exe -m pmresearch discover --topic data_centers_ai_power --topic energy_climate --catalog-scan --max-pages 5

# Macro and Fed decision markets.
.venv\Scripts\python.exe -m pmresearch discover --topic fed_monetary_policy --topic us_macro --max-series 100

# Explicit Kalshi series, discovered from kalshi_series.json or the API.
.venv\Scripts\python.exe -m pmresearch discover --platform kalshi --kalshi-series CONTROLS --max-pages 5

# Include closed recent events. Archived Kalshi markets need historical reads below.
.venv\Scripts\python.exe -m pmresearch discover --topic midterms_2026 --include-closed
```

Topic rules live in [config/topics.json](config/topics.json). Pass `--topics-config config/topics.json` to use an edited version; otherwise the installed package uses its bundled copy. The normalizer requires US context for ambiguous terms, so a bare `GDP` or `data center` title can be excluded unless its event/rules provide that context. Inspect `candidates.jsonl` and extend the taxonomy when necessary. New themes will need new vocabulary. `--catalog-scan` broadens discovery but remains bounded by `--max-pages`.

The output manifest reports errors, page caps, series limits and other coverage limits. A default run is a bounded discovery sample, not a census of every relevant market. Changing queries, taxonomy or limits can create apparent emergence/disappearance unrelated to market demand. Raw responses preserve cursors for inspection, but the CLI does not automatically resume an interrupted run; rerun or use adapter cursor parameters explicitly.

## Direct extraction and historical data

PowerShell JSON quoting can differ by version. The Python cookbook avoids shell quoting and is useful for complex parameters.

```powershell
.venv\Scripts\python.exe -m pmresearch request gamma /tags
.venv\Scripts\python.exe -m pmresearch request kalshi /series
.venv\Scripts\python.exe -m pmresearch request kalshi /historical/cutoff
.venv\Scripts\python.exe -m pmresearch request kalshi /historical/markets --params '{"limit":100}'
.venv\Scripts\python.exe -m pmresearch request kalshi /exchange/status

.venv\Scripts\python.exe -m pmresearch collect --universe "data/<run>/markets.jsonl" --start 2026-08-01T00:00:00Z --end 2026-09-01T00:00:00Z --period 60 --max-pages 20
```

`request` retrieves **one response**, not all pages. Use adapter iterators for bulk extraction. `collect` queries both live and historical Kalshi trades when the requested period crosses the trade cutoff, and deduplicates by trade ID. Archived candle routing is based on archived market availability, because candle archival follows the **market's settlement time**, not each candle timestamp. Long candle/history windows are chunked and boundary observations deduplicated. For full Polymarket public-trade history beyond offset limits, use `iter_trades_windowed`; a saturated one-second window or exhausted request budget raises a visible error.

Historical candles and sampled prices are not historical order books. Capture books now if future depth, spread and liquidity analysis matters. API history retention and sampling differ, and an empty result is not proof of no trading. Missing or skipped datasets remain null/absent and errors are recorded.

## Streaming and optional credentials

```powershell
# Token IDs come from markets.jsonl; Gamma ID / condition ID will not work here.
.venv\Scripts\python.exe -m pmresearch stream polymarket --id "<outcome-token-id>" --seconds 120

# Comments about a specific Polymarket event.
.venv\Scripts\python.exe -m pmresearch stream rtds --event-id "<numeric-event-id>" --seconds 120

# Kalshi requires a key and RSA PEM file for WebSocket handshakes.
$env:KALSHI_API_KEY_ID = "your-key-id"
$env:KALSHI_PRIVATE_KEY_PATH = "C:\secure\kalshi-private-key.pem"
.venv\Scripts\python.exe -m pmresearch stream kalshi --id "<market-ticker>" --seconds 120
.venv\Scripts\python.exe -m pmresearch request kalshi /portfolio/positions --authenticated
```

Credentials are read from process environment variables; `.env.example` is only a template. Credentials and request headers are not written to manifests or raw logs. Private response data and public wallet activity are retained locally if you explicitly collect them; `data/`, `.env` and key files are excluded from Git. Existing Polymarket L2 credentials can be injected from Python; see its guide. Public REST research needs neither platform's credentials.

Streams preserve raw frames, receipt timestamps, connection IDs and gaps. Reconnection does not recover missed frames. Kalshi orderbook sequence gaps trigger resubscription; consumers must start a new book from a fresh snapshot. This is a raw capture utility, not a persistent matching-engine replica. Ctrl+C retains captured data and marks the run interrupted.

## Output and interpretation

| File | Contents |
|---|---|
| `manifest.json` | Settings, start/end time, request count, status, failures and limits |
| `raw_responses.jsonl` | API response envelopes with endpoint, parameters, retrieval timestamp and original payload; no auth headers |
| `candidates.jsonl` | Raw candidate markets with event context, including items rejected by topic filters |
| `markets.jsonl`, `markets.csv` | One row per Polymarket outcome token, or Kalshi YES contract; identifiers, rules, prices, quality flags and topic evidence |
| `kalshi_series.json`, `coverage.json` | Discovery catalog and coverage counts |
| `extracted.jsonl` | Books, histories, trades and selected analytics in platform-native structures |
| `history_window.json` | Requested bounds, sampling and Kalshi archival cutoff |
| `research_report.json` | Research watchlist, topic coverage, valid price changes and skipped comparisons |

Prices are normalized to 0–1 when they represent binary contracts. Kalshi `*_dollars` / `*_fp` fields are preferred; legacy cents are divided by 100 only when their field explicitly denotes cents. Kalshi NO bids imply YES asks at `1 - NO bid`. One-sided or crossed books are not treated as reliable midpoints. Polymarket displayed outcome prices and Kalshi quote midpoints can reflect different price conventions; the report does not call their difference an arbitrage.

Polymarket notional volume and Kalshi contract counts retain separate units. Kalshi's deprecated liquidity fields are not interpreted as useful liquidity estimates. Price, event attention, trading activity, concentration and public support are separate quantities. A single collected snapshot does not establish momentum. The report does not average unrelated contracts into a topic probability, infer conditional probabilities from marginals, or infer equity returns from election probabilities. Cross-venue matching requires manual verification of outcomes, date, resolution source and rules.

## Validation

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check pmresearch tests scripts examples
```

Tests cover pagination, retry/auth behavior, identifier joins, fixed-point units, implied asks, topic exclusions, history routing/deduplication, snapshot comparisons and stream gap handling. Live checks cover selected public endpoints only; credentials were not supplied for private-account or Kalshi WebSocket tests. See [docs/validation.md](docs/validation.md) for exact verified scope and sample results.

`requirements.lock` records the dependency versions used for these checks. To reproduce them, install that file before installing this package with `pip install --no-deps -e .`.

Official sources: [Polymarket API index](https://docs.polymarket.com/llms.txt), [Polymarket real-time feeds](https://docs.polymarket.com/market-data/realtime-data), [Kalshi API index](https://docs.kalshi.com/llms.txt), [Kalshi historical data](https://docs.kalshi.com/getting_started/historical_data), [Kalshi fixed-point fields](https://docs.kalshi.com/getting_started/fixed_point_migration).
