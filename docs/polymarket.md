# Polymarket extraction reference

Verified against the official documentation and OpenAPI/AsyncAPI specifications on **7 September 2026**. All adapters preserve raw response fields. This matters because the current CLOB metadata uses compact keys and the SDKs have changed substantially since older tutorials were written.

## What is covered

| API family | Adapter and base URL | Research data and scope |
|---|---|---|
| Gamma | `pm.gamma`, `https://gamma-api.polymarket.com` | Event/market discovery, metadata, descriptions and rules, tags and related-tag graph, recurring series, comments, public profiles, event creators, search, comment/tweet counts. Also exposes sports/team metadata through documented GET routes. |
| Predictions CLOB | `pm.clob`, `https://clob.polymarket.com` | Order books, executable prices, midpoint, spread, last trades, token price history, batch history, market trading parameters, tick sizes, fees, negative-risk flags, rewards and builder-attributed trades. Optional authenticated account reads. |
| Data | `pm.data`, `https://data-api.polymarket.com` | Public prints, wallet activity, open/closed positions, top holders, market positions, open interest, live event volume, position value, question revisions, trader/builder leaderboards, builder volume, wallet combo positions/activity and token approval state. |
| Market WebSocket and RTDS | `pmresearch.streams` | Current order-book/trade/lifecycle updates; RTDS topics available in the separate streaming module. See its examples and current subscription schema. |
| Combos RFQ | `pm.combos`, `https://combos-rfq-api.polymarket.com` | Public catalog of markets eligible for combos. RFQ negotiation and execution are trading operations and are outside extraction scope. |
| Bridge | `pm.bridge`, `https://bridge.polymarket.com` | Supported assets, existing transfer status/history and quote previews. No bridge address creation, deposits or withdrawals. |
| Relayer | `pm.relayer`, `https://relayer-v2.polymarket.com` | Existing transaction status, nonce, relay payload and wallet deployment state. Recent account transactions require an injected credential signer. No transaction submission or wallet deployment. |
| Perpetuals | `pm.perps`, `https://api.perpetuals.polymarket.com` | Separate public instrument, ticker, book, trade, funding, kline, mark/index and exchange metadata. These are perpetual instruments, **not election probability contracts**. Kept separate from predictions. |
| On-chain analytics / subgraphs | `SubgraphAPI(endpoint=...)` | Optional GraphQL query transport for a caller-selected provider. Requires the `graphql` extra and a verified provider endpoint/schema. Current Polymarket blockchain-data documentation points to Goldsky, Dune and Allium rather than guaranteeing an old subgraph URL. |

Sources: [documentation index](https://docs.polymarket.com/llms.txt), [Gamma specification](https://docs.polymarket.com/api-spec/gamma-openapi.yaml), [CLOB specification](https://docs.polymarket.com/api-spec/clob-openapi.yaml), [Data specification](https://docs.polymarket.com/api-spec/data-openapi.yaml), [Combos specification](https://docs.polymarket.com/api-spec/combos-rfq-openapi.yaml), [Bridge specification](https://docs.polymarket.com/api-spec/bridge-openapi.yaml), [Relayer specification](https://docs.polymarket.com/api-spec/relayer-openapi.yaml), [Perpetuals specification](https://docs.polymarket.com/api-spec/perps-openapi.json), [blockchain-data resources](https://docs.polymarket.com/resources/blockchain-data).

API-key management, session keys, order entry/cancellation, RFQ submission, settlement, collateral movement and contract writes are not extraction operations and are deliberately absent. Authenticated user/order WebSocket streams and private Perpetuals account streams are not configured by the public collectors. An official Data endpoint, `GET /v1/accounting/snapshot?user=...`, returns a **ZIP of CSV files**; this JSON adapter does not download it. The regular positions, activity and value endpoints provide structured account research data. Public APIs require no API key; credentials are only needed for the optional participant/relayer reads.

## Identifiers and discovery

Use the correct identifier for each surface:

| Identifier | Source | Uses |
|---|---|---|
| Gamma event `id` | Event object | Event metadata/comments/tags; Data `eventId` filters and live-volume `id` |
| Gamma market `id` | Market object | Gamma market detail, description, tags |
| Market `conditionId` | Gamma market | CLOB metadata; Data trades/positions/holders/open interest |
| Outcome token ID | Gamma `clobTokenIds`, paired with `outcomes` | CLOB books, prices, price history and market WebSocket subscriptions |
| `questionID` / `questionId` | Market/on-chain metadata | Data `/revisions` |
| Public proxy wallet address | Public trades/holders/profile | Wallet positions, activity and value; distinct from API-key ownership in some setups |

Gamma frequently encodes `outcomes`, `outcomePrices`, and `clobTokenIds` as JSON **strings**. Parse them and keep the original ordering. Never assume the first outcome is Yes without checking its label. Multi-candidate events contain multiple individual markets, and negative-risk events require particular care when adding probabilities.

```python
import json
from pmresearch.polymarket import Polymarket

with Polymarket() as pm:
    events = list(pm.gamma.iter_search(
        "midterms", max_pages=5, limit_per_type=20,
        search_tags=True, search_profiles=False,
    ))
    event = events[0]
    market = event["markets"][0]
    tokens = market["clobTokenIds"]
    outcomes = market["outcomes"]
    tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    token_by_outcome = dict(zip(outcomes, tokens))
    print(event["title"], market["question"], token_by_outcome)
```

`search(query, **params)` returns events, tags, profiles and pagination metadata. `iter_search(query, max_pages=10, page=1, **params)` yields **events only**, following the search API's `page` and `pagination.hasMore`. Search is relevance-ranked; it is not a full census. Use broad catalog scans plus tags and the local topic classifier to find markets whose wording differs from your seed terms.

`iter_events(page_size=100, max_pages=100, pagination="keyset", **params)` and `iter_markets(...)` default to the current keyset endpoints. They follow `next_cursor` using `after_cursor`. The respective maximum page sizes are 500 and 100. Keyset endpoints reject `offset`; explicit `pagination="offset"` uses the older list endpoints when needed.

Useful event filters include `title_search`, `tag_id`, `tag_slug`, `related_tags`, `closed`, `series_id`, `volume_min`, `liquidity_min`, `start_date_min/max` and `end_date_min/max`. `title_search` applies to `/events/keyset`. `active` is documented on the older `/events` endpoint, not on keyset. Market filters include `condition_ids`, `clob_token_ids`, `tag_id`, `closed`, `volume_num_min`, `liquidity_num_min` and date limits. Pass API spellings unchanged. Arrays on Gamma are repeated URL parameters; Data array filters use comma-separated values, handled by the adapter.

```python
with Polymarket() as pm:
    politics = pm.gamma.tag_by_slug("politics")
    neighbours = pm.gamma.related_tags(politics["id"], full=True, status="active")
    for event in pm.gamma.iter_events(
        tag_id=[int(politics["id"])], closed=False, max_pages=20,
    ):
        print(event["id"], event["title"])
```

Discover available tags instead of hard-coding numeric IDs. Related tags form a directed graph, not a clean hierarchical classification. A broad search for data centers, grid interconnection, permitting, electricity bills, utility regulation, AI, water restrictions and local moratoria can identify relevant issue contracts, but a missing contract cannot be treated as a zero probability.

## Prices, history and liquidity

```python
with Polymarket() as pm:
    # Replace these with identifiers from the discovery example.
    condition_id = market["conditionId"]
    token_id = tokens[0]
    metadata = pm.clob.market(condition_id)
    book = pm.clob.book(token_id)
    buy_price = pm.clob.price(token_id, side="BUY")
    sell_price = pm.clob.price(token_id, side="SELL")
    midpoint = pm.clob.midpoint(token_id)
    spread = pm.clob.spread(token_id)
    history = pm.clob.price_history(token_id, interval="1w", fidelity=60)
    both_histories = pm.clob.batch_price_history(tokens, interval="1w", fidelity=60)
    trades = pm.data.trades(market=[condition_id], limit=1000, takerOnly=True)
    holders = pm.data.holders([condition_id], limit=20)
    open_interest = pm.data.open_interest([condition_id])
    event_volume = pm.data.live_volume(event["id"])
```

The current documented CLOB metadata route is `/clob-markets/{condition_id}`. `/markets-by-token/{token_id}` resolves metadata from a token. Older examples using CLOB `/markets/{condition_id}` are not the route used here. The simplified/sampling catalogs remain available through `clob.iter_markets(kind=...)`.

The price endpoint's `BUY` side returns the lowest ask and `SELL` returns the highest bid. Book arrays are not guaranteed to have the best price first; compute best bid as `max(bids)` and best ask as `min(asks)`. Preserve decimal strings or use `Decimal` when calculating execution costs. Do not mix last-trade prices, midpoint estimates and executable quotes in a time series without labeling them. [Price and order-book documentation](https://docs.polymarket.com/market-data/prices-order-books).

`price_history(token_id, startTs=..., endTs=..., fidelity=...)` accepts epoch **seconds**; `fidelity` is in **minutes**. The batch endpoint uses `start_ts/end_ts` in its JSON body and allows at most 20 tokens. The adapter also accepts the opposite casing and maps it to the correct wire format. Without a time range it defaults to `interval="max"`. Batch books/prices/midpoints/spreads/last prices use read-only POSTs. Price history is a sampled series; it does not reconstruct historical full-depth books. Begin live book collection now if future microstructure research is planned.

## Trade and activity history

`iter_trades()` and `iter_activity()` are bounded offset collectors. Current documented limits are:

| Endpoint | Maximum page size | Maximum offset | Additional limitation |
|---|---:|---:|---|
| `/trades` | 10,000 | 10,000 | Market/event-scoped history retains an approximately three-year floor even when `start` is earlier |
| `/activity` | 500 | 5,000 | Wallet required; `start=1` can request complete available wallet history |
| `/positions` | 500 | 10,000 | Snapshot of current positions |
| `/closed-positions` | 50 | 100,000 | Wallet required |
| `/holders` | 20 per outcome | None | Top holders only; not all holders |
| `/v1/market-positions` | 500 per outcome | 10,000 | Result grouped per token; paginate each group consistently |
| `/v1/leaderboard` | 50 | 1,000 | Rank/category/time-period snapshot |

For deep history use bounded, explicit windows:

```python
from datetime import datetime, timezone

start = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
end = int(datetime.now(timezone.utc).timestamp())
with Polymarket() as pm:
    for trade in pm.data.iter_trades_windowed(
        market=[condition_id], start=start, end=end,
        page_size=1000, max_requests=200, takerOnly=True,
    ):
        print(trade)
```

The window collector buffers a window, bisects it if it exceeds the offset budget, and emits only complete child windows. It handles inclusive second boundaries without duplicate parent-probe results. A single second exceeding the endpoint capacity raises `PaginationError`. A request budget reached mid-window emits `PaginationWarning`; the current incomplete window is not emitted. Output ordering is by processed windows and each endpoint's native row order, so sort by timestamp downstream. This cannot recover history the server does not retain.

All iterators warn when their configured cap is reached and fail if pages or cursors repeat. A cap warning means the result is incomplete; increasing a cap or narrowing the interval is a research decision. Offset pages are not a frozen database snapshot. Use fixed historical end times for backfills and overlap/deduplicate incremental runs using transaction, asset, wallet and event identifiers appropriate to the endpoint.

Public trades default to `takerOnly=true`. Setting it false may introduce both sides of an economic trade; prevent volume double counting. Activity deposits/withdrawals require `excludeDepositsWithdrawals=False` even when explicitly requested with the `type` filter. `market` and `eventId` are mutually exclusive on the Data API. Sources: [Data API specification](https://docs.polymarket.com/api-spec/data-openapi.yaml).

## Metadata, attention and wallet diagnostics

```python
with Polymarket() as pm:
    rules = pm.gamma.market_description(market["id"])
    comments = list(pm.gamma.iter_comments(
        parent_entity_type="Event", parent_entity_id=event["id"],
        page_size=100, max_pages=3,
    ))
    counts = pm.gamma.get(f"/events/{event['id']}/comments/count")
    leaders = pm.data.leaderboard(category="POLITICS", timePeriod="MONTH", orderBy="VOL")
    related = pm.gamma.market_tags(market["id"])
    rewards = pm.clob.rewards(condition_id)
```

Comments and tweet/comment counts provide attention signals, not representative voter sentiment. Counts are current snapshots and may be revised. Rewards can explain apparent increases in liquidity or volume. Leaderboards select on trading results; they do not establish superior forecasting ability. Public wallets are analytical identifiers, not verified identities. For rule changes, archive Gamma descriptions and call `data.revisions(question_id)` where the appropriate on-chain question ID is available.

For other documented reads use `adapter.get(path, **params)`: Gamma creator/series summaries, Data `/v1/approvals`, `/other`, `/v1/market-positions`, and public Perps `/v1/info/mark-history`, `/v1/info/index`, `/v1/info/statistics`, etc. The adapter uses an explicit GET allowlist. It excludes CLOB's state-changing `GET /balance-allowance/update`.

## Optional authenticated CLOB reads

Use existing L2 credentials only. The public discovery/history workflow does not need them. Set `POLYMARKET_ADDRESS`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET` and `POLYMARKET_API_PASSPHRASE` in the environment and inject the signer into the CLOB transport alone:

```python
from pmresearch.http import HttpClient
from pmresearch.polymarket import ClobAPI, PolymarketL2Signer

with HttpClient(ClobAPI.BASE, signer=PolymarketL2Signer.from_env()) as client:
    clob = ClobAPI(client)
    own_orders = clob.get("/data/orders", market=condition_id)
    own_trades = clob.account_trades(maker_address="YOUR_MAKER_ADDRESS", market=condition_id)
    balance = clob.get("/balance-allowance", asset_type="COLLATERAL", signature_type=0)
```

Use the correct address/signature type for the existing account; the above placeholders are not account setup. The signer computes GET HMAC headers afresh for retries, does not create or derive credentials, and rejects POST signing. Auth headers never enter response traces. These account reads were contract-tested with mocks, not live-tested with private credentials. Existing SDK signing reference: [official CLOB signing implementation](https://github.com/Polymarket/py-clob-client/blob/main/py_clob_client/signing/hmac.py).

## Streams, on-chain context and optional APIs

The market stream endpoint is `wss://ws-subscriptions-clob.polymarket.com/ws/market`. Its initial payload is `{"assets_ids": ["TOKEN_ID"], "type": "market", "custom_feature_enabled": true, "initial_dump": true}`. Send text `PING` every ten seconds and consume `PONG`. Relevant messages include book, price-change, last-trade, tick-size, best-bid/ask and new/resolved-market events. Reconnects are gaps: take a fresh snapshot and retain receipt times. [Market AsyncAPI specification](https://docs.polymarket.com/asyncapi.json).

`SubgraphAPI` accepts a caller-verified HTTPS GraphQL endpoint and validates the document AST to reject mutation/subscription operations. Provider schemas, indexing delays, API keys, chain/contract versions and historical coverage must be established separately. No default subgraph URL or assumed entity names are built into the collector. On-chain fills can enrich public trade history but cannot reconstruct off-chain orders that never settled.

`pm.combos.iter_markets()` follows opaque `cursor` pagination. `pm.bridge.iter_status(bridge_address)` follows `nextCursor` even when a page is short or empty. `pm.bridge.quote(**body)` previews fees only. `pm.perps.get('/v1/info/...', **params)` uses the separate Perps parameter/unit conventions; do not pass prediction-token IDs as Perps instrument IDs or interpret instrument prices as probabilities.

## Validation performed

A small read-only live check retrieved 2026 Senate/House midterm search results, a Gamma keyset page, market descriptions, CLOB metadata/books/single and batch history, Data filtered trades/holders/open interest, the combo catalog and public Perps instruments. No credentials were used. Unit tests cover request formats, pagination termination/caps, time-window splitting, authentication isolation and GraphQL read-only enforcement. Authenticated account reads, Bridge/Relayer operations, full historical completeness and every optional route have **not** been live-validated.
