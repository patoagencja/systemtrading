"""Generate static HTML dashboard → docs/index.html.

Premium dark-themed trading terminal. Two modes: Live paper trading &
12-month Backtest. Password-protected via crypto.subtle SHA-256.
"""
import os
import sys
from datetime import datetime, date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))

import plotly.graph_objects as go

from app.database import db_cursor
from app.config import INITIAL_CAPITAL_PLN
from app.metrics import compute_metrics

DOCS_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "docs"))
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "trading2025")

# ── helpers ─────────────────────────────────────────────────────────────────

def _fmt(x, dec=0):
    if x is None:
        return "—"
    return f"{x:,.{dec}f}".replace(",", " ")  # thin space


def _sgn(v, unit="PLN", dec=0):
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{_fmt(v, dec)} {unit}"


def _pct(v, dec=1):
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{dec}f}%"


def _cls(v):
    if v is None or v == 0:
        return ""
    return "pos" if v > 0 else "neg"


def _safe(v, fmt=".2f"):
    if v is None:
        return "—"
    if isinstance(v, float) and (v != v):  # NaN
        return "—"
    return f"{v:{fmt}}"


# ── data gathering ───────────────────────────────────────────────────────────

def gather(mode: str) -> dict:
    d: dict = {"mode": mode}
    with db_cursor() as cur:
        cur.execute(
            "SELECT snapshot_date, total_value_pln, cash_pln, invested_pln "
            "FROM portfolio_snapshots WHERE run_mode=? ORDER BY snapshot_date",
            (mode,),
        )
        d["snapshots"] = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT * FROM trades WHERE status='open' AND run_mode=? ORDER BY entry_date DESC",
            (mode,),
        )
        d["open_trades"] = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT * FROM trades WHERE status='closed' AND run_mode=? ORDER BY exit_date DESC",
            (mode,),
        )
        d["closed_trades"] = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT * FROM trades WHERE run_mode=? ORDER BY entry_date DESC LIMIT 100",
            (mode,),
        )
        d["all_trades"] = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """SELECT strategy,
                      COUNT(*) n,
                      SUM(CASE WHEN pnl_pln>0 THEN 1 ELSE 0 END) wins,
                      SUM(pnl_pln) total_pnl,
                      AVG(pnl_pln) avg_pnl,
                      100.0*SUM(CASE WHEN pnl_pln>0 THEN 1 ELSE 0 END)/COUNT(*) win_rate
               FROM trades
               WHERE status='closed' AND run_mode=?
               GROUP BY strategy ORDER BY total_pnl DESC""",
            (mode,),
        )
        d["by_strategy"] = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """SELECT ticker, strategy, signal_date, score,
                      entry_price, stop_loss, take_profit, rsi, volume_ratio, reason
               FROM signals WHERE acted=0 AND run_mode=?
               ORDER BY score DESC LIMIT 20""",
            (mode,),
        )
        d["pending_signals"] = [dict(r) for r in cur.fetchall()]

    d["open_count"] = len(d["open_trades"])
    return d


# ── benchmark (SPY buy-and-hold aligned to snapshot dates) ──────────────────

def fetch_spy_benchmark(snap_dates: list[str], initial: float) -> list[dict]:
    """Return [{date, value}] aligned to snap_dates. Returns [] on failure."""
    if not snap_dates:
        return []
    try:
        from app.data_provider import fetch_ohlcv
        spy = fetch_ohlcv("SPY", period="2y")
        if spy.empty:
            return []
        spy_dates_set = {str(d.date()) for d in spy.index}
        first_price = None
        result = []
        for sd in snap_dates:
            if sd in spy_dates_set:
                row = spy[spy.index.date == date.fromisoformat(sd)]
                if not row.empty:
                    price = float(row.iloc[0]["close"])
                    if first_price is None:
                        first_price = price
                    result.append({"date": sd, "value": initial * price / first_price})
        return result
    except Exception as e:
        print(f"  [warn] benchmark fetch failed: {e}")
        return []


# ── Plotly dark chart theme ──────────────────────────────────────────────────

_DARK_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#8b949e", size=11, family="system-ui,sans-serif"),
    xaxis=dict(gridcolor="#21262d", linecolor="#30363d", zerolinecolor="#30363d"),
    yaxis=dict(gridcolor="#21262d", linecolor="#30363d", zerolinecolor="#30363d"),
    margin=dict(l=50, r=16, t=36, b=28),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
    hoverlabel=dict(bgcolor="#161b22", bordercolor="#30363d", font_color="#e6edf3"),
)


def _chart_html(fig) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": False})


def render_equity_chart(dates, values, benchmark=None, height=300) -> str:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=values, mode="lines", name="Portfolio",
        line=dict(color="#388bfd", width=2),
        hovertemplate="%{x}<br>%{y:,.0f} PLN<extra></extra>",
    ))
    if benchmark:
        b_dates = [b["date"] for b in benchmark]
        b_vals = [b["value"] for b in benchmark]
        fig.add_trace(go.Scatter(
            x=b_dates, y=b_vals, mode="lines", name="SPY B&H",
            line=dict(color="#2ea043", width=1.5, dash="dot"),
            hovertemplate="%{x}<br>%{y:,.0f} PLN<extra></extra>",
        ))
    fig.add_hline(y=INITIAL_CAPITAL_PLN, line_dash="dash", line_color="#30363d", line_width=1)
    layout = dict(_DARK_LAYOUT)
    layout.update(dict(title=dict(text="Krzywa kapitału", font=dict(size=13, color="#8b949e")),
                       height=height, yaxis_title="PLN"))
    fig.update_layout(**layout)
    return _chart_html(fig)


def render_drawdown_chart(dates, dd_series, height=180) -> str:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=dd_series, mode="lines", fill="tozeroy", name="Drawdown",
        line=dict(color="#f85149", width=1),
        fillcolor="rgba(248,81,73,0.12)",
        hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>",
    ))
    layout = dict(_DARK_LAYOUT)
    layout.update(dict(title=dict(text="Drawdown (%)", font=dict(size=13, color="#8b949e")),
                       height=height, yaxis_title="%", showlegend=False))
    fig.update_layout(**layout)
    return _chart_html(fig)


def render_pnl_bar_chart(by_strategy: list[dict], height=220) -> str:
    if not by_strategy:
        return ""
    names = [r["strategy"].replace("_", " ") for r in by_strategy]
    pnls = [r["total_pnl"] or 0 for r in by_strategy]
    colors = ["#2ea043" if p >= 0 else "#f85149" for p in pnls]
    fig = go.Figure(go.Bar(
        x=pnls, y=names, orientation="h",
        marker_color=colors,
        hovertemplate="%{y}<br>%{x:,.0f} PLN<extra></extra>",
    ))
    layout = dict(_DARK_LAYOUT)
    layout.update(dict(title=dict(text="P&L wg strategii", font=dict(size=13, color="#8b949e")),
                       height=height, xaxis_title="PLN", showlegend=False))
    fig.update_layout(**layout)
    return _chart_html(fig)


# ── monthly heatmap ──────────────────────────────────────────────────────────

def _heat_color(pct: float) -> str:
    abs_v = abs(pct)
    if abs_v < 0.5:
        alpha = 0.08
    elif abs_v < 1.5:
        alpha = 0.18
    elif abs_v < 3.0:
        alpha = 0.32
    elif abs_v < 5.0:
        alpha = 0.52
    else:
        alpha = 0.75
    if pct >= 0:
        return f"rgba(46,160,67,{alpha})"
    return f"rgba(248,81,73,{alpha})"


def render_monthly_heatmap(monthly_returns: dict) -> str:
    if not monthly_returns:
        return "<p class='muted' style='padding:16px 0'>Brak danych</p>"

    months_pl = ["Sty", "Lut", "Mar", "Kwi", "Maj", "Cze",
                 "Lip", "Sie", "Wrz", "Paź", "Lis", "Gru"]

    years = sorted({k[:4] for k in monthly_returns})
    if not years:
        return ""

    rows = []
    for year in years:
        year_vals = []
        year_total = 1.0
        for m in range(1, 13):
            key = f"{year}-{m:02d}"
            v = monthly_returns.get(key)
            year_vals.append(v)
            if v is not None:
                year_total *= (1 + v / 100)
        year_pct = (year_total - 1) * 100

        cells = ""
        for v in year_vals:
            if v is None:
                cells += "<td class='hm-empty'>—</td>"
            else:
                sign = "+" if v >= 0 else ""
                color = _heat_color(v)
                cls = "pos" if v >= 0 else "neg"
                cells += (f"<td style='background:{color}'>"
                          f"<span class='{cls}'>{sign}{v:.1f}%</span></td>")

        yc = "pos" if year_pct >= 0 else "neg"
        sign = "+" if year_pct >= 0 else ""
        cells += f"<td class='hm-total'><span class='{yc}'>{sign}{year_pct:.1f}%</span></td>"
        rows.append(f"<tr><td class='hm-year'>{year}</td>{cells}</tr>")

    header = "".join(f"<th>{m}</th>" for m in months_pl) + "<th>Rok</th>"
    return f"""<div class="table-scroll">
<table class="heatmap">
<thead><tr><th></th>{header}</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>"""


# ── panel renderers ──────────────────────────────────────────────────────────

def render_overview_panel(d: dict, m: dict, benchmark=None) -> str:
    snaps = d["snapshots"]
    period = f"{snaps[0]['snapshot_date']} → {snaps[-1]['snapshot_date']}" if snaps else "—"
    latest = snaps[-1] if snaps else {}
    portfolio_val = latest.get("total_value_pln", INITIAL_CAPITAL_PLN)
    cash = latest.get("cash_pln", INITIAL_CAPITAL_PLN)
    invested = latest.get("invested_pln", 0)
    ret_pct = m["total_return_pct"]
    pnl = portfolio_val - INITIAL_CAPITAL_PLN

    equity_chart = render_equity_chart(m["dates"], m["values"], benchmark) if m["values"] else ""
    dd_chart = render_drawdown_chart(m["dates"], m["dd_series"]) if m["dd_series"] else ""

    pf_str = f"{m['profit_factor']:.2f}" if m["profit_factor"] < 900 else "∞"
    calmar_str = f"{m['calmar']:.2f}" if m["calmar"] < 900 else "∞"

    mode = d["mode"]
    note_benchmark = ""
    if mode == "backtest" and benchmark:
        spy_final = benchmark[-1]["value"] if benchmark else INITIAL_CAPITAL_PLN
        spy_ret = (spy_final / INITIAL_CAPITAL_PLN - 1) * 100
        alpha = ret_pct - spy_ret
        sign_spy = "+" if spy_ret >= 0 else ""
        sign_alpha = "+" if alpha >= 0 else ""
        alpha_cls = "pos" if alpha >= 0 else "neg"
        note_benchmark = f"""
        <div class="bench-row">
          <div class="bench-item">
            <span class="bench-label">SPY B&H</span>
            <span class="bench-val {'pos' if spy_ret>=0 else 'neg'}">{sign_spy}{spy_ret:.1f}%</span>
          </div>
          <div class="bench-item">
            <span class="bench-label">Alpha vs SPY</span>
            <span class="bench-val {alpha_cls}">{sign_alpha}{alpha:.1f}%</span>
          </div>
          <div class="bench-item">
            <span class="bench-label">Sharpe</span>
            <span class="bench-val">{m['sharpe']:.2f}</span>
          </div>
          <div class="bench-item">
            <span class="bench-label">Sortino</span>
            <span class="bench-val">{m['sortino']:.2f}</span>
          </div>
          <div class="bench-item">
            <span class="bench-label">Calmar</span>
            <span class="bench-val">{calmar_str}</span>
          </div>
        </div>"""

    no_data = ""
    if not snaps:
        no_data = """<div class="empty-state">
          <div class="empty-icon">🤖</div>
          <div class="empty-title">Brak danych</div>
          <div class="empty-sub">Robot uruchamia się o 23:00 UTC w dni robocze.</div>
        </div>"""

    return f"""
<div class="section-header">
  <span class="period-tag">{period}</span>
</div>

{no_data}

<div class="kpi-grid">
  <div class="kpi">
    <div class="kpi-label">Wartość portfela</div>
    <div class="kpi-value">{_fmt(portfolio_val)} <span class="kpi-unit">PLN</span></div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Zwrot łączny</div>
    <div class="kpi-value {_cls(ret_pct)}">{_pct(ret_pct)}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">P&amp;L</div>
    <div class="kpi-value {_cls(pnl)}">{_sgn(pnl)}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Gotówka</div>
    <div class="kpi-value">{_fmt(cash)} <span class="kpi-unit">PLN</span></div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Otwarte</div>
    <div class="kpi-value">{d['open_count']}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Win rate</div>
    <div class="kpi-value">{m['win_rate']:.1f}%</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Profit factor</div>
    <div class="kpi-value">{pf_str}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Max drawdown</div>
    <div class="kpi-value neg">{m['max_drawdown_pct']:.1f}%</div>
  </div>
</div>

{note_benchmark}

<div class="chart-card">{equity_chart if equity_chart else '<p class="muted p16">Brak danych do wykresu.</p>'}</div>
<div class="chart-card" style="margin-top:10px">{dd_chart if dd_chart else ''}</div>

<div class="metrics-grid">
  <div class="metric-card">
    <div class="metric-label">Sharpe Ratio</div>
    <div class="metric-value">{m['sharpe']:.2f}</div>
    <div class="metric-sub">Annualised, RF=4%</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Sortino Ratio</div>
    <div class="metric-value">{m['sortino']:.2f}</div>
    <div class="metric-sub">Downside deviation</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Calmar Ratio</div>
    <div class="metric-value">{calmar_str}</div>
    <div class="metric-sub">Ann. return / Max DD</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Expectancy</div>
    <div class="metric-value {_cls(m['expectancy_pln'])}">{_sgn(m['expectancy_pln'], dec=0)}</div>
    <div class="metric-sub">Per trade</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Avg R-multiple</div>
    <div class="metric-value {_cls(m['avg_r_multiple'])}">{m['avg_r_multiple']:+.2f}R</div>
    <div class="metric-sub">Reward / risk units</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Avg holding</div>
    <div class="metric-value">{m['avg_holding_days']:.1f}</div>
    <div class="metric-sub">Days per trade</div>
  </div>
</div>"""


def render_positions_panel(d: dict) -> str:
    trades = d["open_trades"]
    if not trades:
        return """<div class="empty-state">
          <div class="empty-icon">📭</div>
          <div class="empty-title">Brak otwartych pozycji</div>
          <div class="empty-sub">Nowe pozycje pojawią się po spełnieniu warunków strategii.</div>
        </div>"""

    rows = []
    for t in trades:
        pnl = t.get("pnl_pln") or 0
        pnl_pct = t.get("pnl_pct") or 0
        hold = t.get("holding_days") or 0
        tid = t.get("id", "")
        rows.append(
            f"<tr class='tr-click' onclick='showTrade({tid})'>"
            f"<td><b>{t['ticker']}</b></td>"
            f"<td class='mono'>{t['strategy'].replace('_', ' ')}</td>"
            f"<td class='mono'>{t['entry_date']}</td>"
            f"<td class='mono'>{t['entry_price']:.2f}</td>"
            f"<td class='mono'>{t['stop_loss']:.2f}</td>"
            f"<td class='mono'>{t['take_profit']:.2f}</td>"
            f"<td class='mono'>{t['shares']:.1f}</td>"
            f"<td class='mono'>{hold}d</td>"
            f"<td class='mono {_cls(pnl)}'>{_sgn(pnl)}</td>"
            f"<td class='mono {_cls(pnl_pct)}'>{_pct(pnl_pct)}</td>"
            f"</tr>"
        )

    return f"""<p class="table-hint">Kliknij wiersz, żeby zobaczyć szczegóły transakcji</p>
<div class="table-scroll">
<table>
<thead><tr>
  <th>Ticker</th><th>Strategia</th><th>Wejście</th><th>Cena wej.</th>
  <th>Stop Loss</th><th>Take Profit</th><th>Akcje</th><th>Czas</th>
  <th>P&amp;L PLN</th><th>P&amp;L %</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>"""


def render_trades_panel(d: dict, limit: int = 100) -> str:
    trades = d["all_trades"][:limit]
    if not trades:
        return "<p class='muted p16'>Brak transakcji.</p>"

    rows = []
    for t in trades:
        st = t["status"]
        badge_cls = "badge-open" if st == "open" else "badge-closed"
        badge = f"<span class='badge {badge_cls}'>{st.upper()}</span>"
        pnl = t.get("pnl_pln")
        pnl_pct = t.get("pnl_pct")
        r = t.get("r_multiple")
        exit_date = t.get("exit_date") or "—"
        exit_reason = t.get("exit_reason") or "—"
        tid = t.get("id", "")
        rows.append(
            f"<tr class='tr-click' onclick='showTrade({tid})'>"
            f"<td>{badge}</td>"
            f"<td><b>{t['ticker']}</b></td>"
            f"<td class='mono'>{t['strategy'].replace('_', ' ')}</td>"
            f"<td class='mono'>{t['entry_date']}</td>"
            f"<td class='mono'>{exit_date}</td>"
            f"<td class='mono {_cls(pnl) if pnl is not None else ''}'>"
            f"{'live' if pnl is None else _sgn(pnl)}</td>"
            f"<td class='mono {_cls(pnl_pct) if pnl_pct is not None else ''}'>"
            f"{'—' if pnl_pct is None else _pct(pnl_pct)}</td>"
            f"<td class='mono'>{f'{r:+.2f}R' if r is not None else '—'}</td>"
            f"<td class='mono small'>{exit_reason}</td>"
            f"</tr>"
        )

    return f"""<p class="table-hint">Kliknij wiersz, żeby zobaczyć szczegóły</p>
<div class="table-scroll">
<table>
<thead><tr>
  <th>Status</th><th>Ticker</th><th>Strategia</th>
  <th>Wejście</th><th>Wyjście</th>
  <th>P&amp;L PLN</th><th>P&amp;L %</th><th>R</th><th>Powód wyjścia</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>"""


def render_strategies_panel(d: dict, m: dict) -> str:
    strats = d["by_strategy"]
    pnl_chart = render_pnl_bar_chart(strats)

    if not strats:
        return "<p class='muted p16'>Brak danych o strategiach.</p>"

    rows = []
    for s in strats:
        pnl = s.get("total_pnl") or 0
        wins = int(s.get("wins") or 0)
        n = int(s.get("n") or 0)
        wr = s.get("win_rate") or 0
        avg_pnl = s.get("avg_pnl") or 0
        rows.append(
            f"<tr>"
            f"<td><b>{s['strategy'].replace('_', ' ')}</b></td>"
            f"<td class='mono'>{n}</td>"
            f"<td class='mono'>{wins} / {n - wins}</td>"
            f"<td class='mono'>{wr:.1f}%</td>"
            f"<td class='mono {_cls(pnl)}'>{_sgn(pnl)}</td>"
            f"<td class='mono {_cls(avg_pnl)}'>{_sgn(avg_pnl)}</td>"
            f"</tr>"
        )

    avg_win_str = _sgn(m["avg_win_pln"])
    avg_loss_str = _sgn(m["avg_loss_pln"])
    streak_win = m["max_win_streak"]
    streak_loss = m["max_loss_streak"]

    return f"""
<div class="grid2">
  <div>
    <h3 class="section-title">Wyniki wg strategii</h3>
    <div class="table-scroll">
    <table>
    <thead><tr>
      <th>Strategia</th><th>Transakcje</th><th>W/L</th>
      <th>Win rate</th><th>Total P&amp;L</th><th>Avg P&amp;L</th>
    </tr></thead>
    <tbody>{"".join(rows)}</tbody>
    </table></div>
  </div>
  <div>
    <h3 class="section-title">Statystyki globalne</h3>
    <div class="stat-list">
      <div class="stat-row"><span>Avg zysk</span><span class="pos mono">{avg_win_str}</span></div>
      <div class="stat-row"><span>Avg strata</span><span class="neg mono">{avg_loss_str}</span></div>
      <div class="stat-row"><span>Max seria wygranych</span><span class="pos mono">{streak_win}</span></div>
      <div class="stat-row"><span>Max seria strat</span><span class="neg mono">{streak_loss}</span></div>
      <div class="stat-row"><span>Gross profit</span><span class="pos mono">{_sgn(m['gross_profit'])}</span></div>
      <div class="stat-row"><span>Gross loss</span><span class="neg mono">{_sgn(m['gross_loss'])}</span></div>
    </div>
  </div>
</div>
<div class="chart-card" style="margin-top:16px">{pnl_chart}</div>"""


def render_signals_panel(d: dict) -> str:
    signals = d["pending_signals"]
    if not signals:
        return """<div class="empty-state">
          <div class="empty-icon">📡</div>
          <div class="empty-title">Brak oczekujących sygnałów</div>
          <div class="empty-sub">Sygnały pojawiają się po wieczornym skanie (23:00 UTC).</div>
        </div>"""

    rows = []
    for s in signals:
        rows.append(
            f"<tr>"
            f"<td><b>{s['ticker']}</b></td>"
            f"<td class='mono'>{s['strategy'].replace('_', ' ')}</td>"
            f"<td class='mono'>{s['signal_date']}</td>"
            f"<td class='mono'>{s['score']:.0f}</td>"
            f"<td class='mono'>{s['entry_price']:.2f}</td>"
            f"<td class='mono'>{s['stop_loss']:.2f}</td>"
            f"<td class='mono'>{s['take_profit']:.2f}</td>"
            f"<td class='mono'>{s.get('rsi', 0):.1f}</td>"
            f"<td class='mono small'>{(s.get('reason') or '')[:60]}</td>"
            f"</tr>"
        )

    return f"""<div class="table-scroll">
<table>
<thead><tr>
  <th>Ticker</th><th>Strategia</th><th>Data</th><th>Score</th>
  <th>Entry</th><th>Stop</th><th>Target</th><th>RSI</th><th>Powód</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>"""


def render_yearly_breakdown(snapshots: list, closed_trades: list) -> str:
    """Table with per-year return, trades, win-rate, Sharpe."""
    if not snapshots:
        return "<p class='muted'>Brak danych rocznych</p>"

    import math

    # group snapshot values by year
    by_year: dict[str, list] = {}
    for s in snapshots:
        y = s["snapshot_date"][:4]
        by_year.setdefault(y, []).append(s)

    # group closed trades by exit year
    trades_by_year: dict[str, list] = {}
    for t in closed_trades:
        ed = t.get("exit_date") or ""
        y = ed[:4]
        if y:
            trades_by_year.setdefault(y, []).append(t)

    rows = ""
    prev_value = INITIAL_CAPITAL_PLN
    for year in sorted(by_year.keys()):
        snaps_y = by_year[year]
        start_val = prev_value
        end_val = snaps_y[-1]["total_value_pln"]
        ret = (end_val - start_val) / start_val * 100 if start_val else 0
        prev_value = end_val

        # daily returns for Sharpe
        daily_vals = [s["total_value_pln"] for s in snaps_y]
        if start_val:
            daily_vals = [start_val] + daily_vals
        daily_rets = [(daily_vals[i] - daily_vals[i-1]) / daily_vals[i-1]
                      for i in range(1, len(daily_vals)) if daily_vals[i-1] > 0]
        sharpe = 0.0
        if len(daily_rets) > 1:
            mean_r = sum(daily_rets) / len(daily_rets)
            var = sum((r - mean_r) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
            std_r = math.sqrt(var) if var > 0 else 0
            rf_daily = 1.04 ** (1 / 252) - 1
            if std_r > 0:
                sharpe = (mean_r - rf_daily) / std_r * math.sqrt(252)

        # trades stats
        ty = trades_by_year.get(year, [])
        wins = sum(1 for t in ty if (t.get("pnl_pln") or 0) > 0)
        wr = wins / len(ty) * 100 if ty else 0
        pnl_sum = sum(t.get("pnl_pln") or 0 for t in ty)

        ret_cls = "pos" if ret >= 0 else "neg"
        pnl_cls = "pos" if pnl_sum >= 0 else "neg"
        rows += (
            f"<tr>"
            f"<td><b>{year}</b></td>"
            f"<td class='mono {ret_cls}'>{_pct(ret)}</td>"
            f"<td class='mono {pnl_cls}'>{_sgn(pnl_sum)}</td>"
            f"<td class='mono'>{len(ty)}</td>"
            f"<td class='mono'>{'—' if not ty else f'{wr:.0f}%'}</td>"
            f"<td class='mono'>{sharpe:.2f}</td>"
            f"</tr>"
        )

    if not rows:
        return "<p class='muted'>Brak danych rocznych</p>"

    return f"""
<div class="table-scroll">
<table>
<thead><tr>
  <th>Rok</th><th>Zwrot</th><th>P&amp;L</th>
  <th>Transakcji</th><th>Win%</th><th>Sharpe</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>
</div>"""


def render_analysis_panel(d: dict, m: dict) -> str:
    heatmap = render_monthly_heatmap(m["monthly_returns"])
    exit_reasons = m["exit_reasons"]

    reasons_rows = "".join(
        f"<div class='stat-row'><span>{r}</span><span class='mono'>{n}</span></div>"
        for r, n in sorted(exit_reasons.items(), key=lambda x: -x[1])
    ) if exit_reasons else "<p class='muted'>Brak danych</p>"

    closed = d["closed_trades"]
    best5 = sorted(closed, key=lambda t: t.get("pnl_pln") or 0, reverse=True)[:5]
    worst5 = sorted(closed, key=lambda t: t.get("pnl_pln") or 0)[:5]

    def trade_mini_row(t):
        pnl = t.get("pnl_pln") or 0
        pnl_pct = t.get("pnl_pct") or 0
        return (f"<tr><td><b>{t['ticker']}</b></td>"
                f"<td class='mono'>{t.get('exit_date','—')}</td>"
                f"<td class='mono {_cls(pnl)}'>{_sgn(pnl)}</td>"
                f"<td class='mono {_cls(pnl_pct)}'>{_pct(pnl_pct)}</td></tr>")

    best_rows = "".join(trade_mini_row(t) for t in best5) or "<tr><td colspan=4>—</td></tr>"
    worst_rows = "".join(trade_mini_row(t) for t in worst5) or "<tr><td colspan=4>—</td></tr>"

    yearly_table = render_yearly_breakdown(d["snapshots"], d["closed_trades"])

    return f"""
<div class="section-block">
  <h3 class="section-title">Zestawienie roczne</h3>
  {yearly_table}
</div>

<div class="section-block" style="margin-top:20px">
  <h3 class="section-title">Miesięczny P&amp;L</h3>
  {heatmap}
</div>

<div class="grid2" style="margin-top:20px">
  <div>
    <h3 class="section-title">Powody zamknięcia</h3>
    <div class="stat-list">{reasons_rows}</div>
  </div>
  <div>
    <h3 class="section-title">Łączne statystyki</h3>
    <div class="stat-list">
      <div class="stat-row"><span>Zwrot łączny</span>
        <span class="mono {_cls(m['total_return_pct'])}">{_pct(m['total_return_pct'])}</span></div>
      <div class="stat-row"><span>Zwrot ann.</span>
        <span class="mono {_cls(m['ann_return_pct'])}">{_pct(m['ann_return_pct'])}</span></div>
      <div class="stat-row"><span>Max drawdown</span>
        <span class="neg mono">{m['max_drawdown_pct']:.1f}%</span></div>
      <div class="stat-row"><span>Transakcji</span>
        <span class="mono">{m['total_trades']}</span></div>
      <div class="stat-row"><span>Avg hold</span>
        <span class="mono">{m['avg_holding_days']:.1f}d</span></div>
    </div>
  </div>
</div>

<div class="grid2" style="margin-top:20px">
  <div>
    <h3 class="section-title">🏆 Top 5 zyski</h3>
    <div class="table-scroll"><table>
    <thead><tr><th>Ticker</th><th>Data</th><th>P&amp;L</th><th>%</th></tr></thead>
    <tbody>{best_rows}</tbody></table></div>
  </div>
  <div>
    <h3 class="section-title">📉 Top 5 straty</h3>
    <div class="table-scroll"><table>
    <thead><tr><th>Ticker</th><th>Data</th><th>P&amp;L</th><th>%</th></tr></thead>
    <tbody>{worst_rows}</tbody></table></div>
  </div>
</div>"""


def build_trades_js(live: dict, bt: dict) -> str:
    """Serialise all trades to a JS constant for the modal."""
    import json

    def safe(v):
        if v is None:
            return ""
        if isinstance(v, float) and v != v:
            return 0
        return v

    trades: dict = {}
    for t in live["open_trades"] + live["closed_trades"] + bt["open_trades"] + bt["closed_trades"]:
        tid = t.get("id")
        if tid is None:
            continue
        trades[str(tid)] = {
            "ticker": safe(t.get("ticker")),
            "strategy": safe(t.get("strategy")),
            "status": safe(t.get("status")),
            "run_mode": safe(t.get("run_mode")),
            "entry_date": safe(t.get("entry_date")),
            "entry_price": safe(t.get("entry_price")),
            "stop_loss": safe(t.get("stop_loss")),
            "take_profit": safe(t.get("take_profit")),
            "exit_date": safe(t.get("exit_date")),
            "exit_price": safe(t.get("exit_price")),
            "exit_reason": safe(t.get("exit_reason")),
            "shares": safe(t.get("shares")),
            "position_value_pln": safe(t.get("position_value_pln")),
            "risk_pln": safe(t.get("risk_pln")),
            "pnl_pln": safe(t.get("pnl_pln")),
            "pnl_pct": safe(t.get("pnl_pct")),
            "r_multiple": safe(t.get("r_multiple")),
            "holding_days": safe(t.get("holding_days")),
            "score": safe(t.get("score")),
            "entry_reason": safe(t.get("entry_reason")),
            "max_holding_days": safe(t.get("max_holding_days")),
        }
    return f"var TRADES={json.dumps(trades, ensure_ascii=False)};"


# ── full HTML assembly ────────────────────────────────────────────────────────

def build_html(live: dict, live_m: dict, bt: dict, bt_m: dict,
               benchmark=None, updated: str = "") -> str:
    import hashlib
    pw_hash = hashlib.sha256(DASHBOARD_PASSWORD.encode()).hexdigest()
    if not updated:
        updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    live_has_data = bool(live["snapshots"])
    bt_has_data = bool(bt["snapshots"])

    # pre-render all panels
    live_overview = render_overview_panel(live, live_m)
    live_positions = render_positions_panel(live)
    live_trades = render_trades_panel(live)
    live_strategies = render_strategies_panel(live, live_m)
    live_signals = render_signals_panel(live)
    live_analysis = render_analysis_panel(live, live_m)

    bt_overview = render_overview_panel(bt, bt_m, benchmark=benchmark)
    bt_positions = render_positions_panel(bt)
    bt_trades = render_trades_panel(bt)
    bt_strategies = render_strategies_panel(bt, bt_m)
    bt_analysis = render_analysis_panel(bt, bt_m)

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Edge Paper Trader</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
:root{{
  --bg:#0d1117;
  --surface:#161b22;
  --surface2:#1c2128;
  --border:#21262d;
  --border2:#30363d;
  --text:#e6edf3;
  --muted:#8b949e;
  --accent:#388bfd;
  --green:#2ea043;
  --red:#f85149;
  --yellow:#d29922;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
html{{height:100%}}
body{{
  font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  background:var(--bg);color:var(--text);
  font-size:13px;line-height:1.5;height:100%;
}}
/* lock */
#lock{{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:9999}}
.lock-box{{background:var(--surface);border:1px solid var(--border2);border-radius:12px;padding:40px 36px;text-align:center;max-width:340px;width:90%}}
.lock-logo{{font-size:32px;margin-bottom:16px}}
.lock-title{{color:var(--text);font-size:20px;font-weight:700;margin-bottom:6px}}
.lock-sub{{color:var(--muted);font-size:13px;margin-bottom:24px}}
#pw-in{{width:100%;padding:10px 14px;border-radius:8px;border:1px solid var(--border2);background:var(--bg);color:var(--text);font-size:14px;outline:none}}
#pw-in:focus{{border-color:var(--accent)}}
.lock-btn{{margin-top:10px;width:100%;padding:10px;border:none;border-radius:8px;background:var(--accent);color:#fff;font-size:14px;font-weight:600;cursor:pointer}}
.lock-btn:hover{{background:#1f6feb}}
#pw-err{{display:none;margin-top:10px;color:var(--red);font-size:12px}}
/* app */
#app{{display:none;height:100%;flex-direction:column}}
/* header */
.header{{
  background:var(--surface);border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:16px;padding:0 20px;height:52px;
  flex-shrink:0;
}}
.brand{{display:flex;align-items:center;gap:8px;flex:1}}
.brand-icon{{color:var(--accent);font-size:18px;font-weight:700}}
.brand-name{{font-size:15px;font-weight:700;color:var(--text)}}
.brand-sep{{color:var(--border2)}}
.brand-sub{{color:var(--muted);font-size:12px}}
.mode-toggle{{display:flex;gap:4px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:3px}}
.mode-btn{{padding:4px 14px;border:none;border-radius:6px;background:transparent;color:var(--muted);font-size:12px;font-weight:600;cursor:pointer;transition:.15s}}
.mode-btn.active{{background:var(--accent);color:#fff}}
.header-meta{{color:var(--muted);font-size:11px;white-space:nowrap}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--green);display:inline-block;margin-right:5px;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
/* nav */
.nav{{
  background:var(--surface);border-bottom:1px solid var(--border);
  display:flex;gap:2px;padding:0 16px;flex-shrink:0;
}}
.nav-tab{{
  padding:12px 16px;border:none;background:none;color:var(--muted);
  font-size:13px;font-weight:500;cursor:pointer;border-bottom:2px solid transparent;
  margin-bottom:-1px;transition:.15s;white-space:nowrap;
}}
.nav-tab.active{{color:var(--text);border-bottom-color:var(--accent)}}
.nav-tab:hover:not(.active){{color:var(--text)}}
/* content */
.content{{flex:1;overflow-y:auto;padding:20px}}
.panel{{display:none;max-width:1200px;margin:0 auto}}
.panel.visible{{display:block}}
/* KPIs */
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:16px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px}}
.kpi-label{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px}}
.kpi-value{{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}}
.kpi-unit{{font-size:12px;color:var(--muted);font-weight:400}}
/* metrics grid */
.metrics-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px;margin-top:16px}}
.metric-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px}}
.metric-label{{font-size:11px;color:var(--muted);margin-bottom:4px}}
.metric-value{{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}}
.metric-sub{{font-size:10px;color:var(--muted);margin-top:2px}}
/* benchmark row */
.bench-row{{display:flex;gap:16px;flex-wrap:wrap;background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:12px 16px;margin-bottom:16px}}
.bench-item{{display:flex;flex-direction:column}}
.bench-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
.bench-val{{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}}
/* charts */
.chart-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 8px}}
/* grid */
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:680px){{.grid2{{grid-template-columns:1fr}}}}
/* tables */
.table-scroll{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border2);color:var(--muted);font-weight:600;white-space:nowrap;font-size:11px}}
td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}}
tr:hover td{{background:var(--surface2)}}
.mono{{font-variant-numeric:tabular-nums;font-size:12px}}
.small{{font-size:11px;color:var(--muted)}}
/* badges */
.badge{{display:inline-block;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:700}}
.badge-open{{background:rgba(46,160,67,.15);color:var(--green);border:1px solid rgba(46,160,67,.3)}}
.badge-closed{{background:rgba(139,148,158,.12);color:var(--muted);border:1px solid rgba(139,148,158,.2)}}
/* colors */
.pos{{color:var(--green)}}
.neg{{color:var(--red)}}
.muted{{color:var(--muted)}}
/* heatmap */
.heatmap{{border-collapse:separate;border-spacing:3px}}
.heatmap th,.heatmap td{{border:none;border-radius:4px;padding:5px 8px;font-size:11px;text-align:center;white-space:nowrap}}
.heatmap th{{background:transparent;color:var(--muted);padding:4px 8px}}
.hm-year{{color:var(--muted);background:transparent!important;font-weight:700;text-align:right!important;padding-right:12px!important}}
.hm-empty{{background:var(--surface2);color:var(--muted)}}
.hm-total{{background:var(--surface2);font-weight:700}}
/* stat list */
.stat-list{{display:flex;flex-direction:column;gap:6px}}
.stat-row{{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;background:var(--surface2);border-radius:6px;font-size:12px}}
.stat-row span:first-child{{color:var(--muted)}}
/* section */
.section-header{{display:flex;align-items:center;gap:12px;margin-bottom:16px}}
.period-tag{{background:var(--surface2);border:1px solid var(--border);color:var(--muted);border-radius:6px;padding:3px 10px;font-size:11px;font-weight:600}}
.section-title{{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}}
.section-block{{margin-bottom:20px}}
/* empty state */
.empty-state{{text-align:center;padding:60px 20px;color:var(--muted)}}
.empty-icon{{font-size:40px;margin-bottom:12px}}
.empty-title{{font-size:16px;font-weight:600;color:var(--text);margin-bottom:6px}}
.empty-sub{{font-size:13px}}
.p16{{padding:16px 0}}
/* note */
.disclaimer{{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:10px 14px;font-size:11px;color:var(--muted);margin-top:20px}}
/* clickable rows */
.tr-click{{cursor:pointer;transition:background .1s}}
.tr-click:hover td{{background:rgba(56,139,253,.08)!important}}
.table-hint{{font-size:11px;color:var(--muted);margin-bottom:6px}}
/* trade modal */
#trade-modal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:8000;align-items:center;justify-content:center;padding:16px}}
#trade-modal.open{{display:flex}}
.modal-box{{background:var(--surface);border:1px solid var(--border2);border-radius:14px;width:100%;max-width:560px;max-height:90vh;overflow-y:auto}}
.modal-header{{display:flex;align-items:center;justify-content:space-between;padding:18px 20px 12px;border-bottom:1px solid var(--border)}}
.modal-ticker{{font-size:22px;font-weight:800;letter-spacing:.02em}}
.modal-close{{background:none;border:none;color:var(--muted);font-size:22px;cursor:pointer;line-height:1;padding:2px 6px;border-radius:4px}}
.modal-close:hover{{background:var(--surface2);color:var(--text)}}
.modal-body{{padding:16px 20px 20px}}
.modal-section{{margin-bottom:14px}}
.modal-section-title{{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:8px;font-weight:700}}
.modal-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.modal-field{{background:var(--surface2);border-radius:8px;padding:10px 12px}}
.modal-field-label{{font-size:10px;color:var(--muted);margin-bottom:3px}}
.modal-field-value{{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}}
.modal-reason{{background:var(--surface2);border-radius:8px;padding:10px 12px;font-size:12px;color:var(--muted);line-height:1.5}}
</style>
</head>
<body>
<div id="lock">
  <div class="lock-box">
    <div class="lock-logo">▲</div>
    <div class="lock-title">Market Edge</div>
    <div class="lock-sub">Paper Trading Terminal — wpisz hasło</div>
    <input id="pw-in" type="password" placeholder="Hasło..." autofocus autocomplete="current-password">
    <button class="lock-btn" onclick="window._checkPw(document.getElementById('pw-in').value)">Wejdź</button>
    <div id="pw-err">Nieprawidłowe hasło</div>
  </div>
</div>

<div id="app">
  <div class="header">
    <div class="brand">
      <span class="brand-icon">▲</span>
      <span class="brand-name">Market Edge</span>
      <span class="brand-sep">|</span>
      <span class="brand-sub">Paper Trader</span>
    </div>
    <div class="mode-toggle">
      <button class="mode-btn active" data-mode="live" onclick="setMode('live')">Live</button>
      <button class="mode-btn" data-mode="backtest" onclick="setMode('backtest')">Backtest</button>
    </div>
    <div class="header-meta">
      <span class="dot"></span>{updated}
    </div>
  </div>

  <div class="nav" id="nav">
    <button class="nav-tab active" data-tab="overview" onclick="setTab('overview')">Przegląd</button>
    <button class="nav-tab" data-tab="positions" onclick="setTab('positions')">Pozycje</button>
    <button class="nav-tab" data-tab="trades" onclick="setTab('trades')">Transakcje</button>
    <button class="nav-tab" data-tab="strategies" onclick="setTab('strategies')">Strategie</button>
    <button class="nav-tab" data-tab="analysis" onclick="setTab('analysis')">Analiza</button>
    <button class="nav-tab" id="tab-signals-btn" data-tab="signals" onclick="setTab('signals')">Sygnały</button>
  </div>

  <div class="content">
    <div class="panel" id="p-live-overview">{live_overview}</div>
    <div class="panel" id="p-live-positions">{live_positions}</div>
    <div class="panel" id="p-live-trades">{live_trades}</div>
    <div class="panel" id="p-live-strategies">{live_strategies}</div>
    <div class="panel" id="p-live-analysis">{live_analysis}</div>
    <div class="panel" id="p-live-signals">{live_signals}</div>

    <div class="panel" id="p-backtest-overview">{bt_overview}</div>
    <div class="panel" id="p-backtest-positions">{bt_positions}</div>
    <div class="panel" id="p-backtest-trades">{bt_trades}</div>
    <div class="panel" id="p-backtest-strategies">{bt_strategies}</div>
    <div class="panel" id="p-backtest-analysis">{bt_analysis}</div>
    <div class="panel" id="p-backtest-signals"><p class="muted p16">Sygnały dostępne tylko w trybie Live.</p></div>

    <div class="disclaimer">
      ⚠️ Symulacja paper trading — nie porada inwestycyjna. System wirtualny, brak realnych transakcji.
      Wiarygodność wyników wymaga 100–200+ zamkniętych transakcji.
    </div>
  </div>
</div>

<!-- Trade detail modal -->
<div id="trade-modal" onclick="if(event.target===this)closeModal()">
  <div class="modal-box">
    <div class="modal-header">
      <div>
        <div class="modal-ticker" id="m-ticker">—</div>
        <div id="m-strategy" style="color:var(--muted);font-size:12px;margin-top:2px"></div>
      </div>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>
    <div class="modal-body">
      <div class="modal-section">
        <div class="modal-section-title">Status</div>
        <div class="modal-grid">
          <div class="modal-field"><div class="modal-field-label">Status</div><div class="modal-field-value" id="m-status">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Tryb</div><div class="modal-field-value" id="m-mode">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Score sygnału</div><div class="modal-field-value" id="m-score">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Dni trzymania</div><div class="modal-field-value" id="m-hold">—</div></div>
        </div>
      </div>
      <div class="modal-section">
        <div class="modal-section-title">Wejście</div>
        <div class="modal-grid">
          <div class="modal-field"><div class="modal-field-label">Data wejścia</div><div class="modal-field-value" id="m-entry-date">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Cena wejścia</div><div class="modal-field-value" id="m-entry-price">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Stop Loss</div><div class="modal-field-value neg" id="m-sl">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Take Profit</div><div class="modal-field-value pos" id="m-tp">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Akcje</div><div class="modal-field-value" id="m-shares">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Wartość pozycji</div><div class="modal-field-value" id="m-pos-val">—</div></div>
        </div>
      </div>
      <div id="m-exit-section" class="modal-section">
        <div class="modal-section-title">Wyjście</div>
        <div class="modal-grid">
          <div class="modal-field"><div class="modal-field-label">Data wyjścia</div><div class="modal-field-value" id="m-exit-date">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Cena wyjścia</div><div class="modal-field-value" id="m-exit-price">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Powód wyjścia</div><div class="modal-field-value small" id="m-exit-reason">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Ryzyko</div><div class="modal-field-value" id="m-risk">—</div></div>
        </div>
      </div>
      <div class="modal-section">
        <div class="modal-section-title">Wynik</div>
        <div class="modal-grid">
          <div class="modal-field"><div class="modal-field-label">P&amp;L PLN</div><div class="modal-field-value" id="m-pnl">—</div></div>
          <div class="modal-field"><div class="modal-field-label">P&amp;L %</div><div class="modal-field-value" id="m-pnl-pct">—</div></div>
          <div class="modal-field"><div class="modal-field-label">R-multiple</div><div class="modal-field-value" id="m-r">—</div></div>
          <div class="modal-field"><div class="modal-field-label">Max hold dni</div><div class="modal-field-value" id="m-max-hold">—</div></div>
        </div>
      </div>
      <div id="m-reason-section" class="modal-section" style="display:none">
        <div class="modal-section-title">Uzasadnienie sygnału</div>
        <div class="modal-reason" id="m-reason"></div>
      </div>
    </div>
  </div>
</div>

<script>
{build_trades_js(live, bt)}

(function(){{
  var HASH="{pw_hash}",KEY="mept_v2";
  function unlock(){{
    document.getElementById("lock").style.display="none";
    var app=document.getElementById("app");
    app.style.display="flex";
    setTimeout(function(){{window.dispatchEvent(new Event("resize"))}},120);
  }}
  async function check(pw){{
    var buf=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(pw));
    var hex=Array.from(new Uint8Array(buf)).map(function(b){{return("00"+b.toString(16)).slice(-2)}}).join("");
    if(hex===HASH){{sessionStorage.setItem(KEY,"1");unlock();}}
    else{{document.getElementById("pw-err").style.display="";document.getElementById("pw-in").value="";document.getElementById("pw-in").focus();}}
  }}
  window._checkPw=check;
  window.addEventListener("DOMContentLoaded",function(){{
    if(sessionStorage.getItem(KEY)==="1"){{unlock();return;}}
    document.getElementById("pw-in").addEventListener("keydown",function(e){{if(e.key==="Enter")check(this.value);}});
  }});
}})();

var MODE="live", TAB="overview";

function setMode(m){{
  MODE=m;
  document.querySelectorAll(".mode-btn").forEach(function(b){{b.classList.toggle("active",b.dataset.mode===m)}});
  var sigBtn=document.getElementById("tab-signals-btn");
  if(sigBtn) sigBtn.style.display=(m==="live")?"":"none";
  if(m==="backtest" && TAB==="signals") setTab("overview");
  else updatePanels();
}}

function setTab(t){{
  TAB=t;
  document.querySelectorAll(".nav-tab").forEach(function(b){{b.classList.toggle("active",b.dataset.tab===t)}});
  updatePanels();
}}

function updatePanels(){{
  document.querySelectorAll(".panel").forEach(function(p){{
    var id="p-"+MODE+"-"+TAB;
    p.classList.toggle("visible",p.id===id);
  }});
  setTimeout(function(){{window.dispatchEvent(new Event("resize"))}},80);
}}

function _fmt(v,dec){{
  if(v===null||v===undefined||v==="")return"—";
  var n=parseFloat(v);
  if(isNaN(n))return"—";
  return n.toLocaleString("pl-PL",{{minimumFractionDigits:dec||0,maximumFractionDigits:dec||0}});
}}
function _sign(v,unit){{
  if(v===null||v===undefined||v==="")return"—";
  var n=parseFloat(v);
  return(n>=0?"+":"")+_fmt(n,unit==="pct"?2:0)+(unit==="pct"?"%":" PLN");
}}
function _cls(el,v){{
  var n=parseFloat(v);
  el.className="modal-field-value"+(n>0?" pos":n<0?" neg":"");
}}

function showTrade(id){{
  var t=TRADES[String(id)];
  if(!t)return;
  document.getElementById("m-ticker").textContent=t.ticker;
  document.getElementById("m-strategy").textContent=t.strategy.replace(/_/g," ");
  var stEl=document.getElementById("m-status");
  stEl.textContent=t.status.toUpperCase();
  stEl.className="modal-field-value"+(t.status==="open"?" pos":" muted");
  document.getElementById("m-mode").textContent=t.run_mode;
  document.getElementById("m-score").textContent=t.score?parseFloat(t.score).toFixed(0):"—";
  document.getElementById("m-hold").textContent=t.holding_days?t.holding_days+"d":"—";
  document.getElementById("m-entry-date").textContent=t.entry_date||"—";
  document.getElementById("m-entry-price").textContent=t.entry_price?parseFloat(t.entry_price).toFixed(2)+" USD":"—";
  document.getElementById("m-sl").textContent=t.stop_loss?parseFloat(t.stop_loss).toFixed(2)+" USD":"—";
  document.getElementById("m-tp").textContent=t.take_profit?parseFloat(t.take_profit).toFixed(2)+" USD":"—";
  document.getElementById("m-shares").textContent=t.shares?parseFloat(t.shares).toFixed(1):"—";
  document.getElementById("m-pos-val").textContent=_fmt(t.position_value_pln)+" PLN";
  document.getElementById("m-max-hold").textContent=t.max_holding_days?t.max_holding_days+"d":"—";
  // exit section
  var exitSec=document.getElementById("m-exit-section");
  exitSec.style.display=t.status==="closed"?"":"none";
  if(t.status==="closed"){{
    document.getElementById("m-exit-date").textContent=t.exit_date||"—";
    document.getElementById("m-exit-price").textContent=t.exit_price?parseFloat(t.exit_price).toFixed(2)+" USD":"—";
    document.getElementById("m-exit-reason").textContent=t.exit_reason||"—";
    document.getElementById("m-risk").textContent=_fmt(t.risk_pln)+" PLN";
  }}
  // P&L
  var pnlEl=document.getElementById("m-pnl");
  pnlEl.textContent=_sign(t.pnl_pln,"pln");
  _cls(pnlEl,t.pnl_pln);
  var pnlPctEl=document.getElementById("m-pnl-pct");
  pnlPctEl.textContent=t.pnl_pct?_sign(t.pnl_pct,"pct"):"—";
  _cls(pnlPctEl,t.pnl_pct);
  var rEl=document.getElementById("m-r");
  rEl.textContent=t.r_multiple?(parseFloat(t.r_multiple)>=0?"+":"")+parseFloat(t.r_multiple).toFixed(2)+"R":"—";
  _cls(rEl,t.r_multiple);
  // reason
  var rSec=document.getElementById("m-reason-section");
  if(t.entry_reason){{
    rSec.style.display="";
    document.getElementById("m-reason").textContent=t.entry_reason;
  }}else{{rSec.style.display="none";}}
  document.getElementById("trade-modal").classList.add("open");
  document.body.style.overflow="hidden";
}}

function closeModal(){{
  document.getElementById("trade-modal").classList.remove("open");
  document.body.style.overflow="";
}}

document.addEventListener("keydown",function(e){{if(e.key==="Escape")closeModal();}});

window.addEventListener("DOMContentLoaded",function(){{
  setMode("live");
  setTab("overview");
}});
</script>
</body>
</html>"""


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    os.makedirs(DOCS_DIR, exist_ok=True)

    live = gather("live")
    bt = gather("backtest")

    all_live_trades = live["open_trades"] + live["closed_trades"]
    all_bt_trades = bt["open_trades"] + bt["closed_trades"]

    live_m = compute_metrics(live["snapshots"], all_live_trades, INITIAL_CAPITAL_PLN)
    bt_m = compute_metrics(bt["snapshots"], all_bt_trades, INITIAL_CAPITAL_PLN)

    # Benchmark (SPY B&H) aligned to backtest snapshot dates
    benchmark = []
    bt_dates = [s["snapshot_date"] for s in bt["snapshots"]]
    if bt_dates:
        benchmark = fetch_spy_benchmark(bt_dates, INITIAL_CAPITAL_PLN)

    updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    html = build_html(live, live_m, bt, bt_m, benchmark=benchmark, updated=updated)

    out_path = os.path.join(DOCS_DIR, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    open(os.path.join(DOCS_DIR, ".nojekyll"), "w").close()
    print(f"Dashboard written → {out_path}  ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
