# QuonTech daily performance tracker

`portfolio.html` shows, in the Performance section:

- **Live block**: return since 16 Oct, portfolio value, annualised volatility, annualised Sharpe ratio, daily chart (index 16 Oct = 100) with hover.
- **"At each round" table**, as asked by the AMC 2026 guidelines: each round on its own period
  (16 Oct-6 Nov for the Round 2 report, 6 Nov-27 Nov for the Round 3 report, 27 Nov-27 Dec),
  plus the whole challenge 16 Oct-27 Dec, which is the basis of the ranking.

All numbers come from `data/portfolio_performance.json`, written by the GitHub Action
`.github/workflows/quontech-daily-nav.yml` (Tuesday to Saturday, 06:17 UTC, plus manual run).

## Method

| Measure | Definition |
|---|---|
| NAV | Bloomberg basket values at the period's reference close x (vendor close / vendor reference close), + cash + dividends on ex-date. Periods chain-linked. |
| Return | NAV at end of window / NAV at start of window - 1 (compounded) |
| Volatility | sample st. dev. of daily returns x sqrt(252) |
| Sharpe | (mean daily return - mean daily risk-free) / st. dev. of daily returns x sqrt(252) |
| Risk-free | 1-month US T-bill, FRED `DGS1MO` (comparable to Bloomberg `USGG1M`); yield known at the previous close / 252 per daily return, the daily analogue of the Round 1 code (`USGG1M` shifted / 12) |

Sources of the convention: Round 1 guidelines, section 5 (SR = (mean R_P - mean R_f) / sd(R_P)); Class 2 notes (annualisation: mean x n, sd x sqrt(n), SR x sqrt(n)); Class 2 slides and TA Lecture 1 (n = 252 for daily data). Same structure as the team's Round 1 files: Quontech_Excel.xlsx (RETURNS!N = USGG1M of the previous month-end / 100 / 12; STATS: AVERAGE x 12, STDEV.S x SQRT(12), Sharpe = mean excess / vol) and Quontech_code.ipynb (annual_stats); here the period is one trading day instead of one month. Shares and cash in data/portfolio_config.json match the Tri-Force sheet (residual cash USD 1,134.93). Volatility and Sharpe are shown only from 5 daily returns. These are **estimates**: the
official AMC figures are Bloomberg's (PORT), and the TA may annualise differently.

## Setup (once)

1. Copy everything into the repository root and push.
2. Settings > Actions > General > Workflow permissions: **Read and write**.
3. Actions > "QuonTech - validated daily NAV" > **Run workflow**. Read the log.

## When the holdings change (Round 2 on 6 Nov, Round 3 on 27 Nov)

After the close of `valid_until` (6 Nov) the tracker **freezes** by design: it does not
value the old ETFs as if nothing had changed. To resume, add a new object to `periods`
in `data/portfolio_config.json`:

```json
{
  "label": "Round 2",
  "reference_date": "2026-11-06",
  "valid_until": "2026-11-27",
  "cash_usd": 0.00,
  "positions": [
    {"ticker": "IVV", "shares": 10000, "official_basis_usd": 7666700.00, "official_reference_close": 766.67},
    {"ticker": "MSFT", "shares": 5000, "official_basis_usd": 2500000.00, "official_reference_close": 500.00}
  ]
}
```

Rules checked by the script: `reference_date` = previous `valid_until`; shares are
integers; `official_basis_usd` = shares x Bloomberg close (within USD 1). Use the
**complete** portfolio after the trades (all positions, not only the new ones), with
Bloomberg closes of the reference date, and the cash left in PRTU. Then run the workflow.
Use valid_until "2026-12-27" for the last period.

## Fail-closed checks

Missing ticker on a date, vendor reference price >5% from Bloomberg, split, suspicious
dividend, >10% daily NAV move, stale prices or T-bill rate, revision >10 bps of a
published NAV: the run stops and the last valid file stays online.

## Data sources and licence

Prices: Yahoo Finance via the unofficial `yfinance` (personal-use terms: review before
public use; only aggregated portfolio values are published, never raw prices).
Risk-free: FRED (Federal Reserve Bank of St. Louis), public CSV, no API key.

## Local test

`python -m unittest discover -s tests -v` (no network needed).
