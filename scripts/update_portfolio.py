#!/usr/bin/env python3
"""Conservative EOD performance rebuild for the QuonTech portfolio.

NO prices are fabricated. The workflow fails closed on missing, stale or anomalous
vendor data, so the last valid file stays published.

Valuation convention
  * The portfolio is a chain of holding periods (Round 1 SAA, then Round 2, ...).
    Each period is defined by official Bloomberg values at its reference close.
  * Within a period, each position is marked as
        official_basis_usd * vendor_close_t / vendor_close_reference
    so that differences between Bloomberg and vendor price levels do not leak in.
  * Periods are chain-linked: NAV_t = NAV_ref * V_t / V_ref.
  * Dividends increase value on the ex-date, counted once. A split stops the run.
  * When the last configured period ends (valid_until), the series is frozen until
    the next period (new trades) is added to data/portfolio_config.json.

Performance measures (AMC 2026 rules)
  * Return: cumulative, from the 16 Oct 2026 USD 100m basket.
  * Volatility: sample st. dev. of daily returns x sqrt(252).
  * Sharpe ratio: (mean daily return - mean daily risk-free) / st. dev. x sqrt(252).
    Risk-free = 1-month US T-bill (FRED DGS1MO, comparable to Bloomberg USGG1M used in the Round 1 code),
    yield known at the previous close / 252 per daily return.
  * Table rows follow the AMC guidelines: each round on its own period
    (16 Oct-6 Nov, 6 Nov-27 Nov, 27 Nov-27 Dec) plus the whole challenge.

Data licence: yfinance is unofficial; Yahoo states the data are intended for personal
use. Check public-site rights before enabling the publication workflow.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import statistics
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / 'data' / 'portfolio_config.json'
OUTPUT_PATH = ROOT / 'data' / 'portfolio_performance.json'

TRADING_DAYS = 252
MIN_RETURNS_FOR_RISK = 5      # below this, volatility and Sharpe are not shown
MAX_GAP_DAYS = 5              # max calendar days between last close and a reference date
FRED_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}&coed={end}'


def money(x):
    return Decimal(str(x))


def d(iso):
    return date.fromisoformat(iso)


def us_today():
    return datetime.now(ZoneInfo('America/New_York')).date()


# --------------------------------------------------------------------------- config

def load_config(path=CONFIG_PATH):
    c = json.loads(Path(path).read_text(encoding='utf8'))
    periods = c['periods']
    if not periods:
        raise ValueError('No holding period configured')
    for i, p in enumerate(periods):
        tickers = [x['ticker'] for x in p['positions']]
        if not tickers or len(set(tickers)) != len(tickers):
            raise ValueError(f'{p["label"]}: empty or duplicate tickers')
        for x in p['positions']:
            if not (isinstance(x['shares'], int) and x['shares'] > 0):
                raise ValueError(f'{p["label"]} {x["ticker"]}: shares must be a positive integer')
            basis = money(x['official_basis_usd'])
            implied = money(x['shares']) * money(x['official_reference_close'])
            if basis <= 0 or abs(implied - basis) > Decimal('1.00'):
                raise ValueError(f'{p["label"]} {x["ticker"]}: basis {basis} != shares x close {implied}')
        if money(p['cash_usd']) < 0:
            raise ValueError(f'{p["label"]}: negative cash')
        if d(p['valid_until']) <= d(p['reference_date']):
            raise ValueError(f'{p["label"]}: valid_until must follow reference_date')
        if i > 0 and p['reference_date'] != periods[i - 1]['valid_until']:
            raise ValueError(f'{p["label"]}: reference_date must equal previous valid_until (contiguous periods)')
    first = periods[0]
    total = sum((money(x['official_basis_usd']) for x in first['positions']), money(first['cash_usd']))
    if abs(total - money(c['initial_capital_usd'])) > Decimal('0.01'):
        raise ValueError(f'Opening official basket does not reconcile: {total}')
    return c


def reference_date(config):
    return config['periods'][0]['reference_date']


def last_valid_until(config):
    return config['periods'][-1]['valid_until']


def holdings_status(config, today):
    return 'frozen_awaiting_next_period' if today > d(last_valid_until(config)) else 'active'


# --------------------------------------------------------------------------- data

def download_yahoo(config, today=None):
    """Return ticker->{date:{close, dividend, split}} with no forward fill."""
    import yfinance as yf
    today = today or us_today()
    begin = reference_date(config)
    # End is exclusive: never today's possibly unfinished bar, never past the holdings.
    end = min(today, d(last_valid_until(config)) + timedelta(days=1)).isoformat()
    tickers = sorted({x['ticker'] for p in config['periods'] for x in p['positions']})
    result = {}
    for ticker in tickers:
        bars = yf.Ticker(ticker).history(
            start=begin, end=end, interval='1d', auto_adjust=False,
            actions=True, repair=False, raise_errors=True,
        )
        if bars is None or bars.empty:
            raise RuntimeError(f'No historical data for {ticker}')
        ticker_data = {}
        for idx, r in bars.iterrows():
            day = idx.date().isoformat()
            close = float(r['Close'])
            if not math.isfinite(close) or close <= 0:
                raise ValueError(f'{ticker} invalid close on {day}: {close}')

            def scalar(key, fallback=0.0):
                try:
                    out = float(r.get(key, fallback))
                    return out if math.isfinite(out) else fallback
                except (ValueError, TypeError):
                    return fallback
            ticker_data[day] = {'close': close, 'dividend': scalar('Dividends'), 'split': scalar('Stock Splits')}
        result[ticker] = ticker_data
    return result


def parse_fred_csv(text):
    """FRED CSV -> sorted [(iso_date, yield_pct)], skipping holidays ('.' or empty)."""
    rows = []
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError('Unexpected FRED CSV header')
    for row in reader:
        if len(row) < 2 or row[1].strip() in ('', '.'):
            continue
        y = float(row[1])
        if not math.isfinite(y) or not (-1 < y < 25):
            raise ValueError(f'Implausible T-bill yield on {row[0]}: {y}')
        rows.append((row[0], y))
    if not rows:
        raise ValueError('No risk-free observations from FRED')
    return sorted(rows)


def download_risk_free(config, end_iso):
    rf = config['risk_free']
    start = (d(reference_date(config)) - timedelta(days=14)).isoformat()
    url = FRED_URL.format(series=rf['series'], start=start, end=end_iso)
    req = urllib.request.Request(url, headers={'User-Agent': 'quontech-nav-tracker/1.0'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return parse_fred_csv(resp.read().decode('utf8'))


def rf_for_interval(rates, prev_iso, cur_iso, max_stale):
    """Risk-free return for one daily return (close prev -> close cur).

    Yield known at the previous close, divided by 252: the daily analogue of the
    Round 1 code (USGG1M yield shifted by one period / 12 for monthly returns), so
    that mean(rf) x 252 equals the average T-bill yield."""
    known = [(day, y) for (day, y) in rates if day <= prev_iso]
    if not known:
        raise ValueError(f'No risk-free yield on or before {prev_iso}')
    if (d(prev_iso) - d(known[-1][0])).days > max_stale:
        raise ValueError(f'Risk-free yield stale at {prev_iso} (last {known[-1][0]})')
    return known[-1][1] / 100 / TRADING_DAYS


# --------------------------------------------------------------------------- NAV

def construct_series(config, market, today=None):
    today = today or us_today()
    first_obs = config['first_public_observation_date']
    nav_ref = money(config['initial_capital_usd'])
    previous_nav = nav_ref
    points = []
    periods = config['periods']

    for i, p in enumerate(periods):
        ref, until = p['reference_date'], p['valid_until']
        tickers = [x['ticker'] for x in p['positions']]
        for t in tickers:
            if t not in market or ref not in market[t]:
                raise ValueError(f'Missing {ref} baseline close for {t} ({p["label"]})')
        for x in p['positions']:
            source_ref = market[x['ticker']][ref]['close']
            expected = float(x['official_reference_close'])
            if abs(source_ref / expected - 1) > .05:
                raise ValueError(f'{x["ticker"]} source baseline ({source_ref}) differs >5% '
                                 f'from supplied Bloomberg reference ({expected})')
        window = sorted({day for t in tickers for day in market[t] if ref < day <= until})
        for day in window:
            missing = [t for t in tickers if day not in market[t]]
            if missing:
                raise ValueError(f'Incomplete date {day}: missing {missing}')

        cash = money(p['cash_usd'])
        v_ref = cash + sum(money(x['official_basis_usd']) for x in p['positions'])
        dividends = Decimal('0')
        for day in window:
            marked = Decimal('0')
            for x in p['positions']:
                bar = market[x['ticker']][day]
                if money(bar.get('split', 0.0)) != 0:
                    raise ValueError(f'Corporate action split for {x["ticker"]} on {day}: '
                                     'requires manual price-series normalization before release')
                div = money(bar.get('dividend', 0.0))
                if div < 0 or div > money(bar['close']) * Decimal('0.30'):
                    raise ValueError(f'Suspicious dividend for {x["ticker"]} on {day}: {div}')
                dividends += money(x['shares']) * div
                marked += money(x['official_basis_usd']) * money(bar['close']) / money(market[x['ticker']][ref]['close'])
            nav = nav_ref * (cash + marked + dividends) / v_ref
            daily_return = float(nav / previous_nav - 1)
            if abs(daily_return) > .10:
                raise ValueError(f'Unexpected >10% daily NAV movement {day}: {daily_return:.2%}')
            if day >= first_obs:
                initial = money(config['initial_capital_usd'])
                points.append({
                    'date': day,
                    'period': p['label'],
                    'nav_usd': round(float(nav), 2),
                    'return_pct': round(float((nav / initial - 1) * 100), 6),
                    'index_100': round(float(nav / initial * 100), 6),
                    'daily_return_pct': round(daily_return * 100, 6),
                })
            previous_nav = nav
        if i + 1 < len(periods):
            if not window or window[-1] != until:
                raise ValueError(f'{p["label"]} must have a close on valid_until {until} before chaining')
            nav_ref = previous_nav

    if not points:
        raise ValueError('No complete daily close on or after the first observation')
    newest = d(points[-1]['date'])
    if newest >= today:
        raise ValueError('Latest bar is current/unfinished US trading session')
    if holdings_status(config, today) == 'frozen_awaiting_next_period':
        if (d(last_valid_until(config)) - newest).days > MAX_GAP_DAYS:
            raise ValueError(f'Series ends {newest}, too far before valid_until {last_valid_until(config)}')
    elif (today - newest).days > MAX_GAP_DAYS:
        raise ValueError(f'Stale last close ({points[-1]["date"]})')
    return points


# --------------------------------------------------------------------------- metrics

def attach_risk_free(config, points, rates):
    prev = reference_date(config)
    max_stale = config['risk_free']['max_staleness_days']
    for pt in points:
        pt['risk_free_pct'] = round(rf_for_interval(rates, prev, pt['date'], max_stale) * 100, 8)
        prev = pt['date']
    return points


def summarize(points, base_nav):
    """Return / volatility / Sharpe on the daily returns in `points`, return measured from base_nav.

    Course convention (Class 2 notes; AMC Round 1 guidelines, section 5):
      SR = (mean R_P - mean R_f) / sd(R_P), arithmetic means, sample st. dev.,
      annualised with n = 252: mean x n, st. dev. x sqrt(n), SR x sqrt(n).
    """
    n = len(points)
    out = {
        'start_close': None,
        'as_of_close': points[-1]['date'],
        'n_returns': n,
        'return_pct': round((points[-1]['nav_usd'] / base_nav - 1) * 100, 4),
        'volatility_pct': None,
        'sharpe': None,
        'risk_free_ann_pct': None,
    }
    if n < MIN_RETURNS_FOR_RISK:
        return out
    r = [pt['daily_return_pct'] / 100 for pt in points]
    rf = [pt['risk_free_pct'] / 100 for pt in points]
    sd = statistics.stdev(r)
    out['volatility_pct'] = round(sd * math.sqrt(TRADING_DAYS) * 100, 4)
    out['risk_free_ann_pct'] = round(statistics.fmean(rf) * TRADING_DAYS * 100, 4)
    if sd > 0:
        excess = statistics.fmean(r) - statistics.fmean(rf)
        out['sharpe'] = round(excess / sd * math.sqrt(TRADING_DAYS), 4)
    return out


def window_stats(config, points, start, end):
    """Stats for returns from the close of `start` to the close of `end` (inclusive).

    Returns None when the series does not cover the window."""
    ref = reference_date(config)
    if start == ref:
        base = float(config['initial_capital_usd'])
        start_close = ref
    else:
        before = [pt for pt in points if pt['date'] <= start]
        if not before or (d(start) - d(before[-1]['date'])).days > MAX_GAP_DAYS:
            return None
        base, start_close = before[-1]['nav_usd'], before[-1]['date']
    inside = [pt for pt in points if start_close < pt['date'] <= end]
    if not inside:
        return None
    out = summarize(inside, base)
    out['start_close'] = start_close
    return out


def build_metrics(config, points, today):
    milestones = []
    for m in config['milestones']:
        row = {'key': m['key'], 'label': m['label'], 'start': m['start'], 'as_of': m['as_of'], 'status': 'pending'}
        stats = window_stats(config, points, m['start'], m['as_of']) if today > d(m['as_of']) else None
        if stats and (d(m['as_of']) - d(stats['as_of_close'])).days <= MAX_GAP_DAYS:
            row['status'] = 'final'
            row.update(stats)
        milestones.append(row)
    to_date = window_stats(config, points, reference_date(config), points[-1]['date'])
    return {'to_date': to_date, 'milestones': milestones}


# --------------------------------------------------------------------------- output

def validate_history(new_points, output_path=OUTPUT_PATH):
    """Fail on material retroactive changes to published history (>10 bps NAV)."""
    file = Path(output_path)
    if not file.exists():
        return
    old = json.loads(file.read_text(encoding='utf8'))
    if old.get('status') != 'validated_eod':
        return
    old_map = {row['date']: row['nav_usd'] for row in old.get('series', [])}
    new_map = {row['date']: row['nav_usd'] for row in new_points}
    for day, old_nav in old_map.items():
        if day not in new_map:
            raise ValueError(f'Published date disappeared: {day}')
        if abs(new_map[day] / old_nav - 1) > .001:
            raise ValueError(f'Historical NAV on {day} revised >10 bps: {old_nav} vs {new_map[day]}; manual review needed')


def build_output(c, points, metrics, today):
    return {
        'status': 'validated_eod',
        'holdings_status': holdings_status(c, today),
        'portfolio_name': c['portfolio_name'],
        'currency': 'USD',
        'reference_date': reference_date(c),
        'first_observation': c['first_public_observation_date'],
        'holdings_valid_until': last_valid_until(c),
        'as_of': points[-1]['date'],
        'last_updated_utc': datetime.now(ZoneInfo('UTC')).isoformat(timespec='seconds'),
        'source': 'Yahoo Finance prices via unofficial yfinance; Bloomberg initial basket; FRED DGS1MO risk-free',
        'method': ('Bloomberg reference-close basket values + vendor closing price relatives, chain-linked across '
                   'holding periods; cash + declared dividends on ex-dates; not exact PRTU NAV. Splits force manual review.'),
        'metrics_method': ('Return compounded over the window. Volatility = sample st. dev. of daily returns x sqrt(252). '
                           'Sharpe = (mean daily return - mean daily 1-month T-bill rate [yield/252]) / st. dev. of daily returns x sqrt(252). '
                           f'Volatility and Sharpe shown from {MIN_RETURNS_FOR_RISK} daily returns.'),
        'risk_free_source': c['risk_free']['description'],
        'initial_capital_usd': c['initial_capital_usd'],
        'latest_nav_usd': points[-1]['nav_usd'],
        'latest_return_pct': points[-1]['return_pct'],
        'metrics': metrics,
        'benchmark_notice': 'Round 1 portfolio IS the SAA, not a portfolio with independent SAA excess returns.',
        'series': points,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='Only print computed summary')
    args = ap.parse_args()
    today = us_today()
    c = load_config()
    prices = download_yahoo(c, today)
    points = construct_series(c, prices, today)
    rates = download_risk_free(c, points[-1]['date'])
    attach_risk_free(c, points, rates)
    validate_history(points)
    payload = build_output(c, points, build_metrics(c, points, today), today)
    if args.dry_run:
        print(json.dumps({k: payload[k] for k in ('as_of', 'holdings_status', 'latest_nav_usd', 'metrics')}, indent=2))
        return
    if OUTPUT_PATH.exists():
        old = json.loads(OUTPUT_PATH.read_text(encoding='utf8'))
        strip = lambda x: {k: v for k, v in x.items() if k != 'last_updated_utc'}
        if strip(old) == strip(payload):
            print(f'No change since last publication (last close {points[-1]["date"]}).')
            return
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf8')
    t = payload['metrics']['to_date']
    print(f'Updated {len(points)} validated EOD observations, last {points[-1]["date"]}: '
          f'${points[-1]["nav_usd"]:,.2f} | return {t["return_pct"]}% | vol {t["volatility_pct"]} | Sharpe {t["sharpe"]}')


if __name__ == '__main__':
    main()
