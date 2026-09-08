<div class="eyebrow">MIDTERMS 2026 · FUNDAMENTAL EQUITY RESEARCH</div>

# What the election agenda is changing—and what equity prices can support

<p class="dek">A public-data study of issue attention, political probabilities, execution flow and company valuation. Built for a US long-only portfolio with a 12–24 month horizon.</p>

<div class="meta">Information cutoff: 8 September 2026, 15:07 UTC · Equity closes through 4 September · Research assembled 9 September · All probabilities below refer to this frozen vintage.</div>

<div class="metrics"><div><b>6,032</b><span>research contracts</span></div><div><b>1.28m</b><span>hourly observations</span></div><div><b>21,585</b><span>public trade prints</span></div><div><b>43</b><span>equity session dates</span></div><div><b>7</b><span>company underwriting cases</span></div></div>

## The investment conclusions

**The most useful signal is a change in who bears costs, rather than a simple change in party control.** Affordability concerns connect gasoline, electricity, housing and healthcare, but the policy responses redistribute costs differently. State utility tariffs can protect households while allowing projects to proceed; tighter financing can impair construction even when demand remains strong; healthcare enrollment pressure can affect payers and providers differently. A single “midterm winner” equity basket would hide those distinctions.

Five conclusions deserve portfolio attention:

1. **Prioritize rates and affordability in the risk review.** Continuing Fed-related open interest rose 124%, while the next-decision contract trades much more actively than the selected policy contracts. This is evidence of increased participation around monetary uncertainty, not proof that elections caused it or that participants collectively expect hikes. Energy has already outperformed; assessing additional inflation protection requires testing its current valuation and the portfolio's actual exposure.
2. **Separate data-center exposure into contracts, locations and valuation.** Opposition crosses party lines, but the observed state responses are heterogeneous. Existing generation, regulated utilities, cooling/electrical equipment and hyperscalers have different earnings sensitivities. Ratepayer protections can be enabling if they create an investable connection agreement. A generalized sell-everything “techlash” thesis is too coarse.
3. **Do not confuse a thematic beneficiary with an attractive entry price.** In the explicitly designed central cases, VRT and WMT need entry prices roughly 25% below the observed closes to meet a 10% annual price-return hurdle. That is an underwriting sensitivity—not a target-price forecast. Both could justify higher prices through stronger growth or sustained multiples, but the required operating evidence must be explicit.
4. **Treat thin political-policy prices as research alerts.** The AI-framework contract traded only nine times over four weeks; one print supplied 70% of volume. The three-state moratorium contract had 65 prints. Those odds can identify a proposition worth investigating, but should not set precise portfolio weights.
5. **Retain statistical discipline even when an apparent edge is attractive.** Fourteen primary relationships pass a conventional HAC/multiple-testing screen, yet none passes the combined liquidity, variation and robustness requirements for sizing. The estimates remain useful exposure hypotheses; they do not establish a calibrated election hedge.

These conclusions are strongest as a stock-selection and risk-budgeting framework. No actual holdings, proprietary forecasts or option chain are supplied, so this report does not calculate a portfolio-specific hedge or claim a current option premium is attractive.

[TOC]

## Research scope and what was actually completed

This is a completed implementation on an audited research cohort, with an executable public-tape collector, issue analytics, electoral evidence, return models, company scenarios and this report. The wider creation inventory contains 61,018 classified contracts. The original all-category extraction is a separate, larger job and is **not represented as complete here**.

The common hourly request spans 8 July–8 September. Activity comparisons use complete UTC days; the main latest window is 25 August–7 September against the preceding 28 days, normalized per day. The execution tape uses 11 August–7 September across 13 selected scenarios. Equity models use 43 dates, supplying at most 42 adjacent returns. Millions of hourly observations do not turn those 42 returns into a large independent sample.

The public evidence comprises 29 primary-source records, ten comparable poll trends and five deliberately selected state cases. It is a structured evidence packet, not a census of media coverage, campaign advertisements, voters or state laws. The generic portfolio framework uses sector ETFs and transparent small baskets; it does not reproduce MSCI ACWI's constituent portfolio.

{{political_narrative}}

## What Kalshi activity adds to the electoral evidence

### New listings mostly reveal the platform's product schedule

The first test asks whether a theme has more tradable propositions, more distinct events or simply more thresholds per event. Energy macro added **13,566 contracts across 2,114 events** in the latest 14 days. Of those events, **2,071 belonged to previously observed series families**. WTI hourly contracts and 15-minute WTI/natural-gas events dominate the count. This is economically relevant market coverage, but a weak standalone measure of campaign salience.

Electricity macro contract issuance rose **14.86×** on a per-day basis, yet **149 of 156 new contracts** belonged to twelve routine ERCOT daily events. Only seven concerned one data-center-construction event. An unadjusted detector would rank this as an extraordinary political-attention surge. Its main input would actually be listing cadence.

{{creation_chart}}

The practical use of creation is therefore narrower: send a genuinely different **policy proposition** for analyst review, inspect its rule and institution, and wait for evidence of trading adoption and electoral relevance. Even “first observed series” means first appearance in this archive; it does not establish that Kalshi created an entirely new topic that day.

### Continuing participation is growing, but concentration changes the conclusion

We compare the same covered contracts across both windows, calculate family-level trading rates and then summarize across families. The table also reports continuing-contract OI. The two measures have different weighting and should not be combined into one opaque score.

{{attention_table}}

{{attention_chart}}

Fed participation is the clearest large-scale change: continuing OI increased **19.308 million contracts, or 124.2%**. Seven of nine qualifying families had higher average volume rates; all nine had higher average log activity, which reduces the influence of exceptional trading days. The median family volume-rate ratio was **2.58×**. Nevertheless, Fed-decision markets supplied **90.7%** of eligible volume. The approaching meeting is an obvious competing explanation to “the election agenda has changed.”

Inflation macro continuing OI increased **525,609 contracts, or 218.3%**, with four of five families showing higher average log activity. Its broad-activity classification changes when coverage requirements move between 70%, 80% and 90%. Data-center policy continuing OI rose **9,581 contracts, or 67.2%**, but only two families qualified and moratorium-count contracts supplied **99.49%** of their volume. The evidence supports increased interest in a concentrated proposition, not a measured nationwide electoral surge.

We also inspect event lifecycle. The main Fed-decision continuing-panel ratio is 3.48×, whereas a separate scheduled-lead comparison is 1.78×; CPI's corresponding diagnostics are 2.14× and 0.53×. These are different estimators and only one and two earlier events support the lead comparisons. They cannot identify the correct “adjusted” effect, but they show why a strong election-salience claim would be premature.

The five repeated-launch comparisons meeting the minimum coverage/event requirements all show lower recent launch activity. Trump's energy-mention family, for example, falls to 0.19× on the first two complete UTC days after creation, across four recent versus seven earlier events. This does not refute voter concern: changing speech schedules, launch timing and market selection make launch adoption a different object from issue priority.

### Raw OI can tell the opposite story from continuing contracts

Observed policy-market OI fell from **9.587m to 7.964m contracts**. A dashboard using that total would show a 16.9% contraction. Yet continuing contracts added **748,074 contracts (+10.62%)**. Entries added 171,918 and departures removed 2.543m; 2.465m of the departed OI has corroborated settlement dates. Much of the apparent withdrawal was market lifecycle.

{{oi_table}}

The accounting identity is exact apart from floating-point rounding: total change equals continuing change plus observed entries minus observed exits. “Entry” need not mean a new listing, and “exit” need not mean settlement unless dates corroborate it. The decomposition matters because an expiring busy market can hide growing outstanding exposure in the remaining policy propositions.

OI still does **not** measure bullish money. Every outstanding matched contract contains opposing economic exposures, and a contract's face value is not the amount of investor capital newly committed. Changes in price, participation and trade initiation must be presented separately.

## What the prices and execution tape are saying

{{odds_paths_chart}}

The frozen snapshot contains materially different propositions: a Democratic House win at 84.5%; a Democratic Senate win at 47.5%; at least three binding statewide data-center moratoriums by year-end at 67%; election-day gasoline above $4 at 40.5%; exactly a 25 bp September Fed hike at 52.5%; and an end-2027 Fed upper bound above 3.75% at 69%. These are separate marginal prices with different deadlines and rules. They are not a coherent joint probability tree.

The moratorium price rose 36.5 percentage points versus its 28-day comparison; gasoline above $4 rose 14 points. Both warrant review, but their interpretation changes when execution depth is visible. The source snapshot allows wider diagnostic quote spreads than the return models; every regression independently applies its stricter endpoint gates.

{{tape_table}}

{{tape_chart}}

Three tape results are particularly informative:

- **The next Fed decision is a much more substantial trading market.** The exactly +25 bp contract records 10,349 executions and 5.786m traded contracts across all 28 days. The largest print is only about 0.84% of volume. YES-taker share is roughly 50.6%, despite the rise in broader Fed OI. That looks like substantial two-sided trading participation; it is not evidence of a one-way institutional hike position.
- **AI-policy precision is difficult to defend.** Nine prints total just 318.81 contracts; the largest print is 223.4, and the top five contribute 97.6%. An apparent regression relationship with this probability can be highly sensitive to a sparse quote process. A 12.5% quoted chance of a framework before 2027 cannot be generalized to the entire AI regulatory environment, especially when the rule includes a qualifying voluntary framework.
- **Trade initiation can disagree with a price change.** The gasoline contract's overall YES-taker share is only 30.8%, while its comparable end-of-day midpoint rises from 35% on 10 August to 40.5% on 7 September. A smaller amount of aggressive YES demand can interact with changing offers, while NO trades occur at different levels and times. Aggregate signed turnover is not a price forecast. The 10 August endpoint precedes the tape window; it is used only to frame its interval change.

The moratorium market's 65 prints total 4,915.49 contracts; five prints account for 50.9%. Pennsylvania's governor contract is also concentrated: one roughly 30,000-contract print accounts for 57.9% of its 28-day volume. A high election probability should not be mistaken for deep execution support in that particular contract.

The public API supplies taker direction and a block-trade flag, but not trader identity or individual holdings. All observed prints in this selected tape are flagged non-block. That does not imply small investors: large orders can be split, and the exchange's block flag describes execution type. Tape and hourly quantities reconcile on all **122 fully comparable 24-hour contract-days**, with no material mismatch. [Kalshi trade documentation](https://docs.kalshi.com/api-reference/market/get-trades), [order-direction definitions](https://docs.kalshi.com/getting_started/order_direction).

## Where political developments and equities tell different stories

{{equity_chart}}

There is no uniform electricity/data-center equity response in the observed period. CEG gains **22.5%**, VST falls **3.6%**, regulated utilities AEP and DUK fall **7.7% and 4.4%**, VRT falls **11.7%**, and ETN gains **3.1%**. MSFT gains **30.6%** while GOOGL falls **6.4%**. Meanwhile XLE gains **15.2%**, against SPY's **3.3%**. These are observed returns, not estimates of the effect of policy news.

The disagreement is useful because it forces a more specific fundamental question. If permitting becomes harder, already operating capacity can become scarcer, while equipment delivery may be delayed. If large-load customers fund grid costs under enforceable contracts, a regulated utility's risk can improve even as its political scrutiny increases. If higher rates dominate, a fundamentally sound project can still have a lower equity value. Company news, earnings and starting valuations remain alternative explanations for every observed divergence.

### A stronger Fed signal does not settle the growth-versus-inflation debate

The July Fed statement held the target range at 3.50–3.75%; three dissenters preferred a 25 bp increase. The September jobs release reports 162,000 August payroll growth and 4.1% unemployment, with weak revised June/July gains. This combination does not justify assuming a clean soft landing, an imminent recession or politically induced easing. [Federal Reserve July statement](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm). [BLS August employment release](https://www.bls.gov/news.release/archives/empsit_09042026.htm).

The portfolio should distinguish four channels:

| Economic interpretation | Election connection to examine | Earnings/valuation implication | Practical response |
|---|---|---|---|
| Persistent supply inflation | Pressure for gasoline, utility and import-cost relief | Energy realizations may hold up; household spending and discount rates remain pressured | Review aggregate energy underweight and rate-sensitive holdings together; avoid chasing an already strong energy rally without normalization stress |
| Disinflation with stable demand | Incumbents can emphasize relief; demands for broad intervention may moderate | Housing affordability and valuation duration may improve, but margins and orders still decide earnings | Stage additions only when company order quality and financing conditions confirm the mechanism |
| Growth weakening before prices normalize | Competing demands for relief, spending and restraint | Lower rates can coexist with credit losses, weaker sales and higher equity risk premiums | Prefer balance-sheet resilience; do not equate cuts with automatic bank or cyclical upside |
| Relief through cost reallocation | State tariffs, reimbursement, subsidies and permitting conditions | Gains and losses occur within sectors and along value chains | Select companies using contractual cost recovery and exposure, rather than a single party-control factor |

The end-2027 rate contract is useful context but has only 19 valid adjacent equity observations under the model gates. Its 69% threshold price does not yield a reliable expected policy rate: a tail probability is not a complete distribution, and separate thresholds may be incoherent. Housing also depends on long yields, term premium and mortgage spreads; a change in the overnight path is only one component.

### What the empirical sensitivities can—and cannot—support

We fit simple daily return models using probability **changes**, with and without contemporaneous SPY return. Quotes must be fresh enough at both ends of consecutive equity sessions. The baseline uses a maximum 10-point spread, with 5/15-point alternatives. We report HC3 and HAC uncertainty, a five-session block bootstrap, removal of the largest odds move, and corrections across the full primary testing family.

There are **230 primary combinations**, of which **184 are estimable**. Fourteen have HAC-based, multiple-testing-adjusted q-values below 5%. Using the larger HC3/HAC p-value before the same adjustment leaves **three** below 10%; all three involve the sparse AI-framework or moratorium tapes. **Zero** pass the complete sizing screen. The screen also requires adequate observed moves, robust intervals and a credible execution tape. Its thresholds are analyst safeguards, not a historically calibrated trading strategy.

{{beta_table}}

{{beta_chart}}

The House–HCA association is the most interpretable example of both promise and restraint. The market-conditioned estimate is approximately **+0.89% HCA return per +1 percentage-point House-D probability move**. It has 42 paired daily changes, a positive block-bootstrap interval and a stable sign across spread gates and omission of the largest move. But the observed House probability range is only **81.5–85.5%**, the largest daily move is one point, none clears the quote-noise rule, and conservative multiple-testing q is about **0.141**. HCA alone is not a representative census of hospitals. This is a research hypothesis about healthcare policy exposure, not an election-sized return forecast.

The AI-framework/generation relationship has a still larger numerical coefficient, but the underlying odds vary across only four points, no move clears the quote-noise rule, the tighter spread sample collapses and the tape is extremely thin. The apparent “precision” should reduce confidence in mechanical inference rather than prompt a bigger trade. Similar problems affect several moratorium sensitivities.

The September hike–energy association is positive: about **+0.064% sector return per +1pp hike-probability move**, with a positive block-bootstrap interval but a conservative q near **0.403**. A common inflation or energy-supply shock could raise both energy equities and hike odds. It would be incorrect to conclude that a Fed hike causes oil shares to rise. The data support monitoring a shared exposure, not a causal hedge coefficient.

{{diagnostic_table}}

{{sensitivity_explorer}}

Every estimable coefficient is independently verified through covariance or residualization, with maximum numerical discrepancy {{numerical_error}}. That verifies arithmetic, not causal identification. The code also exports chronological holdout residuals and the five largest probability moves. These are exploratory discrepancy/event tables. Registered public releases overlap several intervals—for example Wisconsin's large 12 August move follows the 11 August primary—and absence of a registered event does not mean a clean news day.

## Converting the political thesis into a 12–24 month investment decision

### Require the entry price to survive the thesis

The underwriting model deliberately separates long-run fundamentals from the short-run regressions. Starting EPS and financial facts come from company releases/SEC filings; lower, central and upper earnings-growth and exit-multiple combinations are **analyst-designed assumptions**. There are no inferred scenario probabilities and no consensus-forecast claim.

{{valuation_table}}

{{hurdle_chart}}

For VRT, the observed starting P/E is about **63.5×**. With 20% annual EPS growth and a 40× exit multiple, the 24-month price return is **−9.2%**. Meeting a 10% annual price-return hurdle at that exit multiple requires approximately **38.6% EPS CAGR**, or a starting price around **$210.41**, versus the observed $280.53. The operational debate must therefore concern the durability and conversion of growth, not merely whether data centers continue being built.

WMT presents the same valuation issue through a different mechanism. Affordability pressure may support traffic and trade-down, while labor, merchandise costs and pricing constrain margins. Starting around **38.8×** earnings, an 8% EPS-growth/30× exit case produces **−9.9%** over 24 months. A 10% annual price hurdle requires about **25.1% EPS CAGR**, or a starting price of **$79.82** under that same central case, versus $107.14 observed. Defensive sales are not sufficient evidence of defensive share returns.

MSFT is modeled with EPS **$17.28**, after removing the disclosed $0.67 OpenAI investment gain from reported $17.95. A 14% growth/26× exit combination produces **+16.8%** over two years, still below the 21% cumulative price return corresponding to a 10% annual hurdle. The adjustment is a single disclosed normalization, not a comprehensive estimate of recurring earnings. [Microsoft FY2026 release](https://www.microsoft.com/en-us/investor/earnings/fy-2026-q4/press-release-webcast).

{{valuation_chart}}

The XOM downside case tests simultaneous earnings normalization and multiple compression; oil-sector cyclicality can make that combination more severe than a typical through-cycle valuation response. DHI's apparent low P/E does not settle the quality of cyclical earnings: cancellation rates and incentives matter. UNH requires a cost/reimbursement recovery before its underperformance becomes evidence of value. JPM's outcome depends on asset yields, deposits, credit and loan demand, not just the sign of a rate move. The complete scenario configuration records these assumptions and invalidation conditions.

{{fundamental_table}}

{{company_source_links}}

At constant revenue, a 100 bp consolidated EBIT-margin change equals approximately **22.6% of WMT EBIT** and **20.8% of UNH EBIT**. This is an accounting sensitivity, not an estimate of an actual tariff or medical-cost shock. UNH's group EBIT margin is not its medical cost ratio. Company mix, taxes, financing and share count must be added before treating the result as EPS sensitivity. Cash FCF yield here is (CFO minus cash capital expenditure) divided by the market-cap proxy; lease additions and normalization are not fully captured, and the bank measure is withheld.

{{scenario_calculator}}

### Portfolio actions and decision gates

| Priority | Action for a long-only equity book | What must be true before increasing exposure | What would invalidate the view |
|---|---|---|---|
| 1 · Risk review now | Review combined exposure to higher rates, household cost pressure and capital-intensive growth; look through broad sector labels | The portfolio's earnings and financing assumptions remain viable under the higher-for-longer scenario | Falling inflation with resilient demand, or company-level insulation stronger than modeled |
| 2 · Electricity selection | Distinguish contracted existing capacity, regulated grid investment and equipment delivery; assess location and who funds connections | Enforceable cost recovery, credible power availability and project conversion justify the valuation | Tariff recovery fails, customer credit weakens, utilization disappoints or restrictions reach existing economics |
| 3 · Valuation discipline | Put VRT/WMT on an evidence-and-price watchlist; use the calculator to record the growth required by the entry price | Operating evidence supports the chosen growth/multiple combination and the return hurdle | Sustained margins/mix or faster growth justify a higher multiple; the central case must then change transparently |
| 4 · Energy hedge review | Assess whether existing energy exposure offsets inflation/supply risk; avoid treating recent sector strength as an automatic new-buy signal | Sustainable earnings and balance-sheet cash generation protect the normalized downside case | Supply normalizes faster, oil realizations weaken or offsetting corporate risks dominate |
| 5 · Healthcare differentiation | Review payer/provider reimbursement and enrollment exposure; use HCA association only to prioritize research | Cost, utilization and funding evidence explains company-level earnings better than the generic sector story | Corporate execution overwhelms policy exposure or the proposed funding mechanism never becomes implementable |
| 6 · Housing patience | Require mortgage-payment improvement, cancellations and incentive expense to corroborate any rates-driven opportunity | Demand recovery survives margin/land-cost stress | Cuts accompany employment deterioration or mortgage spreads remain elevated |
| 7 · Conditional hedging | Compare broad/sector put spreads or collars with reducing exposed holdings, after obtaining live option prices | Downside scenario, current premium, liquidity and carry make protection worthwhile | Protection cost exhausts expected risk reduction, or basis risk fails the intended exposure |

These are review and underwriting actions, not trade instructions at the frozen historical prices. The immediate implementation is to rank holdings by relevant business exposure and valuation tolerance, then use current market data before execution. There is no defensible basis here for sizing a hedge directly from a thin Kalshi contract's regression beta.

For a global framework, the same channels reach non-US firms through US sales, dollar financing, commodity costs and US project exposure. This study does not estimate non-US stock betas or currency-adjusted portfolio returns, so it makes no numerical global-rotation claim.

## Audit trail, limitations and reproduction

The HTML is self-contained: charts, tables, model explorer and scenario calculator work offline. All derived CSVs, input hashes, source registry, definitions and code are included in the separate programme folder. The complete [research blueprint](../BLUEPRINT.md) specifies estimands, transformations, accounting identities, confidence gates and extension requirements; the [README](../README.md) gives execution commands.

| Validation | Result and practical meaning |
|---|---|
| Frozen hourly source reconciliation | 1,276,909 rows match per-market audited counts; source identity/windows and successful artifacts checked |
| Daily/session integrity | No duplicate market-days, no negative observed OI/volume, no quote after equity-session cutoff |
| Public execution completeness | All cursors exhausted for 13 specified contracts; 21,585 unique executions with canonical direction fields |
| Candle/tape reconciliation | 122 complete comparable days, no material volume discrepancy |
| OI decomposition | Identity residual within floating-point rounding; incomplete observations remain explicit |
| Regression arithmetic | All 1,242 estimable coefficients independently reproduced; uncertainty and identification remain separate questions |
| Sizing reliability | None of the 230 specified primary relationships clears all diagnostics |
| Long-horizon valuation | 42 designed cases, seven issuers × three cases × two horizons; no dividends or probability weighting |

The main limitations are selection of the research cohort, a short equity sample, retrospective taxonomy/basket choices, sparse and possibly unchanged underlying quotes, missing historical book depth, no investor identities, and unresolved common-news confounding. The public evidence is selected rather than exhaustive. Historical company facts and adjusted prices are retrieved vintages, not a complete point-in-time archive. No apparent relationship is presented as a proven causal effect or a backtested investment strategy.

The next evidence that could change the portfolio view is concrete: repeated comparable voter-priority readings; binding state cost-allocation decisions; company project conversion and margin disclosures; clearer distinction between inflation-driven and growth-driven changes in rates; and a longer, genuinely out-of-sample record of liquid political-probability shocks. Until then, the most defensible advantage is to combine precise policy interpretation with disciplined company underwriting.

<details><summary>Open the dated primary-source registry</summary>

{{primary_source_links}}

</details>
