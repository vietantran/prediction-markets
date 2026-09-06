# Validation and known limits

Validated in this workspace on 2026-09-07 Asia/Singapore (2026-09-06 UTC), with CPython 3.12 and the versions in `requirements.lock`.

## Automated checks

`python -m pytest -q`: **75 passed, 18 subtests passed**.

`python -m ruff check pmresearch tests scripts examples`: **passed**.

`uv build --wheel --python .venv/Scripts/python.exe`: **passed**; the wheel includes the default taxonomy JSON. The code targets Python 3.11+, but this execution environment tested Python 3.12.

Tests exercise:

- Correct Gamma/condition/token ID joins and endpoint parameter casing.
- Bounded cursor, keyset and offset pagination, repeated-page detection, time-window splitting and deduplication.
- HTTP retries, boolean parameters, terminal errors and freshly signed retries without credential logging.
- Unexpected response/schema failures finalize the command manifest as failed, never as successful.
- Kalshi RSA-PSS signatures verified against a generated test key; Polymarket L2 HMAC behavior with test credentials.
- Kalshi fixed-point versus legacy cents, fractional quantity, NO-bid-to-YES-ask conversion, zero/one-sided/crossed books.
- US-only topic filtering, sports/foreign exclusions, district codes, headline-year precedence and election-cycle inference with explicit evidence.
- Inactive Polymarket placeholders and unverified 0/1 boundary quotes do not create fabricated midpoint probabilities.
- Historical/live routing and stable-ID deduplication.
- Same-contract snapshot comparison, rule/token/source changes, invalid timestamps and missing data.
- WebSocket subscription identifiers, visible sequence gaps and separation of data messages from acknowledgements.

## Live public checks

These are bounded functional checks, not exchange-wide coverage claims or trading recommendations. Raw outputs are under ignored `data/` and `.cache/` directories.

| Check | Result |
|---|---|
| Official API inventory | Eight OpenAPI schemas fetched successfully; 311 operations indexed with source hashes |
| Polymarket Gamma | Search, keyset events/markets, event metadata/descriptions and tags read successfully |
| Polymarket CLOB | Condition metadata, outcome books, spreads, single and batch price history read successfully |
| Polymarket Data | Filtered public trades, top holders and open interest read successfully |
| Additional Polymarket APIs | Public Perps instruments and combo-market catalog read successfully; no trade operations invoked |
| Initial combined Senate/Fed discovery | 755 normalized rows; page/series limits recorded; zero extraction errors |
| Revised Kalshi midterm discovery | 407 outcome rows from CONTROLH, CONTROLS and KXHOUSERACE; 204 events and 411 candidate markets inspected; two expected bounds warnings, zero errors |
| Polymarket policy-issue discovery | Six outcome rows representing three contracts from two bounded queries (`data center`, `electricity prices`); zero errors |
| Polymarket selected-contract collection | Senate contract books, seven-day hourly price history, public trades, holders, OI and comments; zero errors; contract budget reported |
| Kalshi selected-contract collection | Senate-control books, candles, public trades and market/OI metadata; zero errors; contract budget reported |
| Data-center contract collection | Books, history, trades, OI and comments; zero errors; contract budget reported |
| Polymarket WebSocket | Public Senate outcome stream captured market data, including a run of 6 data messages over 12 seconds; no gaps/errors |
| Python cookbook | Polymarket and Kalshi examples both executed successfully |
| Research report CLI | Executed successfully against the 12-row sample |

The Kalshi midterm fix is significant for recall: current chamber-winner templates differ from empty legacy control templates, and many district events omit the literal year from their title. For the latter, a 2026 label is inferred only when the event ticker ends in `-26`/`-2026` **and** the rules explicitly identify a congressional term beginning in 2027. Candidate event metadata retains the exact supporting evidence. Expiration dates are never used as election-year substitutes.

## Not live-validated

No account credentials were supplied. Kalshi authenticated WebSocket and private-account reads, Polymarket private CLOB/builder reads, provider-hosted GraphQL, bridge transaction histories, all reference datasets and every combination of historical filters were not live-tested. Unit tests and wrappers do not prove that an account is entitled to an endpoint or that a provider retains the requested history.

Streams do not backfill disconnect gaps. The command captures raw events and does not reconstruct a continuous book. REST snapshots are not atomic across venues or outcomes. Search results, quote timestamps, API coverage and archived cutoffs can change between calls. An empty response may be legitimate, filtered, unavailable or outside retention; it is not converted to zero.

The sample in `examples/sample_markets.csv` and `examples/sample_markets.jsonl` contains selected real public observations with receipt timestamps. It includes data-center policy, Senate control and Fed rates. It is neither an exhaustive universe nor evidence of an investment edge. Source-run selection is documented in `examples/sample_provenance.json`.
