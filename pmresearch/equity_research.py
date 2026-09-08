"""Public, read-only equity evidence with explicit temporal and missing-data controls.

Run: python -m pmresearch.equity_research --out data/.../equities
     --asof 2026-09-08T15:07:00Z --start 2026-07-08
     --universe config/midterm_equity_universe.json
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import time
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
FLOW_TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet", "RevenuesNetOfInterestExpense", "RevenueNetOfInterestExpense"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "cash_capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "diluted_eps": ["EarningsPerShareDiluted"],
}
INSTANT_TAGS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "assets": ["Assets"],
    "common_shares_outstanding": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
    "long_term_debt_current": ["LongTermDebtCurrent"],
    "long_term_debt_noncurrent": ["LongTermDebtNoncurrent"],
}
PRICE_FIELDS = ["ticker", "date", "close", "adjusted_close", "volume", "adjusted_return_1d", "currency", "source", "retrieved_at", "raw_file"]
RETURN_FIELDS = ["ticker", "name", "kind", "basket", "peer_group", "topics", "period", "start_date", "end_date", "sessions", "total_return", "sector_benchmark", "sector_return", "sector_excess_return", "spy_return", "spy_excess_return", "peer_ex_self_return", "peer_ex_self_excess_return", "peer_count", "coverage_status", "note"]
FUND_FIELDS = ["ticker", "metric", "period_type", "period_start", "period_end", "value", "unit", "tag", "accessions", "filed_dates", "latest_known_at", "source_urls", "status", "note"]


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def utcnow():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_csv(path, rows, fields):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class PublicCache:
    """Cache full public responses, including failed response evidence; bounded retries."""
    def __init__(self, out, transport=None, min_interval=0.3):
        self.out = Path(out)
        (self.out / "raw").mkdir(parents=True, exist_ok=True)
        self.records = []
        self.min_interval = min_interval
        self.last = 0.0
        self.client = httpx.Client(timeout=40, follow_redirects=True, transport=transport,
                                  headers={"User-Agent": os.environ.get("SEC_USER_AGENT", "pmresearch/0.1 public-financial-research")})

    def get(self, label, url, params=None):
        key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()[:14]
        path = self.out / "raw" / f"{label}_{key}.json.gz"
        if path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                saved = json.load(handle)
            if saved.get("status") == "ok":
                self.records.append({k: v for k, v in saved.items() if k != "data"} | {"cached": True})
                return saved
        for attempt in range(2):
            time.sleep(max(0, self.min_interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            saved = {"url": url, "params": params, "retrieved_at": utcnow(), "raw_file": str(path.resolve()), "attempt": attempt + 1}
            try:
                response = self.client.get(url, params=params)
                saved["http_status"] = response.status_code
                saved["response_url"] = str(response.url)
                response.raise_for_status()
                saved["data"] = response.json()
                saved["status"] = "ok"
            except (httpx.HTTPError, ValueError) as exc:
                saved["status"] = "error"
                saved["error"] = str(exc)[:1000]
                if "response" in locals():
                    saved["response_excerpt"] = response.text[:4000]
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(saved, handle)
            self.records.append({k: v for k, v in saved.items() if k != "data"})
            if saved["status"] == "ok":
                return saved
            if saved.get("http_status") not in {408, 429, 500, 502, 503, 504, None}:
                break
            if attempt == 0:
                time.sleep(1)
        raise RuntimeError(saved.get("error", "Public request failed"))

    def close(self):
        self.client.close()


def parse_prices(payload, ticker, asof, provenance=None):
    """Conservatively require regular NY session close; never accept partial same-day bars."""
    chart = payload.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise ValueError(f"Missing chart result: {chart.get('error')}")
    item = chart["result"][0]
    quote = (item.get("indicators", {}).get("quote") or [{}])[0]
    adjusted = (item.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose", [])
    rows, excluded = [], 0
    for index, timestamp in enumerate(item.get("timestamp") or []):
        local_date = datetime.fromtimestamp(timestamp, UTC).astimezone(NY).date()
        # Conservative on early-close days: a partial day is never labeled complete.
        session_close = datetime.combine(local_date, day_time(16), NY).astimezone(UTC)
        if session_close > asof:
            excluded += 1
            continue
        close = (quote.get("close") or [])[index] if index < len(quote.get("close") or []) else None
        adj = adjusted[index] if index < len(adjusted) else None
        volume = (quote.get("volume") or [])[index] if index < len(quote.get("volume") or []) else None
        if not finite(close) or not finite(adj) or close <= 0 or adj <= 0:
            continue  # No substitution of unadjusted prices into an adjusted-return series.
        rows.append({"ticker": ticker, "date": local_date.isoformat(), "close": close,
                     "adjusted_close": adj, "volume": volume, "currency": item.get("meta", {}).get("currency"),
                     "source": "Yahoo chart adjusted close", **(provenance or {})})
    return sorted({row["date"]: row for row in rows}.values(), key=lambda x: x["date"]), excluded


def period_return(series, dates):
    """Require every SPY session in the window; never forward-fill missing stock prices."""
    if len(dates) < 2 or any(day not in series for day in dates):
        return None
    return series[dates[-1]]["adjusted_close"] / series[dates[0]]["adjusted_close"] - 1


def calculate_returns(price_rows, universe, start):
    maps = {ticker: {row["date"]: row for row in rows} for ticker, rows in price_rows.items()}
    calendar = sorted(maps.get("SPY", {}))
    requested = [day for day in calendar if day >= start.isoformat()]
    if len(requested) < 2:
        return [], [], [], "No usable SPY session calendar in requested window"
    end = requested[-1]
    end_index = calendar.index(end)
    windows = {"common_window": requested}
    for length in [5, 20]:
        windows[f"{length}_sessions"] = calendar[end_index-length:end_index+1] if end_index >= length else []
    entries = [dict(row, kind="stock") for row in universe["securities"]]
    entries += [{"ticker": ticker, "name": ticker, "kind": "benchmark", "sector_benchmark": "SPY"} for ticker in universe["benchmarks"]]
    result = []
    for entry in entries:
        ticker = entry["ticker"]
        for period, dates in windows.items():
            own = period_return(maps.get(ticker, {}), dates)
            benchmark = entry.get("sector_benchmark", "SPY")
            sector = period_return(maps.get(benchmark, {}), dates)
            spy = period_return(maps.get("SPY", {}), dates)
            peer_values = [period_return(maps.get(other["ticker"], {}), dates) for other in universe["securities"]
                           if other["ticker"] != ticker and entry.get("peer_group") and other.get("peer_group") == entry["peer_group"]]
            peers = [value for value in peer_values if value is not None]
            peer_mean = sum(peers) / len(peers) if peers else None
            result.append({**entry, "topics": "|".join(entry.get("topics", [])), "period": period,
                           "start_date": dates[0] if dates else None, "end_date": dates[-1] if dates else None,
                           "sessions": max(0, len(dates)-1), "total_return": own, "sector_benchmark": benchmark,
                           "sector_return": sector, "sector_excess_return": own-sector if own is not None and sector is not None else None,
                           "spy_return": spy, "spy_excess_return": own-spy if own is not None and spy is not None else None,
                           "peer_ex_self_return": peer_mean, "peer_ex_self_excess_return": own-peer_mean if own is not None and peer_mean is not None else None,
                           "peer_count": len(peers), "coverage_status": "complete" if own is not None and sector is not None and spy is not None else "missing_sessions_or_benchmark",
                           "note": "Decimal returns. Excess is arithmetic percentage-point difference; peer cohort is descriptive, not business-model-neutral or causal."})
    basket_rows, history = [], []
    for basket in sorted({s["basket"] for s in universe["securities"]}):
        members = [s["ticker"] for s in universe["securities"] if s["basket"] == basket]
        for period, dates in windows.items():
            included = [ticker for ticker in members if period_return(maps.get(ticker, {}), dates) is not None]
            values = [period_return(maps[ticker], dates) for ticker in included]
            basket_return = sum(values) / len(values) if values else None
            spy = period_return(maps.get("SPY", {}), dates)
            basket_rows.append({"basket": basket, "period": period, "start_date": dates[0] if dates else None,
                                "end_date": dates[-1] if dates else None, "total_return": basket_return,
                                "spy_return": spy, "spy_excess_return": basket_return-spy if basket_return is not None and spy is not None else None,
                                "expected_members": len(members), "included_members": len(included),
                                "included_tickers": "|".join(included), "excluded_tickers": "|".join(t for t in members if t not in included),
                                "weight_convention": "equal initial weights; buy and hold; members complete throughout each stated period"})
            if period == "common_window" and included:
                for day in dates:
                    nav = 100 * sum(maps[t][day]["adjusted_close"] / maps[t][dates[0]]["adjusted_close"] for t in included) / len(included)
                    history.append({"basket": basket, "date": day, "nav": nav,
                                    "spy_nav": 100 * maps["SPY"][day]["adjusted_close"] / maps["SPY"][dates[0]]["adjusted_close"],
                                    "included_members": len(included), "included_tickers": "|".join(included)})
    return result, basket_rows, history, None


def acceptance_map(submissions):
    recent = submissions.get("filings", {}).get("recent", {})
    result = {}
    for i, accession in enumerate(recent.get("accessionNumber", [])):
        times = recent.get("acceptanceDateTime", [])
        docs = recent.get("primaryDocument", [])
        reports = recent.get("reportDate", [])
        forms = recent.get("form", [])
        result[accession] = {"accepted": times[i] if i < len(times) else None,
                             "document": docs[i] if i < len(docs) else None,
                             "report_date": reports[i] if i < len(reports) else None,
                             "form": forms[i] if i < len(forms) else None}
    return result


def fact_known_at(fact, accepted, asof):
    record = accepted.get(fact.get("accn"), {})
    if record.get("accepted"):
        when = parse_time(record["accepted"])
    elif fact.get("filed"):
        # No timestamp: conservatively allow only filings dated strictly before the cutoff date.
        when = datetime.combine(date.fromisoformat(fact["filed"]) + timedelta(days=1), day_time(), UTC)
    else:
        return None
    return when.isoformat() if when <= asof else None


def fact_url(cik, fact, accepted):
    accession = fact["accn"]
    doc = accepted.get(accession, {}).get("document") or f"{accession}-index.html"
    return f"https://www.sec.gov/Archives/edgar/data/{int(fact.get('_source_cik', cik))}/{accession.replace('-', '')}/{doc}"


def merge_company_payloads(payloads):
    """Combine only explicitly configured predecessor/successor entities; preserve fact CIKs."""
    combined = {"facts": {}}
    for cik, payload in payloads:
        for namespace, concepts in payload.get("facts", {}).items():
            for tag, concept in concepts.items():
                target = combined["facts"].setdefault(namespace, {}).setdefault(tag, {"units": {}})
                for unit, facts in concept.get("units", {}).items():
                    target["units"].setdefault(unit, []).extend({**f, "_source_cik": cik} for f in facts)
    return combined


def merge_submissions(payloads):
    combined = {"filings": {"recent": {}}}
    target = combined["filings"]["recent"]
    for payload in payloads:
        recent = payload.get("filings", {}).get("recent", {})
        count = len(recent.get("accessionNumber", []))
        for key in ["accessionNumber", "acceptanceDateTime", "primaryDocument", "reportDate", "form"]:
            target.setdefault(key, []).extend(recent.get(key, [None] * count))
    return combined


def apply_freshness(rows, accepted, asof):
    """Retain dates and provenance but withhold stale snapshot values and all derived uses."""
    reports = [r["report_date"] for r in accepted.values()
               if r.get("form") in {"10-K", "10-Q", "10-K/A", "10-Q/A"}
               and r.get("report_date") and r.get("accepted") and parse_time(r["accepted"]) <= asof]
    latest_report = max(reports, default=None)
    for row in rows:
        if not row.get("period_end") or not finite(row.get("value")):
            continue
        age = (asof.date()-date.fromisoformat(row["period_end"])).days
        kind = row["period_type"]
        outdated_report = kind in {"ttm", "instant"} and latest_report and row["period_end"] < latest_report
        stale = age > (460 if kind == "annual" else 180) or outdated_report
        if stale:
            row["note"] = (row.get("note", "") + f" Stale value withheld: period ended {row['period_end']}, age {age} days; latest available filing report date {latest_report}. Historical value {row['value']} remains in raw SEC data. Must not be used as current valuation input.").strip()
            row["value"] = None
            row["status"] = "stale_value_withheld"
    return rows


def eligible_facts(payload, tags, unit, accepted, asof):
    output = []
    for rank, tag in enumerate(tags):
        for namespace in ["us-gaap", "dei"]:
            concept = payload.get("facts", {}).get(namespace, {}).get(tag, {})
            for fact in concept.get("units", {}).get(unit, []):
                known = fact_known_at(fact, accepted, asof)
                if known is None or fact.get("form") not in {"10-K", "10-Q", "10-K/A", "10-Q/A"}:
                    continue
                if not finite(fact.get("val")) or not fact.get("end") or fact["end"] > asof.date().isoformat():
                    continue
                start = date.fromisoformat(fact["start"]) if fact.get("start") else None
                end = date.fromisoformat(fact["end"])
                output.append({**fact, "tag": tag, "rank": rank, "unit": unit, "known_at": known,
                               "duration": (end-start).days if start else None})
    return output


def fact_row(ticker, metric, kind, facts, value, cik, accepted, note=""):
    main = facts[0]
    return {"ticker": ticker, "metric": metric, "period_type": kind, "period_start": main.get("start"),
            "period_end": main["end"], "value": value, "unit": main["unit"],
            "tag": "|".join(dict.fromkeys(f["tag"] for f in facts)),
            "accessions": "|".join(dict.fromkeys(f["accn"] for f in facts)),
            "filed_dates": "|".join(dict.fromkeys(f["filed"] for f in facts)),
            "latest_known_at": max(f["known_at"] for f in facts),
            "source_urls": "|".join(dict.fromkeys(fact_url(cik, f, accepted) for f in facts)),
            "status": "observed" if len(facts) == 1 else "derived_matching_periods", "note": note}


def extract_fundamentals(payload, submissions, security, asof):
    """Long-form annual/TTM/instant facts. TTM requires same-tag matched fiscal YTDs."""
    ticker, cik = security["ticker"], security["cik"]
    accepted = acceptance_map(submissions)
    rows = []
    for metric, default_tags in FLOW_TAGS.items():
        tags = ["RevenuesNetOfInterestExpense", "RevenueNetOfInterestExpense"] + default_tags if metric == "revenue" and security.get("bank") else default_tags
        tags = security.get("metric_tags", {}).get(metric, tags)
        unit = "USD/shares" if metric == "diluted_eps" else "USD"
        candidates = eligible_facts(payload, tags, unit, accepted, asof)
        annuals = [f for f in candidates if f["duration"] is not None and 330 <= f["duration"] <= 380]
        if not annuals:
            rows.append({"ticker": ticker, "metric": metric, "period_type": "ttm", "unit": unit, "status": "missing_supported_facts", "note": "No eligible annual observation; not substituted with another economic concept."})
            continue
        def latest_compatible_end(annual):
            end = date.fromisoformat(annual["end"])
            possible = [f["end"] for f in candidates if f["tag"] == annual["tag"]
                        and f["duration"] is not None and 45 <= f["duration"] <= 315
                        and f["end"] > annual["end"] and abs((date.fromisoformat(f["start"])-end).days-1) <= 10]
            return max(possible, default=annual["end"])
        annual = max(annuals, key=lambda f: (latest_compatible_end(f), f["end"], -f["rank"], f["known_at"]))
        rows.append(fact_row(ticker, metric, "annual", [annual], annual["val"], cik, accepted))
        annual_end = date.fromisoformat(annual["end"])
        ytds = [f for f in candidates if f["tag"] == annual["tag"] and f["duration"] is not None and 45 <= f["duration"] <= 315
                and f["end"] > annual["end"] and abs((date.fromisoformat(f["start"])-annual_end).days-1) <= 10]
        if not ytds:
            rows.append(fact_row(ticker, metric, "ttm", [annual], annual["val"], cik, accepted, "TTM equals latest annual; no later matched fiscal YTD found."))
            continue
        current = max(ytds, key=lambda f: (f["end"], f["known_at"]))
        priors = [f for f in candidates if f["tag"] == annual["tag"] and f["duration"] is not None
                  and abs(f["duration"]-current["duration"]) <= 10
                  and abs((date.fromisoformat(current["end"])-date.fromisoformat(f["end"])).days-365) <= 10
                  and abs((date.fromisoformat(f["start"])-date.fromisoformat(annual["start"])).days) <= 10]
        if not priors:
            rows.append({"ticker": ticker, "metric": metric, "period_type": "ttm", "unit": unit, "status": "missing_matching_prior_ytd", "note": "Newer YTD exists but prior comparison unavailable; annual is not mislabeled current TTM."})
            continue
        prior = max(priors, key=lambda f: f["known_at"])
        row = fact_row(ticker, metric, "ttm", [current, annual, prior], annual["val"] + current["val"] - prior["val"], cik, accepted, "Latest annual + current fiscal YTD - matching prior fiscal YTD, identical XBRL tag.")
        row["period_start"] = (date.fromisoformat(prior["end"])+timedelta(days=1)).isoformat()
        rows.append(row)
    for metric, tags in INSTANT_TAGS.items():
        unit = "shares" if metric == "common_shares_outstanding" else "USD"
        candidates = [f for f in eligible_facts(payload, tags, unit, accepted, asof) if f["duration"] is None]
        if candidates:
            selected = max(candidates, key=lambda f: (f["end"], -f["rank"], f["known_at"]))
            rows.append(fact_row(ticker, metric, "instant", [selected], selected["val"], cik, accepted))
        else:
            rows.append({"ticker": ticker, "metric": metric, "period_type": "instant", "unit": unit, "status": "missing_supported_facts"})
    return apply_freshness(rows, accepted, asof)


def derived_metrics(rows, security, last_price):
    indexed = {(r["metric"], r["period_type"]): r for r in rows if finite(r.get("value")) and not str(r.get("status", "")).startswith("stale")}
    output = []
    def derive(metric, value, unit, sources, note):
        main = sources[0]
        output.append({"ticker": security["ticker"], "metric": metric, "period_type": "derived",
                       "period_start": main.get("period_start"), "period_end": main.get("period_end"), "value": value,
                       "unit": unit, "tag": "derived", "accessions": "|".join(dict.fromkeys(r.get("accessions", "") for r in sources)),
                       "filed_dates": "|".join(dict.fromkeys(r.get("filed_dates", "") for r in sources)),
                       "latest_known_at": max(r.get("latest_known_at", "") for r in sources),
                       "source_urls": "|".join(dict.fromkeys(r.get("source_urls", "") for r in sources)),
                       "status": "derived_proxy", "note": note})
    revenue = indexed.get(("revenue", "ttm"))
    operating = indexed.get(("operating_income", "ttm"))
    cashflow = indexed.get(("operating_cash_flow", "ttm"))
    capex = indexed.get(("cash_capex", "ttm"))
    eps = indexed.get(("diluted_eps", "ttm"))
    shares = indexed.get(("common_shares_outstanding", "instant"))
    fcf = None
    if not security.get("bank"):
        if revenue and operating and revenue["value"] != 0 and (revenue["period_start"], revenue["period_end"]) == (operating["period_start"], operating["period_end"]):
            derive("operating_margin_ttm", operating["value"] / revenue["value"], "ratio", [operating, revenue], "GAAP operating income / revenue for matching TTM periods; business mix matters.")
            derive("ebit_effect_100bp_margin", revenue["value"] * .01, "USD", [revenue], "Arithmetic sensitivity only: 100bp operating-margin change at constant revenue; not a forecast or policy elasticity.")
        if cashflow and capex and capex["value"] >= 0 and (cashflow["period_start"], cashflow["period_end"]) == (capex["period_start"], capex["period_end"]):
            fcf = cashflow["value"]-capex["value"]
            derive("cash_fcf_proxy_ttm", fcf, "USD", [cashflow, capex], "Operating cash flow minus cash purchases of PPE. Excludes finance-lease additions and acquisitions; not management adjusted FCF or equity distributable cash.")
    if last_price:
        note = f"Raw close {last_price['close']} on {last_price['date']}; latest vendor history, not an archived valuation snapshot."
        if eps and eps["value"] > 0:
            derive("trailing_pe_proxy", last_price["close"] / eps["value"], "multiple", [eps], note + " Uses matching-tag reconstructed diluted EPS; inspect split/share-basis and one-off effects.")
        if shares and shares["value"] > 0:
            capitalization = shares["value"] * last_price["close"]
            derive("market_cap_proxy", capitalization, "USD", [shares], note + f" Uses reported common shares as of {shares['period_end']}; not current diluted market capitalization.")
            if fcf is not None:
                derive("cash_fcf_yield_proxy", fcf / capitalization, "ratio", [cashflow, capex, shares], note + " CFO-minus-cash-PPE / reported-share market-cap proxy; no bank FCF proxy.")
    return output


def run(out, asof, start, universe_path):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    universe = json.loads(Path(universe_path).read_text(encoding="utf-8-sig"))
    tickers = [s["ticker"] for s in universe["securities"]] + universe["benchmarks"]
    if len(tickers) != len(set(tickers)):
        raise ValueError("Duplicate security/benchmark tickers")
    fetcher = PublicCache(out)
    price_rows, price_status, fundamental_status, fundamentals = {}, [], [], []
    try:
        for ticker in tickers:
            errors = []
            for host in ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]:
                try:
                    response = fetcher.get(f"yahoo_{ticker}", f"https://{host}/v8/finance/chart/{ticker}", {
                        "period1": int(datetime.combine(start-timedelta(days=55), day_time(), UTC).timestamp()),
                        "period2": int(asof.timestamp())+1, "interval": "1d", "events": "div,splits"})
                    rows, excluded = parse_prices(response["data"], ticker, asof, {k: response[k] for k in ["retrieved_at", "raw_file"]})
                    price_rows[ticker] = rows
                    price_status.append({"ticker": ticker, "status": "ok" if rows else "empty", "rows_with_lookback": len(rows),
                                         "requested_rows": sum(r["date"] >= start.isoformat() for r in rows),
                                         "first_date": rows[0]["date"] if rows else None, "last_date": rows[-1]["date"] if rows else None,
                                         "excluded_unfinished_session_bars": excluded, "raw_file": response["raw_file"]})
                    print(f"price {ticker}: {len(rows)} completed bars", flush=True)
                    break
                except (RuntimeError, ValueError, KeyError) as exc:
                    errors.append(str(exc))
            else:
                price_rows[ticker] = []
                price_status.append({"ticker": ticker, "status": "error", "errors": errors})
        returns, baskets, history, return_error = calculate_returns(price_rows, universe, start)
        prices = []
        spy_dates = sorted(row["date"] for row in price_rows.get("SPY", []))
        for ticker, rows in price_rows.items():
            lookup = {r["date"]: r for r in rows}
            for row in rows:
                if row["date"] < start.isoformat():
                    continue
                daily = None
                if row["date"] in spy_dates:
                    i = spy_dates.index(row["date"])
                    if i and spy_dates[i-1] in lookup:
                        daily = row["adjusted_close"] / lookup[spy_dates[i-1]]["adjusted_close"]-1
                prices.append({**row, "adjusted_return_1d": daily})
        for security in universe["securities"]:
            if not security.get("fundamentals"):
                continue
            ticker, cik = security["ticker"], security["cik"]
            try:
                payloads, submissions, raw_files, acceptance_status = [], [], [], "available_recent_filings"
                for source_cik in [cik] + security.get("supplemental_ciks", []):
                    suffix = "" if source_cik == cik else "_successor"
                    company = fetcher.get(f"sec_facts_{ticker}{suffix}", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{source_cik}.json")
                    payloads.append((source_cik, company["data"]))
                    raw_files.append(company["raw_file"])
                    try:
                        submissions.append(fetcher.get(f"sec_submissions_{ticker}{suffix}", f"https://data.sec.gov/submissions/CIK{source_cik}.json")["data"])
                    except RuntimeError:
                        acceptance_status = "partly_unavailable_conservative_filing_date_filter"
                selected = extract_fundamentals(merge_company_payloads(payloads), merge_submissions(submissions), security, asof)
                last = price_rows.get(ticker, [])[-1] if price_rows.get(ticker) else None
                selected += derived_metrics(selected, security, last)
                fundamentals.extend(selected)
                fundamental_status.append({"ticker": ticker, "status": "ok", "rows": len(selected),
                                           "missing_metrics": sum(str(r.get("status", "")).startswith("missing") for r in selected),
                                           "stale_metrics_withheld": sum(r.get("status") == "stale_value_withheld" for r in selected),
                                           "acceptance_timestamps": acceptance_status, "raw_files": raw_files})
                print(f"fundamentals {ticker}: {len(selected)} rows", flush=True)
            except (RuntimeError, ValueError, KeyError) as exc:
                fundamentals.append({"ticker": ticker, "status": "collection_error", "note": str(exc)})
                fundamental_status.append({"ticker": ticker, "status": "error", "error": str(exc)})
    finally:
        fetcher.close()
    write_csv(out/"equity_prices.csv", prices, PRICE_FIELDS)
    write_csv(out/"equity_returns.csv", returns, RETURN_FIELDS)
    write_csv(out/"company_fundamentals.csv", fundamentals, FUND_FIELDS)
    write_csv(out/"equity_baskets.csv", baskets, ["basket", "period", "start_date", "end_date", "total_return", "spy_return", "spy_excess_return", "expected_members", "included_members", "included_tickers", "excluded_tickers", "weight_convention"])
    write_csv(out/"equity_basket_history.csv", history, ["basket", "date", "nav", "spy_nav", "included_members", "included_tickers"])
    manifest = {"schema_version": 1, "completed_at": utcnow(), "asof_utc": asof.isoformat(), "requested_start": start.isoformat(),
                "universe_path": str(Path(universe_path).resolve()), "universe_sha256": hashlib.sha256(Path(universe_path).read_bytes()).hexdigest(),
                "securities_declared": len(universe["securities"]), "benchmarks_declared": len(universe["benchmarks"]),
                "requested_price_rows": len(prices), "return_rows": len(returns), "fundamental_rows": len(fundamentals),
                "price_collections": price_status, "fundamental_collections": fundamental_status, "return_error": return_error,
                "status": "complete_with_documented_metric_gaps" if not return_error and all(r["status"] == "ok" for r in price_status+fundamental_status) else "partial",
                "requests": fetcher.records,
                "limitations": ["Current vendor adjusted history; not a historical point-in-time data archive.",
                                "Regular NY close required. Frozen 2026-09-08 15:07 UTC excludes the entire Sep8 daily bar; Sep7 market holiday is naturally absent from observed SPY sessions.",
                                "Common-window return begins at first completed daily close on/after requested start, not an intraday Kalshi timestamp.",
                                "Long-only equal-initial-weight illustrative cohorts; current economic selection, not an investable index or licensed MSCI benchmark.",
                                "Relative returns and peer differences do not identify causal election effects or investor positioning.",
                                "SEC facts selected by availability before cutoff, preserving tag, unit, fiscal period and accession; same-day filing without acceptance timestamp excluded.",
                                "TTM requires same-tag annual and matched current/prior fiscal YTDs. Missing metrics are never imputed.",
                                "Snapshot values older than 180 days (annual 460 days), or earlier than the latest available 10-Q/10-K report date, are withheld; raw historical facts remain preserved.",
                                "Explicitly configured Exxon predecessor and July 2026 successor entity facts are merged with per-fact CIK provenance; no generic entity linkage is inferred.",
                                "Cash FCF excludes finance-lease additions/acquisitions; bank FCF and operating-margin valuation are not constructed.",
                                "Reported-share market cap, trailing P/E and FCF yield are simple dated proxies, not fair values or buy recommendations."]}
    manifest["files"] = {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in out.glob("*.csv")}
    (out/"collection_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out/"universe_used.json").write_text(json.dumps(universe, indent=2), encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "price_rows": len(prices), "fundamental_rows": len(fundamentals)}), flush=True)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--asof", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--universe", default="config/midterm_equity_universe.json")
    args = parser.parse_args(argv)
    asof, start = parse_time(args.asof), date.fromisoformat(args.start)
    if start > asof.date():
        parser.error("start must not follow asof")
    run(args.out, asof, start, args.universe)


if __name__ == "__main__":
    main()
