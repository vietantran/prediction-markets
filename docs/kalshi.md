# Kalshi extraction coverage

Implemented against the [official documentation index](https://docs.kalshi.com/llms.txt) and [OpenAPI 3.29.0](https://docs.kalshi.com/openapi.yaml), checked 2026-09-07. This adapter reads data. Trading, account mutation, funds movement, API-key creation, and RFQ/quote submission endpoints are outside the extraction task and are not implemented. No third-party user portfolio is public through Kalshi.

Verification on 2026-09-07: unauthenticated production reads succeeded for exchange status, historical cutoff, Politics series, open markets, event detail, a fixed-point orderbook, public trades, and candles. The catalogue contained 2,299 Politics series at that observation. Offline tests cover signing, pagination, exact book conversion, batch encoding, history windows, collection of archived details, and midterm discovery. Authenticated account data and entitled CF Benchmarks reads were not exercised with real credentials.

Production REST: `https://external-api.kalshi.com/trade-api/v2`; demo: `https://external-api.demo.kalshi.co/trade-api/v2`. The old `api.elections.kalshi.com` host remains supported and contains all categories, despite its name. See [environments](https://docs.kalshi.com/getting_started/api_environments).

## Public and market-data REST

All methods preserve API JSON and fixed-point strings. `get_*` returns a whole response; `iter_*` yields rows unless its name is `iter_pages`. Optional keyword parameters are forwarded to the API; use the linked reference for filters and units.

| Data | Adapter | REST path relative to base | Research use / reference |
|---|---|---|---|
| Series catalogue and detail | `get_series_list`, `get_series` | `/series`, `/series/{series_ticker}` | Recurring election/macro families; rules, tags, settlement sources, fees. [Series](https://docs.kalshi.com/api-reference/market/get-series-list) |
| Events and nested markets | `get_events`, `iter_events`, `get_event` | `/events`, `/events/{event_ticker}` | Group mutually exclusive thresholds or candidates; fetch `with_nested_markets=True`. [Events](https://docs.kalshi.com/api-reference/events/get-events) |
| Market contracts | `get_markets`, `iter_markets`, `get_market` | `/markets`, `/markets/{ticker}` | Rules, strikes, close/settle times, dollar prices, volume, open interest. [Markets](https://docs.kalshi.com/api-reference/market/get-markets) |
| Order books, single/batch | `get_orderbook`, `get_orderbooks` | `/markets/{ticker}/orderbook`, `/markets/orderbooks` | Spread and available depth; batch accepts up to 100 tickers. [Books](https://docs.kalshi.com/api-reference/market/get-multiple-market-orderbooks) |
| Public executed trades | `get_trades`, `iter_trades` | `/markets/trades` | Timestamped price, quantity, taker direction and block-trade indicator. [Trades](https://docs.kalshi.com/api-reference/market/get-trades) |
| Single/batch/event candles | `get_candlesticks`, `iter_candlesticks`, `get_batch_candlesticks`, `get_event_candlesticks` | `/series/{series}/markets/{ticker}/candlesticks`, `/markets/candlesticks`, `/series/{series}/events/{ticker}/candlesticks` | OHLC and activity; periods 1, 60, 1440 minutes. [Candles](https://docs.kalshi.com/api-reference/market/get-market-candlesticks), [batch cap](https://docs.kalshi.com/api-reference/market/batch-get-market-candlesticks) |
| Event metadata | `get_event_metadata` | `/events/{event_ticker}/metadata` | Images, settlement/report metadata when provided. [Metadata](https://docs.kalshi.com/api-reference/events/get-event-metadata) |
| Forecast percentiles | `get_forecast_percentile_history` | `/series/{series}/events/{ticker}/forecast_percentile_history` | Numeric forecast distribution history; 5000 means median. Availability depends on event. [Forecasts](https://docs.kalshi.com/api-reference/events/get-event-forecast-percentile-history) |
| Taxonomy | `get_tags_by_categories` | `/search/tags_by_categories` | Discover Politics/Economics tags before filtering locally. [Tags](https://docs.kalshi.com/api-reference/search/get-tags-for-series-categories) |
| Milestones and entities | `iter_milestones`, `get_milestone`, `iter_structured_targets`, `get_structured_target` | `/milestones`, `/milestones/{id}`, `/structured_targets`, `/structured_targets/{id}` | Link related state House/Senate/governor events through a shared real-world occurrence. [Guide](https://docs.kalshi.com/getting_started/targets_and_milestones) |
| Live underlying data | `get_live_data`, `get_batch_live_data`, `get_event_live_data` | `/live_data/milestone/{id}`, `/live_data/batch`, `/live_data/events/{ticker}` | Supported underlying observations only; it is not a general political news feed. [Live data](https://docs.kalshi.com/api-reference/live-data/get-event-live-data) |
| Multivariate/combo events | `iter_multivariate_events`, `iter_multivariate_collections`, `get_multivariate_collection` | `/events/multivariate`, `/multivariate_event_collections`, `/multivariate_event_collections/{ticker}` | Explicit combo markets; contract dependence differs from independent single markets. [Combos](https://docs.kalshi.com/api-reference/events/get-multivariate-events) |
| Liquidity/trading incentives | `iter_incentive_programs` | `/incentive_programs` | Annotate activity potentially influenced by rewards. [Incentives](https://docs.kalshi.com/api-reference/incentive-programs/get-incentives) |
| Exchange operations | `get_exchange_status`, `get_exchange_schedule` | `/exchange/status`, `/exchange/schedule` | Annotate pauses/maintenance in time series. [Status](https://docs.kalshi.com/api-reference/exchange/get-exchange-status) |
| Fee changes | `get_series_fee_changes`, `get_event_fee_changes` | `/series/fee_changes`, `/events/fee_changes` | Research net execution costs; use event overrides when present. [Series fees](https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes), [event fees](https://docs.kalshi.com/api-reference/events/get-event-fee-changes) |

The [public quickstart](https://docs.kalshi.com/getting_started/quick_start_market_data) explicitly shows unauthenticated series/events/markets/orderbook access. Some OpenAPI pages nevertheless mark orderbooks and other market-data reads authenticated. The client uses public access by default and does not silently request keys. If a particular endpoint returns 401, rerun that read with an explicitly configured authenticated client. Forecast/reference access and production entitlements can differ; HTTP failures are retained by the shared transport.

## Election discovery and cycle evidence

Discovery first admits relevant undated election series, then checks the event's cycle. It prioritizes used chamber-control templates and broad district families, so a small limit can still cover House control, Senate control, and individual districts. Series volume is only an activity hint. Within fetched families, close races with valid two-sided quotes and a spread at most 0.15 are ordered first; this is a research selection heuristic, not a confidence score. Pagination bounds can omit other races.

Some House contracts identify the election as `...-26` in their event ticker but state the settlement rule as the congressional **term beginning in 2027**. When both pieces of evidence occur, the pipeline records `derived_election_cycle=2026` and `derived_election_cycle_evidence` (ticker, exact rule, basis) in the saved candidate's event context. It places a clearly marked inference in `series_context` for classification while preserving the original market/event titles. A 2026 expiry date alone does not trigger this derivation; later election cycles still require their own evidence.

## Historical data is a separate API

Use `get_historical_cutoff()` (`GET /historical/cutoff`) at the start of each backfill. The cutoff advances; do not hardcode “three months.” The current guide describes four distinct boundaries:

| Cutoff field | Records and wrappers |
|---|---|
| `market_settled_ts` | `iter_historical_markets`, `get_historical_market`, `get_historical_candlesticks` |
| `trades_created_ts` | `iter_historical_trades`; `iter_fills(historical=True)` for your own fills |
| `orders_updated_ts` | `iter_orders(historical=True)` for old completed/cancelled own orders |
| `market_positions_last_updated_ts` | `iter_historical_positions` for archived own positions |

Paths are `/historical/markets`, `/historical/markets/{ticker}`, `/historical/markets/{ticker}/candlesticks`, `/historical/trades`, `/historical/fills`, `/historical/orders`, `/historical/positions`. Market/candle routing is based on **market settlement time**, not candle timestamp: an old candle for a still-open market remains in the live tier. Series and events stay on their normal endpoints; nested event responses omit archived markets. Historical market filters are mutually exclusive; live timestamp filters also have compatibility constraints. See the [historical guide](https://docs.kalshi.com/getting_started/historical_data) and [market filters](https://docs.kalshi.com/api-reference/market/get-markets).

For a history spanning a trade cutoff, query both live and historical trades and merge by `trade_id`. Query historical markets explicitly for older election comparisons; never equate a missing live market with a never-listed contract. `iter_candlesticks(..., historical=True)` selects the historical route and divides long ranges into bounded windows. Batch candle calls reject ranges that could exceed the documented 10,000-candle total limit. Single `get_*candlesticks` calls do not automatically divide a range; use the iterator for backfills.

## Optional authenticated read APIs

Install the project's authentication dependencies and set `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` to an existing key and local RSA PEM file. Construct `Kalshi(authenticated=True)`. Production/demo keys are separate. Credentials are loaded only when requested; signing headers are not logged.

`KalshiSigner` signs each retry with RSA-PSS/SHA256 and a 32-byte salt, using `timestamp_ms + uppercase_method + full_path_without_query`. It also signs the WebSocket handshake path `/trade-api/ws/v2`. See [authentication](https://docs.kalshi.com/getting_started/quick_start_authenticated_requests).

Named wrappers: `get_balance`, `get_positions`, `iter_positions`, `iter_fills`, `iter_orders`, `get_order`, `iter_settlements`, `iter_historical_positions`, `get_account_limits`, `get_endpoint_costs`. `get_positions` preserves both market and event positions; `iter_positions` selects market rows. These are account-specific, not aggregate public investor holdings.

The GET-only escape hatch `api.get(path, params, authenticated=True)` and `api.iter_pages(path, authenticated=True, **params)` cover less common extraction surfaces without trade operations. These endpoints are mapped in the [official index](https://docs.kalshi.com/llms.txt):

| Surface | GET endpoints |
|---|---|
| Order queue and groups | `/portfolio/orders/queue_positions`, `/portfolio/orders/{order_id}/queue_position`, `/portfolio/order_groups`, `/portfolio/order_groups/{order_group_id}` |
| Account/subaccount histories | `/portfolio/intra_exchange_instance_transfers`, `/portfolio/intra_exchange_instance_transfers/{transfer_id}`, `/portfolio/subaccounts/balances`, `/portfolio/subaccounts/transfers`, `/portfolio/subaccounts/netting`, `/portfolio/deposits`, `/portfolio/withdrawals`, `/portfolio/target_balance_allocation` |
| Account metadata | `/exchange/user_data_timestamp`, `/account/api_usage_level/volume_progress`, `/api_keys` (API-key metadata only; not automatically collected) |
| RFQ/quote observations | `/communications/id`, `/communications/block-trade-proposals`, `/communications/rfqs`, `/communications/rfqs/{rfq_id}`, `/communications/rfqs/{rfq_id}/quotes/{quote_id}`, `/communications/quotes`; legacy `/communications/quotes/{quote_id}` is deprecated |
| FCM-specific reads | `/fcm/orders`, `/fcm/positions`, `/portfolio/summary/total_resting_order_value`; require FCM membership |
| Out-of-focus reference reads | `/search/filters_by_sport`, `/live_data/{type}/milestone/{milestone_id}` (legacy), `/live_data/milestone/{milestone_id}/game_stats`, `/live_data/weather/{city}`, `/live_data/weather/{city}/calibrations` |

`get_cfbenchmarks(path="values", id="BRTI")` and `get_cfbenchmarks(path="history/values", ...)` cover the separate [CF Benchmarks passthrough](https://docs.kalshi.com/cfbenchmarks/rest-passthrough). It uses Kalshi signing, requires an account entitlement, has a higher read-token cost, and does not forward `includeVerification`. This optional crypto underlying feed is peripheral to the election focus.

## Streaming and interpretation

The project's streaming script uses the authenticated Trade API WebSocket endpoint. The current recommended URL is `wss://external-api-ws.kalshi.com/trade-api/ws/v2`. Even public ticker/trade/book channels require authentication on the connection. See [WebSocket connection](https://docs.kalshi.com/websockets/websocket-connection) and the project's streaming documentation for channels and reconnect behavior.

Book snapshots contain **YES bids and NO bids**. `orderbook_to_yes_quotes` converts a NO bid of 0.59 into a YES ask of 0.41, preserves its fractional size, sorts economically, and leaves missing sides missing. Prices use dollar strings (`*_dollars`, up to four decimal places), quantities use `*_fp` strings (up to two). Use `Decimal` for arithmetic; multiplying a quoted price by 100 produces percentage points, not an integer-cent storage format. Valid tick grids come from `price_ranges`. See [fixed-point representation](https://docs.kalshi.com/getting_started/fixed_point_migration) and [orderbooks](https://docs.kalshi.com/getting_started/orderbook_responses).

Bounded iterators warn when they stop with more data available and raise on repeated cursors. Whole raw pages preserve response cursors for resumption. This is a point-in-time feed, not a transactionally frozen snapshot; listings can change while paging. Full-depth historical books require collecting snapshots/deltas prospectively. A market's volume is contract activity and does not reveal unique investors, voters, or polling support. A new contract or volume spike alone does not demonstrate an issue gaining real-world political traction.

```python
from pmresearch.kalshi import Kalshi, orderbook_to_yes_quotes

with Kalshi() as api:
    # Discover current series names/tickers; do not assume old election tickers persist.
    for category in ("Politics", "Economics"):
        series_rows = api.get_series_list(category=category).get("series") or []
        print([(s["ticker"], s["title"]) for s in series_rows][:10])
    # Use discovered identifiers in these subsequent calls:
    # events = list(api.iter_events(series_ticker=series_ticker, with_nested_markets=True))
    # book = orderbook_to_yes_quotes(api.get_orderbook(market_ticker))
    # trades = list(api.iter_trades(ticker=market_ticker, min_ts=start_ts, max_ts=end_ts))
    cutoff = api.get_historical_cutoff()
```
