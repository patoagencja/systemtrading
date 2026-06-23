"""Generate static HTML dashboard → docs/index.html.

Premium dark-themed trading terminal. Two modes: Live paper trading &
12-month Backtest. Password-protected via crypto.subtle SHA-256.
"""
import os
import sys
from datetime import datetime, date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, SCRIPT_DIR)  # for importing sibling scripts

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
    return f"{x:,.{dec}f}".replace(",", " ")  # thin space


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

        # Stop history per trade (SIMPLE_DYNAMIC_EXIT_V1). Optional table.
        stop_hist: dict = {}
        try:
            cur.execute(
                """SELECT sh.trade_id, sh.session_date, sh.previous_stop, sh.new_stop,
                          sh.stop_status, sh.reason, sh.max_profit_pct, sh.highest_close
                   FROM stop_history sh
                   JOIN trades t ON t.id = sh.trade_id
                   WHERE t.run_mode=?
                   ORDER BY sh.trade_id, sh.session_date""",
                (mode,),
            )
            for r in cur.fetchall():
                rd = dict(r)
                stop_hist.setdefault(str(rd["trade_id"]), []).append(rd)
        except Exception:
            pass
        d["stop_history"] = stop_hist

    d["open_count"] = len(d["open_trades"])
    return d


# ── benchmark (SPY buy-and-hold aligned to snapshot dates) ──────────────────

def fetch_spy_benchmark(snap_dates: list[str], initial: float) -> list[dict]:
    """Return [{date, value}] aligned to snap_dates. Returns [] on failure.

    FIX (2026-06): Previously used fetch_ohlcv("SPY", period="2y") which only
    fetched the last 2 years of SPY data. Since the backtest starts 2020-01-01,
    the benchmark was missing ~4 years of data and appeared to start only from
    ~2024 on the equity chart. Now uses fetch_ohlcv_range with the actual
    backtest start date so the benchmark spans the full backtest period.
    """
    if not snap_dates:
        return []
    try:
        from app.data_provider import fetch_ohlcv_range
        # Use the earliest snapshot date as start so SPY aligns with portfolio start
        start_date = snap_dates[0]
        end_date = snap_dates[-1]
        spy = fetch_ohlcv_range("SPY", start=start_date, end=end_date)
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
        if result:
            print(f"  Benchmark SPY: {len(result)} points from {result[0]['date']} to {result[-1]['date']}")
        return result
    except Exception as e:
        print(f"  [warn] benchmark fetch failed: {e}")
        return []


# ── Plotly dark chart theme ──────────────────────────────────────────────────

_DARK_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#94a3b8", size=11, family="Inter,system-ui,sans-serif"),
    xaxis=dict(gridcolor="#1e2d3d", linecolor="#1e2d3d", zerolinecolor="#1e2d3d"),
    yaxis=dict(gridcolor="#1e2d3d", linecolor="#1e2d3d", zerolinecolor="#1e2d3d"),
    margin=dict(l=50, r=16, t=36, b=28),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
    hoverlabel=dict(bgcolor="#1a2235", bordercolor="#2a3a4d", font_color="#f1f5f9"),
)


def _chart_html(fig) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": False})


def render_equity_chart(dates, values, benchmark=None, height=300) -> str:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=values, mode="lines", name="Portfolio",
        line=dict(color="#3b82f6", width=2),
        hovertemplate="%{x}<br>%{y:,.0f} PLN<extra></extra>",
    ))
    if benchmark:
        b_dates = [b["date"] for b in benchmark]
        b_vals = [b["value"] for b in benchmark]
        fig.add_trace(go.Scatter(
            x=b_dates, y=b_vals, mode="lines", name="SPY B&H",
            line=dict(color="#10b981", width=1.5, dash="dot"),
            hovertemplate="%{x}<br>%{y:,.0f} PLN<extra></extra>",
        ))
    fig.add_hline(y=INITIAL_CAPITAL_PLN, line_dash="dash", line_color="#2a3a4d", line_width=1)
    layout = dict(_DARK_LAYOUT)
    layout.update(dict(title=dict(text="Krzywa kapitału", font=dict(size=13, color="#94a3b8")),
                       height=height, yaxis_title="PLN"))
    fig.update_layout(**layout)
    return _chart_html(fig)


def render_drawdown_chart(dates, dd_series, height=180) -> str:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=dd_series, mode="lines", fill="tozeroy", name="Drawdown",
        line=dict(color="#ef4444", width=1),
        fillcolor="rgba(239,68,68,0.12)",
        hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>",
    ))
    layout = dict(_DARK_LAYOUT)
    layout.update(dict(title=dict(text="Drawdown (%)", font=dict(size=13, color="#94a3b8")),
                       height=height, yaxis_title="%", showlegend=False))
    fig.update_layout(**layout)
    return _chart_html(fig)


def render_pnl_bar_chart(by_strategy: list[dict], height=220) -> str:
    if not by_strategy:
        return ""
    names = [r["strategy"].replace("_", " ") for r in by_strategy]
    pnls = [r["total_pnl"] or 0 for r in by_strategy]
    colors = ["#10b981" if p >= 0 else "#ef4444" for p in pnls]
    fig = go.Figure(go.Bar(
        x=pnls, y=names, orientation="h",
        marker_color=colors,
        hovertemplate="%{y}<br>%{x:,.0f} PLN<extra></extra>",
    ))
    layout = dict(_DARK_LAYOUT)
    layout.update(dict(title=dict(text="P&L wg strategii", font=dict(size=13, color="#94a3b8")),
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
        return f"rgba(16,185,129,{alpha})"
    return f"rgba(239,68,68,{alpha})"


def render_monthly_heatmap(monthly_returns: dict) -> str:
    if not monthly_returns:
        return "<p class='muted' style='padding:16px 0'>Brak danych</p>"

    months_pl = ["Sty", "Lut", "Mar", "Kwi", "Maj", "Cze",
                 "Lip", "Sie", "Wrz", "Paz", "Lis", "Gru"]

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
                cells += "<td class='hm-empty'>&#x2014;</td>"
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


# ── right panel helpers ──────────────────────────────────────────────────────

def render_right_panel_signals(signals: list) -> str:
    if not signals:
        return "<p class='panel-empty'>Brak sygnałów</p>"
    rows = []
    for s in signals[:8]:
        rr = ""
        if s.get("entry_price") and s.get("stop_loss") and s.get("take_profit"):
            ep = s["entry_price"]
            sl = s["stop_loss"]
            tp = s["take_profit"]
            risk = abs(ep - sl)
            reward = abs(tp - ep)
            if risk > 0:
                rr = f"R/R {reward/risk:.1f}"
        rows.append(
            f"<div class='signal-row'>"
            f"<span class='signal-ticker'>{s['ticker']}</span>"
            f"<div style='text-align:right'>"
            f"<div class='signal-score'>Score {s['score']:.0f}</div>"
            f"<div class='signal-rr'>{rr}</div>"
            f"</div></div>"
        )
    return "".join(rows)


def render_right_panel_positions(trades: list) -> str:
    if not trades:
        return "<p class='panel-empty'>Brak pozycji</p>"
    rows = []
    for t in trades[:8]:
        pnl_pct = t.get("pnl_pct") or 0
        sign = "+" if pnl_pct >= 0 else ""
        cls = "pos" if pnl_pct >= 0 else "neg"
        rows.append(
            f"<div class='pos-row'>"
            f"<span class='pos-ticker'>{t['ticker']}</span>"
            f"<span class='pos-pnl {cls}'>{sign}{pnl_pct:.1f}%</span>"
            f"</div>"
        )
    return "".join(rows)


def render_intraday_signals_mini(signals: list) -> str:
    if not signals:
        return "<p class='panel-empty'>Brak sygnałów intraday</p>"
    rows = []
    for s in signals[:6]:
        score = float(s.get("score") or 0)
        strategy = (s.get("strategy") or "").replace("_", " ")[:16]
        status = (s.get("status") or "")[:3].upper()
        rows.append(
            f"<div class='signal-row'>"
            f"<div><div class='signal-ticker'>{s.get('ticker','')}</div>"
            f"<div class='signal-rr'>{strategy}</div></div>"
            f"<div><div class='signal-score'>{score:.0f}</div>"
            f"<div class='signal-rr'>{status}</div></div>"
            f"</div>"
        )
    return "".join(rows)


def render_intraday_positions_mini(positions: list) -> str:
    if not positions:
        return "<p class='panel-empty'>Brak pozycji intraday</p>"
    rows = []
    for p in positions[:6]:
        entry = float(p.get("entry_price") or 0)
        strategy = (p.get("strategy") or "").replace("_", " ")[:15]
        rows.append(
            f"<div class='pos-row'>"
            f"<div><div class='pos-ticker'>{p.get('ticker','')}</div>"
            f"<div class='signal-rr'>{strategy}</div></div>"
            f"<div class='pos-pnl'>{entry:.2f}</div>"
            f"</div>"
        )
    return "".join(rows)


# ── panel renderers ──────────────────────────────────────────────────────────

def render_overview_panel(d: dict, m: dict, benchmark=None) -> str:
    snaps = d["snapshots"]
    period = f"{snaps[0]['snapshot_date']} &#x2192; {snaps[-1]['snapshot_date']}" if snaps else "&#x2014;"
    latest = snaps[-1] if snaps else {}
    portfolio_val = latest.get("total_value_pln", INITIAL_CAPITAL_PLN)
    cash = latest.get("cash_pln", INITIAL_CAPITAL_PLN)
    invested = latest.get("invested_pln", 0)
    ret_pct = m["total_return_pct"]
    pnl = portfolio_val - INITIAL_CAPITAL_PLN

    equity_chart = render_equity_chart(m["dates"], m["values"], benchmark) if m["values"] else ""
    dd_chart = render_drawdown_chart(m["dates"], m["dd_series"]) if m["dd_series"] else ""

    pf_str = f"{m['profit_factor']:.2f}" if m["profit_factor"] < 900 else "&#x221E;"
    calmar_str = f"{m['calmar']:.2f}" if m["calmar"] < 900 else "&#x221E;"

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
            <span class="bench-label">SPY B&amp;H</span>
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
          <div class="empty-icon">&#x1F916;</div>
          <div class="empty-title">Brak danych</div>
          <div class="empty-sub">Robot uruchamia sie o 23:00 UTC w dni robocze.</div>
        </div>"""

    pnl_sign = "+" if pnl >= 0 else ""
    ann_ret = m.get("ann_return_pct", 0) or 0

    return f"""
<div class="section-header">
  <span class="period-tag">{period}</span>
</div>

{no_data}

<div class="kpi-grid">
  <div class="kpi-card kpi-primary">
    <div class="kpi-label">Wartosc portfela</div>
    <div class="kpi-value">{_fmt(portfolio_val)} <span class="kpi-unit">PLN</span></div>
    <div class="kpi-change {_cls(pnl)}">{pnl_sign}{_fmt(pnl)} PLN od startu</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Zwrot laczny</div>
    <div class="kpi-value {_cls(ret_pct)}">{_pct(ret_pct)}</div>
    <div class="kpi-sub">Ann. {_pct(ann_ret)}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Win Rate</div>
    <div class="kpi-value">{m['win_rate']:.1f}%</div>
    <div class="kpi-sub">{m.get('total_trades', 0)} transakcji</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Max Drawdown</div>
    <div class="kpi-value neg">{m['max_drawdown_pct']:.1f}%</div>
    <div class="kpi-sub">Profit factor {pf_str}</div>
  </div>
</div>

{note_benchmark}

<div class="charts-row">
  <div class="card">
    {equity_chart if equity_chart else '<p class="muted p16">Brak danych do wykresu.</p>'}
    {dd_chart if dd_chart else ''}
  </div>
  <div class="metrics-stack">
    <div class="metric-mini">
      <div class="metric-mini-label">Sharpe Ratio</div>
      <div class="metric-mini-value">{m['sharpe']:.2f}</div>
      <div class="kpi-sub">Annualised, RF=4%</div>
    </div>
    <div class="metric-mini">
      <div class="metric-mini-label">Sortino</div>
      <div class="metric-mini-value">{m['sortino']:.2f}</div>
      <div class="kpi-sub">Downside deviation</div>
    </div>
    <div class="metric-mini">
      <div class="metric-mini-label">Calmar</div>
      <div class="metric-mini-value">{calmar_str}</div>
      <div class="kpi-sub">Ann. return / Max DD</div>
    </div>
    <div class="metric-mini">
      <div class="metric-mini-label">Expectancy</div>
      <div class="metric-mini-value {_cls(m['expectancy_pln'])}">{_sgn(m['expectancy_pln'], dec=0)}</div>
      <div class="kpi-sub">Per trade</div>
    </div>
    <div class="metric-mini">
      <div class="metric-mini-label">Avg R</div>
      <div class="metric-mini-value {_cls(m['avg_r_multiple'])}">{m['avg_r_multiple']:+.2f}R</div>
      <div class="kpi-sub">Reward / risk units</div>
    </div>
    <div class="metric-mini">
      <div class="metric-mini-label">Avg hold</div>
      <div class="metric-mini-value">{m['avg_holding_days']:.1f}d</div>
      <div class="kpi-sub">Days per trade</div>
    </div>
  </div>
</div>

<div class="card" style="margin-top:16px">
  <div class="card-title">Portfolio Details</div>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px">
    <div><div class="kpi-label">Gotowka</div><div style="font-size:16px;font-weight:600;color:var(--text);margin-top:4px">{_fmt(cash)} PLN</div></div>
    <div><div class="kpi-label">Otwarte pozycje</div><div style="font-size:16px;font-weight:600;color:var(--text);margin-top:4px">{d['open_count']}</div></div>
    <div><div class="kpi-label">Profit Factor</div><div style="font-size:16px;font-weight:600;color:var(--text);margin-top:4px">{pf_str}</div></div>
    <div><div class="kpi-label">Avg hold</div><div style="font-size:16px;font-weight:600;color:var(--text);margin-top:4px">{m['avg_holding_days']:.1f}d</div></div>
  </div>
</div>"""


def render_positions_panel(d: dict) -> str:
    trades = d["open_trades"]
    if not trades:
        return """<div class="empty-state">
          <div class="empty-icon">&#x1F4ED;</div>
          <div class="empty-title">Brak otwartych pozycji</div>
          <div class="empty-sub">Nowe pozycje pojawia sie po spelnieniu warunkow strategii.</div>
        </div>"""

    rows = []
    for t in trades:
        pnl = t.get("pnl_pln") or 0
        pnl_pct = t.get("pnl_pct") or 0
        hold = t.get("holding_days") or 0
        tid = t.get("id", "")
        active_stop = t.get("active_stop_loss")
        if active_stop is None:
            active_stop = t.get("stop_loss") or 0
        max_profit = (t.get("max_profit_pct") or 0) * 100
        locked = (t.get("locked_profit_pct") or 0) * 100
        status = t.get("stop_status") or "INITIAL"
        status_cls = {
            "INITIAL": "badge-closed", "BREAK_EVEN": "badge-open",
            "PROFIT_LOCK_2": "badge-open", "PROFIT_LOCK_4": "badge-open",
            "PROFIT_LOCK_7": "badge-open", "TRAILING": "badge-open",
        }.get(status, "badge-closed")
        rows.append(
            f"<tr class='tr-click' onclick='showTrade({tid})'>"
            f"<td><b>{t['ticker']}</b></td>"
            f"<td class='mono'>{t['strategy'].replace('_', ' ')}</td>"
            f"<td class='mono'>{t['entry_date']}</td>"
            f"<td class='mono'>{t['entry_price']:.2f}</td>"
            f"<td class='mono'>{active_stop:.2f}</td>"
            f"<td class='mono'>{t['take_profit']:.2f}</td>"
            f"<td class='mono {_cls(max_profit)}'>{max_profit:+.1f}%</td>"
            f"<td class='mono {_cls(locked)}'>{locked:+.1f}%</td>"
            f"<td><span class='badge {status_cls}'>{status.replace('_', ' ')}</span></td>"
            f"<td class='mono'>{t['shares']:.1f}</td>"
            f"<td class='mono'>{hold}d</td>"
            f"<td class='mono {_cls(pnl)}'>{_sgn(pnl)}</td>"
            f"<td class='mono {_cls(pnl_pct)}'>{_pct(pnl_pct)}</td>"
            f"</tr>"
        )

    return f"""<p class="table-hint">Kliknij wiersz, zeby zobaczyc szczegoly transakcji</p>
<div class="table-scroll">
<table>
<thead><tr>
  <th>Ticker</th><th>Strategia</th><th>Wejscie</th><th>Cena wej.</th>
  <th>Active Stop</th><th>TP</th><th>Max Profit</th><th>Locked</th><th>Status</th>
  <th>Akcje</th><th>Czas</th>
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
        exit_date = t.get("exit_date") or "&#x2014;"
        exit_reason = t.get("exit_reason") or "&#x2014;"
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
            f"{'&#x2014;' if pnl_pct is None else _pct(pnl_pct)}</td>"
            f"<td class='mono'>{f'{r:+.2f}R' if r is not None else '&#x2014;'}</td>"
            f"<td class='mono small'>{exit_reason}</td>"
            f"</tr>"
        )

    return f"""<p class="table-hint">Kliknij wiersz, zeby zobaczyc szczegoly</p>
<div class="table-scroll">
<table>
<thead><tr>
  <th>Status</th><th>Ticker</th><th>Strategia</th>
  <th>Wejscie</th><th>Wyjscie</th>
  <th>P&amp;L PLN</th><th>P&amp;L %</th><th>R</th><th>Powod wyjscia</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>"""


def render_strategies_panel(d: dict, m: dict) -> str:
    strats = d["by_strategy"]
    pnl_chart = render_pnl_bar_chart(strats)

    if not strats:
        return "<p class='muted p16'>Brak danych o strategiach.</p>"

    _STRATEGY_ICONS = {
        "MEAN_REVERSION": ("MR", "background:rgba(59,130,246,0.2);color:#3b82f6"),
        "MOMENTUM_BREAKOUT": ("MB", "background:rgba(16,185,129,0.2);color:#10b981"),
        "PULLBACK_TREND": ("PT", "background:rgba(245,158,11,0.2);color:#f59e0b"),
        "ETF_RELATIVE_STRENGTH": ("ET", "background:rgba(139,92,246,0.2);color:#8b5cf6"),
    }

    cards = []
    for s in strats:
        pnl = s.get("total_pnl") or 0
        wins = int(s.get("wins") or 0)
        n = int(s.get("n") or 0)
        wr = s.get("win_rate") or 0
        strat_key = s["strategy"]
        icon_label, icon_style = _STRATEGY_ICONS.get(strat_key, ("ST", "background:rgba(100,116,139,0.2);color:#64748b"))
        cards.append(
            f"<div class='strategy-card'>"
            f"<div class='strategy-icon' style='{icon_style}'>{icon_label}</div>"
            f"<div class='strategy-info'>"
            f"<div class='strategy-name'>{strat_key.replace('_', ' ')}</div>"
            f"<div class='strategy-meta'>{n} transakcji &middot; Win {wr:.0f}%</div>"
            f"</div>"
            f"<div class='strategy-pnl {_cls(pnl)}'>{_sgn(pnl)}</div>"
            f"</div>"
        )

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
<div class="strategy-grid" style="margin-bottom:24px">
  {"".join(cards)}
</div>

<div class="grid2">
  <div class="card">
    <div class="card-title">Wyniki wg strategii</div>
    <div class="table-scroll">
    <table>
    <thead><tr>
      <th>Strategia</th><th>Transakcje</th><th>W/L</th>
      <th>Win rate</th><th>Total P&amp;L</th><th>Avg P&amp;L</th>
    </tr></thead>
    <tbody>{"".join(rows)}</tbody>
    </table></div>
  </div>
  <div class="card">
    <div class="card-title">Statystyki globalne</div>
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
<div class="card" style="margin-top:16px">{pnl_chart}</div>"""


def render_signals_panel(d: dict) -> str:
    signals = d["pending_signals"]
    if not signals:
        return """<div class="empty-state">
          <div class="empty-icon">&#x1F4E1;</div>
          <div class="empty-title">Brak oczekujacych sygnalow</div>
          <div class="empty-sub">Sygnaly pojawiaja sie po wieczornym skanie (23:00 UTC).</div>
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
  <th>Entry</th><th>Stop</th><th>Target</th><th>RSI</th><th>Powod</th>
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
            f"<td class='mono'>{'&#x2014;' if not ty else f'{wr:.0f}%'}</td>"
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
                f"<td class='mono'>{t.get('exit_date','&#x2014;')}</td>"
                f"<td class='mono {_cls(pnl)}'>{_sgn(pnl)}</td>"
                f"<td class='mono {_cls(pnl_pct)}'>{_pct(pnl_pct)}</td></tr>")

    best_rows = "".join(trade_mini_row(t) for t in best5) or "<tr><td colspan=4>&#x2014;</td></tr>"
    worst_rows = "".join(trade_mini_row(t) for t in worst5) or "<tr><td colspan=4>&#x2014;</td></tr>"

    yearly_table = render_yearly_breakdown(d["snapshots"], d["closed_trades"])

    return f"""
<div class="card section-block">
  <div class="card-title">Zestawienie roczne</div>
  {yearly_table}
</div>

<div class="card section-block" style="margin-top:16px">
  <div class="card-title">Miesieczny P&amp;L</div>
  {heatmap}
</div>

<div class="grid2" style="margin-top:16px">
  <div class="card">
    <div class="card-title">Powody zamkniecia</div>
    <div class="stat-list">{reasons_rows}</div>
  </div>
  <div class="card">
    <div class="card-title">Laczne statystyki</div>
    <div class="stat-list">
      <div class="stat-row"><span>Zwrot laczny</span>
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

<div class="grid2" style="margin-top:16px">
  <div class="card">
    <div class="card-title">Top 5 zyski</div>
    <div class="table-scroll"><table>
    <thead><tr><th>Ticker</th><th>Data</th><th>P&amp;L</th><th>%</th></tr></thead>
    <tbody>{best_rows}</tbody></table></div>
  </div>
  <div class="card">
    <div class="card-title">Top 5 straty</div>
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
            # SIMPLE_DYNAMIC_EXIT_V1 trade-management fields
            "initial_stop_loss": safe(t.get("initial_stop_loss")),
            "active_stop_loss": safe(t.get("active_stop_loss")),
            "highest_high_since_entry": safe(t.get("highest_high_since_entry")),
            "highest_close_since_entry": safe(t.get("highest_close_since_entry")),
            "max_profit_pct": safe(t.get("max_profit_pct")),
            "locked_profit_pct": safe(t.get("locked_profit_pct")),
            "stop_status": safe(t.get("stop_status")),
            "exit_logic_version": safe(t.get("exit_logic_version")),
            "active_stop_effective_date": safe(t.get("active_stop_effective_date")),
            "max_unrealized_pnl_pct": safe(t.get("max_unrealized_pnl_pct")),
        }

    stop_hist = {}
    stop_hist.update(live.get("stop_history") or {})
    stop_hist.update(bt.get("stop_history") or {})
    return (f"var TRADES={json.dumps(trades, ensure_ascii=False)};\n"
            f"var STOP_HISTORY={json.dumps(stop_hist, ensure_ascii=False)};")


# ── full HTML assembly ────────────────────────────────────────────────────────

def build_html(live: dict, live_m: dict, bt: dict, bt_m: dict,
               benchmark=None, updated: str = "",
               intraday: dict = None) -> str:
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

    # right panel content
    live_signals_mini = render_right_panel_signals(live["pending_signals"])
    live_positions_mini = render_right_panel_positions(live["open_trades"])
    bt_signals_mini = "<p class='panel-empty'>Tylko tryb Live</p>"
    bt_positions_mini = render_right_panel_positions(bt["open_trades"])

    # ── intraday panels ──────────────────────────────────────────────────────
    _id_ph = ("<div class='empty-state'><div class='empty-icon'>&#x1F4C8;</div>"
              "<div class='empty-title'>Brak danych intraday</div>"
              "<div class='empty-sub'>Uruchom backtest intraday lub poczekaj na pierwszy skan live.</div></div>")
    id_live_overview = id_live_positions = id_live_signals = id_live_trades = id_live_strategies = id_live_analysis = _id_ph
    id_bt_overview = id_bt_positions = id_bt_trades = id_bt_strategies = id_bt_analysis = _id_ph
    id_live_sig_mini = id_live_pos_mini = id_bt_pos_mini = ""

    if intraday:
        try:
            import generate_intraday_dashboard as _id
            idlive, idlive_m = intraday["live"], intraday["live_m"]
            idbt, idbt_m = intraday["bt"], intraday["bt_m"]
            id_live_overview   = _id.render_overview_panel(idlive, idlive_m)
            id_live_positions  = _id.render_positions_panel(idlive)
            id_live_signals    = _id.render_signals_panel(idlive)
            id_live_trades     = _id.render_trades_panel(idlive)
            id_live_strategies = _id.render_strategies_panel(idlive, idlive_m)
            id_live_analysis   = _id.render_analysis_panel(idlive, idlive_m)
            id_bt_overview     = _id.render_overview_panel(idbt, idbt_m)
            id_bt_positions    = _id.render_positions_panel(idbt)
            id_bt_trades       = _id.render_trades_panel(idbt)
            id_bt_strategies   = _id.render_strategies_panel(idbt, idbt_m)
            id_bt_analysis     = _id.render_analysis_panel(idbt, idbt_m)
            id_live_sig_mini   = render_intraday_signals_mini(idlive.get("signals", []))
            id_live_pos_mini   = render_intraday_positions_mini(idlive.get("open_positions", []))
            id_bt_pos_mini     = render_intraday_positions_mini(idbt.get("open_positions", []))
        except Exception as e:
            print(f"  [warn] intraday panel rendering failed: {e}")

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Edge Paper Trader</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
:root {{
  --bg: #0a0e1a;
  --surface: #111827;
  --surface2: #1a2235;
  --border: #1e2d3d;
  --border2: #2a3a4d;
  --accent: #3b82f6;
  --accent-glow: rgba(59,130,246,0.15);
  --green: #10b981;
  --green-bg: rgba(16,185,129,0.1);
  --red: #ef4444;
  --red-bg: rgba(239,68,68,0.1);
  --yellow: #f59e0b;
  --text: #f1f5f9;
  --text2: #94a3b8;
  --text3: #4b6280;
  --sidebar-w: 220px;
  --header-h: 60px;
  --right-w: 280px;
  --radius: 12px;
  --radius-sm: 8px;
  --shadow: 0 1px 3px rgba(0,0,0,0.4), 0 1px 2px rgba(0,0,0,0.3);
  --shadow-lg: 0 4px 20px rgba(0,0,0,0.5);
  --font: 'Inter', system-ui, -apple-system, sans-serif;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ height: 100%; }}
body {{
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  font-size: 13px;
  line-height: 1.5;
}}

/* ── LOCK SCREEN ── */
#lock-screen {{
  position: fixed; inset: 0;
  background: var(--bg);
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  z-index: 9999;
}}
.lock-logo {{ font-size: 48px; color: var(--accent); margin-bottom: 8px; }}
.lock-title {{ font-size: 24px; font-weight: 700; color: var(--text); margin-bottom: 4px; }}
.lock-sub {{ font-size: 14px; color: var(--text2); margin-bottom: 32px; }}
.lock-form {{ display: flex; flex-direction: column; align-items: center; gap: 12px; width: 300px; }}
.lock-input {{
  width: 100%; padding: 12px 16px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius-sm); color: var(--text); font-size: 15px; outline: none;
  font-family: var(--font);
}}
.lock-input:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }}
.lock-btn {{
  width: 100%; padding: 12px;
  background: var(--accent); border: none;
  border-radius: var(--radius-sm); color: white;
  font-size: 15px; font-weight: 600; cursor: pointer;
  font-family: var(--font);
}}
.lock-btn:hover {{ background: #2563eb; }}
.lock-error {{ color: var(--red); font-size: 13px; display: none; }}

/* ── APP SHELL ── */
#app {{ display: none; height: 100%; flex-direction: column; }}

/* ── HEADER ── */
#header {{
  height: var(--header-h);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 20px;
  position: sticky; top: 0; z-index: 100;
  flex-shrink: 0;
}}
.header-left {{ display: flex; align-items: center; gap: 0; }}
.logo {{ display: flex; align-items: center; gap: 8px; }}
.logo-icon {{ font-size: 20px; color: var(--accent); }}
.logo-text {{ font-size: 16px; font-weight: 700; color: var(--text); }}
.logo-sub {{ font-size: 11px; color: var(--text3); margin-top: 2px; }}
.engine-tabs {{
  display: flex; background: var(--bg);
  border-radius: var(--radius-sm); padding: 3px; margin-left: 24px;
}}
.engine-tab {{
  padding: 5px 14px; border: none; background: transparent;
  color: var(--text2); border-radius: 6px; cursor: pointer;
  font-size: 13px; font-weight: 500; font-family: var(--font);
}}
.engine-tab.active {{ background: var(--surface2); color: var(--text); }}
.header-center {{ display: flex; align-items: center; }}
.mode-tabs {{
  display: flex; background: var(--bg);
  border-radius: var(--radius-sm); padding: 3px;
}}
.mode-tab {{
  padding: 6px 16px; border: none; background: transparent;
  color: var(--text2); border-radius: 6px; cursor: pointer;
  font-size: 13px; font-weight: 500; font-family: var(--font);
  transition: all 0.15s;
}}
.mode-tab.active {{ background: var(--accent); color: white; }}
.header-right {{ display: flex; align-items: center; }}
.update-time {{ font-size: 12px; color: var(--text3); }}
.dot-live {{
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--green); display: inline-block; margin-right: 5px;
  animation: pulse 2s infinite;
}}
@keyframes pulse {{ 0%,100% {{ opacity:1 }} 50% {{ opacity:.4 }} }}

/* ── BODY WRAP ── */
#body-wrap {{
  display: flex;
  height: calc(100vh - var(--header-h));
  overflow: hidden;
}}

/* ── SIDEBAR ── */
#sidebar {{
  width: var(--sidebar-w);
  min-height: 100%;
  background: var(--surface);
  border-right: 1px solid var(--border);
  padding: 16px 0;
  flex-shrink: 0;
  overflow-y: auto;
}}
.nav-section-label {{
  font-size: 10px; text-transform: uppercase;
  letter-spacing: 0.8px; color: var(--text3);
  font-weight: 600; padding: 8px 20px 4px;
  margin-top: 8px;
}}
.nav-item {{
  display: flex; align-items: center; gap: 10px;
  padding: 10px 20px; color: var(--text2);
  cursor: pointer; font-size: 14px; font-weight: 500;
  transition: all 0.15s;
  border-left: 3px solid transparent;
  text-decoration: none;
}}
.nav-item:hover {{ background: var(--surface2); color: var(--text); }}
.nav-item.active {{
  background: var(--accent-glow);
  color: var(--accent);
  border-left-color: var(--accent);
}}
.nav-icon {{ font-size: 15px; width: 20px; text-align: center; }}
.sidebar-divider {{
  height: 1px; background: var(--border);
  margin: 12px 16px;
}}

/* ── MAIN ── */
#main {{
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  background: var(--bg);
}}
.tab-panel {{ display: none; }}
.tab-panel.active {{ display: block; }}

/* ── RIGHT PANEL ── */
#right-panel {{
  width: var(--right-w);
  min-height: 100%;
  background: var(--surface);
  border-left: 1px solid var(--border);
  overflow-y: auto;
  flex-shrink: 0;
}}
.panel-section {{ padding: 16px; border-bottom: 1px solid var(--border); }}
.panel-title {{
  font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.8px; color: var(--text3);
  font-weight: 600; margin-bottom: 12px;
}}
.panel-empty {{ font-size: 12px; color: var(--text3); padding: 8px 0; }}
.signal-row {{
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px;
}}
.signal-row:last-child {{ border-bottom: none; }}
.signal-ticker {{ font-weight: 600; color: var(--text); }}
.signal-score {{ color: var(--accent); font-weight: 600; font-size: 12px; }}
.signal-rr {{ color: var(--text2); font-size: 11px; }}
.pos-row {{
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px;
}}
.pos-row:last-child {{ border-bottom: none; }}
.pos-ticker {{ font-weight: 600; color: var(--text); }}
.pos-pnl {{ font-size: 12px; font-weight: 600; }}

/* ── CARDS ── */
.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  box-shadow: var(--shadow);
  transition: border-color 0.2s;
}}
.card:hover {{ border-color: var(--border2); }}
.card-title {{
  font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.8px; color: var(--text3);
  font-weight: 600; margin-bottom: 16px;
}}

/* ── KPI GRID ── */
.kpi-grid {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}}
@media (max-width: 1100px) {{ .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
.kpi-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
}}
.kpi-primary {{
  background: linear-gradient(135deg, #1a2235 0%, #1e2d42 100%);
  border-color: var(--accent);
  box-shadow: 0 0 20px rgba(59,130,246,0.1);
}}
.kpi-value {{
  font-size: 28px; font-weight: 700;
  color: var(--text); letter-spacing: -0.5px; margin: 4px 0;
}}
.kpi-label {{
  font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.8px; color: var(--text3); font-weight: 600;
}}
.kpi-unit {{ font-size: 14px; font-weight: 400; color: var(--text2); }}
.kpi-change {{ font-size: 12px; margin-top: 4px; }}
.kpi-sub {{ font-size: 12px; color: var(--text3); margin-top: 4px; }}

/* ── CHARTS ROW ── */
.charts-row {{
  display: grid;
  grid-template-columns: 1fr 200px;
  gap: 16px;
  margin-bottom: 24px;
}}
@media (max-width: 900px) {{ .charts-row {{ grid-template-columns: 1fr; }} }}
.metrics-stack {{ display: flex; flex-direction: column; gap: 12px; }}
.metric-mini {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
}}
.metric-mini-label {{
  font-size: 10px; text-transform: uppercase;
  letter-spacing: 0.8px; color: var(--text3); font-weight: 600;
}}
.metric-mini-value {{
  font-size: 20px; font-weight: 700;
  color: var(--text); margin-top: 4px;
}}

/* ── STRATEGY CARDS ── */
.strategy-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}}
.strategy-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px 20px;
  display: flex; align-items: center; gap: 14px;
  cursor: pointer; transition: all 0.15s;
}}
.strategy-card:hover {{
  border-color: var(--border2);
  transform: translateY(-1px);
}}
.strategy-icon {{
  width: 44px; height: 44px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 700; flex-shrink: 0;
}}
.strategy-info {{ flex: 1; }}
.strategy-name {{ font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 4px; }}
.strategy-meta {{ font-size: 12px; color: var(--text3); }}
.strategy-pnl {{ font-size: 16px; font-weight: 700; }}

/* ── BENCHMARK ROW ── */
.bench-row {{
  display: flex; gap: 16px; flex-wrap: wrap;
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 12px 16px; margin-bottom: 16px;
}}
.bench-item {{ display: flex; flex-direction: column; }}
.bench-label {{ font-size: 10px; color: var(--text3); text-transform: uppercase; letter-spacing: 0.04em; }}
.bench-val {{ font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }}

/* ── GRID ── */
.grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
@media (max-width: 768px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}

/* ── TABLES ── */
.table-scroll {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
thead th {{
  padding: 10px 12px; text-align: left;
  font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.6px; color: var(--text3); font-weight: 600;
  border-bottom: 1px solid var(--border); white-space: nowrap;
}}
tbody tr {{ border-bottom: 1px solid var(--border); transition: background 0.1s; cursor: pointer; }}
tbody tr:hover {{ background: var(--surface2); }}
tbody td {{ padding: 10px 12px; color: var(--text2); }}
tbody td:first-child {{ color: var(--text); font-weight: 600; }}
.mono {{ font-variant-numeric: tabular-nums; font-size: 12px; }}
.small {{ font-size: 11px; color: var(--text3); }}

/* ── HEATMAP ── */
.heatmap {{ border-collapse: separate; border-spacing: 3px; }}
.heatmap th, .heatmap td {{
  border: none; border-radius: 4px;
  padding: 5px 8px; font-size: 11px;
  text-align: center; white-space: nowrap;
}}
.heatmap th {{ background: transparent; color: var(--text3); padding: 4px 8px; }}
.hm-year {{
  color: var(--text3); background: transparent !important;
  font-weight: 700; text-align: right !important; padding-right: 12px !important;
}}
.hm-empty {{ background: var(--surface2); color: var(--text3); }}
.hm-total {{ background: var(--surface2); font-weight: 700; }}

/* ── STAT LIST ── */
.stat-list {{ display: flex; flex-direction: column; gap: 6px; }}
.stat-row {{
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 10px; background: var(--surface2);
  border-radius: 6px; font-size: 12px;
}}
.stat-row span:first-child {{ color: var(--text3); }}

/* ── SECTION / MISC ── */
.section-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }}
.period-tag {{
  background: var(--surface2); border: 1px solid var(--border);
  color: var(--text3); border-radius: 6px;
  padding: 3px 10px; font-size: 11px; font-weight: 600;
}}
.section-title {{
  font-size: 12px; font-weight: 600; color: var(--text3);
  text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px;
}}
.section-block {{ margin-bottom: 16px; }}
.empty-state {{
  text-align: center; padding: 60px 20px; color: var(--text3);
}}
.empty-icon {{ font-size: 40px; margin-bottom: 12px; }}
.empty-title {{ font-size: 16px; font-weight: 600; color: var(--text); margin-bottom: 6px; }}
.empty-sub {{ font-size: 13px; }}
.p16 {{ padding: 16px 0; }}
.muted {{ color: var(--text3); }}

/* ── BADGES ── */
.badge {{
  display: inline-block; border-radius: 4px;
  padding: 2px 7px; font-size: 10px; font-weight: 700;
}}
.badge-open {{
  background: var(--green-bg); color: var(--green);
  border: 1px solid rgba(16,185,129,0.3);
}}
.badge-closed {{
  background: rgba(100,116,139,0.12); color: var(--text3);
  border: 1px solid rgba(100,116,139,0.2);
}}

/* ── COLORS ── */
.pos {{ color: var(--green); }}
.neg {{ color: var(--red); }}

/* ── CLICKABLE ROWS ── */
.tr-click {{ cursor: pointer; transition: background 0.1s; }}
.tr-click:hover td {{ background: rgba(59,130,246,0.06) !important; }}
.table-hint {{ font-size: 11px; color: var(--text3); margin-bottom: 8px; }}

/* ── DISCLAIMER ── */
.disclaimer {{
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 10px 14px;
  font-size: 11px; color: var(--text3); margin-top: 20px;
}}

/* ── TRADE MODAL ── */
#trade-modal {{
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(0,0,0,0.7); backdrop-filter: blur(4px);
  display: none; align-items: center; justify-content: center;
}}
#trade-modal.open {{ display: flex; }}
.modal-box {{
  background: var(--surface2);
  border: 1px solid var(--border2);
  border-radius: var(--radius);
  width: 520px; max-width: 95vw; max-height: 85vh;
  overflow-y: auto; box-shadow: var(--shadow-lg); padding: 28px;
}}
.modal-title {{ font-size: 16px; font-weight: 700; color: var(--text); margin-bottom: 20px; }}
.modal-close {{
  float: right; background: none; border: none;
  color: var(--text3); font-size: 20px; cursor: pointer;
}}
.modal-close:hover {{ color: var(--text); }}
.modal-header {{
  display: flex; align-items: center;
  justify-content: space-between;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 20px;
}}
.modal-ticker {{ font-size: 22px; font-weight: 800; letter-spacing: 0.02em; }}
.modal-body-inner {{ padding: 0; }}
.modal-section {{ margin-bottom: 14px; }}
.modal-section-title {{
  font-size: 10px; text-transform: uppercase;
  letter-spacing: 0.08em; color: var(--text3);
  margin-bottom: 8px; font-weight: 700;
}}
.modal-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
.modal-field {{ background: var(--surface); border-radius: 8px; padding: 10px 12px; border: 1px solid var(--border); }}
.modal-field-label {{ font-size: 10px; color: var(--text3); margin-bottom: 3px; }}
.modal-field-value {{ font-size: 15px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--text); }}
.modal-reason {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 12px;
  font-size: 12px; color: var(--text2); line-height: 1.5;
}}

</style>
</head>
<body>

<!-- Lock Screen -->
<div id="lock-screen">
  <div class="lock-logo">&#x25B2;</div>
  <div class="lock-title">Market Edge</div>
  <div class="lock-sub">Paper Trading Terminal &#x2014; wpisz haslo</div>
  <div class="lock-form">
    <input id="pw-in" class="lock-input" type="password" placeholder="Haslo..." autofocus autocomplete="current-password">
    <button class="lock-btn" onclick="window._checkPw(document.getElementById('pw-in').value)">Wejdz</button>
    <div id="pw-err" class="lock-error">Nieprawidlowe haslo</div>
  </div>
</div>

<!-- App -->
<div id="app">

  <!-- Header -->
  <header id="header">
    <div class="header-left">
      <div class="logo">
        <span class="logo-icon">&#x25B2;</span>
        <span class="logo-text">Market Edge</span>
        <span class="logo-sub">Paper Trader</span>
      </div>
      <div class="engine-tabs">
        <button class="engine-tab active" id="btn-swing" onclick="setEngine('swing')">Swing</button>
        <button class="engine-tab" id="btn-intraday" onclick="setEngine('intraday')">Intraday</button>
      </div>
    </div>
    <div class="header-center">
      <div class="mode-tabs">
        <button class="mode-tab active" id="btn-live" data-mode="live" onclick="setMode('live')">Live Paper</button>
        <button class="mode-tab" id="btn-bt" data-mode="backtest" onclick="setMode('backtest')">Backtest</button>
      </div>
    </div>
    <div class="header-right">
      <div class="update-time"><span class="dot-live"></span>{updated}</div>
    </div>
  </header>

  <!-- Body wrap: sidebar + main + right panel -->
  <div id="body-wrap">

    <!-- Sidebar -->
    <nav id="sidebar">
      <a class="nav-item active" data-tab="overview" href="javascript:void(0)">
        <span class="nav-icon">&#x25A3;</span>Przeglad
      </a>
      <a class="nav-item" data-tab="positions" href="javascript:void(0)">
        <span class="nav-icon">&#x25C8;</span>Pozycje
      </a>
      <a class="nav-item" data-tab="trades" href="javascript:void(0)">
        <span class="nav-icon">&#x2195;</span>Transakcje
      </a>
      <a class="nav-item" data-tab="strategies" href="javascript:void(0)">
        <span class="nav-icon">&#x2B21;</span>Strategie
      </a>
      <a class="nav-item" data-tab="analysis" href="javascript:void(0)">
        <span class="nav-icon">&#x223F;</span>Analiza
      </a>
      <a class="nav-item" data-tab="signals" id="sidebar-signals-btn" href="javascript:void(0)">
        <span class="nav-icon">&#x25CE;</span>Sygnaly
      </a>
    </nav>

    <!-- Main content -->
    <main id="main">

      <!-- Live panels -->
      <div class="tab-panel active" id="p-live-overview">{live_overview}
        <div class="disclaimer">
          &#x26A0; Symulacja paper trading &#x2014; nie porada inwestycyjna. System wirtualny, brak realnych transakcji.
          Wiarygodnosc wynikow wymaga 100&#x2013;200+ zamknietych transakcji.
        </div>
      </div>
      <div class="tab-panel" id="p-live-positions">{live_positions}</div>
      <div class="tab-panel" id="p-live-trades">{live_trades}</div>
      <div class="tab-panel" id="p-live-strategies">{live_strategies}</div>
      <div class="tab-panel" id="p-live-analysis">{live_analysis}</div>
      <div class="tab-panel" id="p-live-signals">{live_signals}</div>

      <!-- Backtest panels -->
      <div class="tab-panel" id="p-backtest-overview">{bt_overview}
        <div class="disclaimer">
          &#x26A0; Symulacja paper trading &#x2014; nie porada inwestycyjna. System wirtualny, brak realnych transakcji.
          Wiarygodnosc wynikow wymaga 100&#x2013;200+ zamknietych transakcji.
        </div>
      </div>
      <div class="tab-panel" id="p-backtest-positions">{bt_positions}</div>
      <div class="tab-panel" id="p-backtest-trades">{bt_trades}</div>
      <div class="tab-panel" id="p-backtest-strategies">{bt_strategies}</div>
      <div class="tab-panel" id="p-backtest-analysis">{bt_analysis}</div>
      <div class="tab-panel" id="p-backtest-signals"><p class="muted p16">Sygnaly dostepne tylko w trybie Live.</p></div>

    </main>

    <!-- Right panel -->
    <aside id="right-panel">
      <!-- Swing right panel -->
      <div id="rp-swing">
        <div class="panel-section">
          <h3 class="panel-title">Sygnaly oczekujace</h3>
          <div id="rp-signals-live">{live_signals_mini}</div>
          <div id="rp-signals-bt" style="display:none">{bt_signals_mini}</div>
        </div>
        <div class="panel-section">
          <h3 class="panel-title">Otwarte pozycje</h3>
          <div id="rp-positions-live">{live_positions_mini}</div>
          <div id="rp-positions-bt" style="display:none">{bt_positions_mini}</div>
        </div>
      </div>
      <!-- Intraday right panel -->
      <div id="rp-intraday" style="display:none">
        <div class="panel-section">
          <h3 class="panel-title">Sygnaly intraday</h3>
          <div id="rp-id-signals-live">{id_live_sig_mini}</div>
          <div id="rp-id-signals-bt" style="display:none"><p class='panel-empty'>Tylko tryb Live</p></div>
        </div>
        <div class="panel-section">
          <h3 class="panel-title">Pozycje intraday</h3>
          <div id="rp-id-positions-live">{id_live_pos_mini}</div>
          <div id="rp-id-positions-bt" style="display:none">{id_bt_pos_mini}</div>
        </div>
      </div>
    </aside>

  </div><!-- /body-wrap -->

</div><!-- /app -->

      <!-- Intraday Live panels -->
      <div class="tab-panel" id="p-intraday-live-overview">{id_live_overview}
        <div class="disclaimer">&#x26A0; Silnik intraday &#x2014; paper trading only. Brak realnych transakcji. Wszystkie pozycje zamykane przed 15:50 ET.</div>
      </div>
      <div class="tab-panel" id="p-intraday-live-positions">{id_live_positions}</div>
      <div class="tab-panel" id="p-intraday-live-signals">{id_live_signals}</div>
      <div class="tab-panel" id="p-intraday-live-trades">{id_live_trades}</div>
      <div class="tab-panel" id="p-intraday-live-strategies">{id_live_strategies}</div>
      <div class="tab-panel" id="p-intraday-live-analysis">{id_live_analysis}</div>

      <!-- Intraday Backtest panels -->
      <div class="tab-panel" id="p-intraday-backtest-overview">{id_bt_overview}
        <div class="disclaimer">&#x26A0; Silnik intraday &#x2014; paper trading only. Brak realnych transakcji.</div>
      </div>
      <div class="tab-panel" id="p-intraday-backtest-positions">{id_bt_positions}</div>
      <div class="tab-panel" id="p-intraday-backtest-signals"><p class="muted p16">Sygnaly dostepne tylko w trybie Live.</p></div>
      <div class="tab-panel" id="p-intraday-backtest-trades">{id_bt_trades}</div>
      <div class="tab-panel" id="p-intraday-backtest-strategies">{id_bt_strategies}</div>
      <div class="tab-panel" id="p-intraday-backtest-analysis">{id_bt_analysis}</div>

<!-- Trade detail modal -->
<div id="trade-modal" onclick="if(event.target===this)closeModal()">
  <div class="modal-box">
    <div class="modal-header">
      <div>
        <div class="modal-ticker" id="m-ticker">&#x2014;</div>
        <div id="m-strategy" style="color:var(--text3);font-size:12px;margin-top:2px"></div>
      </div>
      <button class="modal-close" onclick="closeModal()">&#x2715;</button>
    </div>
    <div class="modal-body-inner">
      <div class="modal-section">
        <div class="modal-section-title">Status</div>
        <div class="modal-grid">
          <div class="modal-field"><div class="modal-field-label">Status</div><div class="modal-field-value" id="m-status">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Tryb</div><div class="modal-field-value" id="m-mode">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Score sygnalu</div><div class="modal-field-value" id="m-score">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Dni trzymania</div><div class="modal-field-value" id="m-hold">&#x2014;</div></div>
        </div>
      </div>
      <div class="modal-section">
        <div class="modal-section-title">Wejscie</div>
        <div class="modal-grid">
          <div class="modal-field"><div class="modal-field-label">Data wejscia</div><div class="modal-field-value" id="m-entry-date">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Cena wejscia</div><div class="modal-field-value" id="m-entry-price">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Stop Loss</div><div class="modal-field-value neg" id="m-sl">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Take Profit</div><div class="modal-field-value pos" id="m-tp">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Akcje</div><div class="modal-field-value" id="m-shares">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Wartosc pozycji</div><div class="modal-field-value" id="m-pos-val">&#x2014;</div></div>
        </div>
      </div>
      <div id="m-exit-section" class="modal-section">
        <div class="modal-section-title">Wyjscie</div>
        <div class="modal-grid">
          <div class="modal-field"><div class="modal-field-label">Data wyjscia</div><div class="modal-field-value" id="m-exit-date">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Cena wyjscia</div><div class="modal-field-value" id="m-exit-price">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Powod wyjscia</div><div class="modal-field-value small" id="m-exit-reason">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Ryzyko</div><div class="modal-field-value" id="m-risk">&#x2014;</div></div>
        </div>
      </div>
      <div class="modal-section">
        <div class="modal-section-title">Wynik</div>
        <div class="modal-grid">
          <div class="modal-field"><div class="modal-field-label">P&amp;L PLN</div><div class="modal-field-value" id="m-pnl">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">P&amp;L %</div><div class="modal-field-value" id="m-pnl-pct">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">R-multiple</div><div class="modal-field-value" id="m-r">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Max hold dni</div><div class="modal-field-value" id="m-max-hold">&#x2014;</div></div>
        </div>
      </div>
      <div id="m-mgmt-section" class="modal-section" style="display:none">
        <div class="modal-section-title">Trade Management (SIMPLE_DYNAMIC_EXIT_V1)</div>
        <div class="modal-grid">
          <div class="modal-field"><div class="modal-field-label">Cena wejscia</div><div class="modal-field-value" id="m-mg-entry">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Initial stop</div><div class="modal-field-value neg" id="m-mg-init-stop">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Active stop</div><div class="modal-field-value" id="m-mg-active-stop">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Take profit</div><div class="modal-field-value pos" id="m-mg-tp">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Dystans do stopu</div><div class="modal-field-value" id="m-mg-dist-stop">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Dystans do TP</div><div class="modal-field-value" id="m-mg-dist-tp">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Najwyzszy high</div><div class="modal-field-value" id="m-mg-hh">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Najwyzszy close</div><div class="modal-field-value" id="m-mg-hc">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Max profit</div><div class="modal-field-value" id="m-mg-maxprofit">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Locked profit</div><div class="modal-field-value" id="m-mg-locked">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Status stopu</div><div class="modal-field-value" id="m-mg-status">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Exit logic</div><div class="modal-field-value small" id="m-mg-version">&#x2014;</div></div>
          <div class="modal-field"><div class="modal-field-label">Sesje</div><div class="modal-field-value" id="m-mg-sessions">&#x2014;</div></div>
        </div>
        <div style="margin-top:12px">
          <div style="position:relative;height:10px;background:#1e2d3d;border-radius:5px;margin:18px 0 6px">
            <div id="m-mg-bar-fill" style="position:absolute;top:0;bottom:0;left:0;background:linear-gradient(90deg,#ef4444,#f59e0b,#10b981);border-radius:5px;width:0%"></div>
            <div id="m-mg-bar-stop" title="Active stop" style="position:absolute;top:-4px;width:2px;height:18px;background:#ef4444;left:0%"></div>
            <div id="m-mg-bar-cur" title="Current" style="position:absolute;top:-6px;width:3px;height:22px;background:#3b82f6;left:0%"></div>
            <div id="m-mg-bar-tp" title="Take profit" style="position:absolute;top:-4px;width:2px;height:18px;background:#10b981;right:0%"></div>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text3)">
            <span>SL</span><span>Active SL</span><span>Current</span><span>TP</span>
          </div>
        </div>
        <div id="m-mg-hist-wrap" style="margin-top:14px;display:none">
          <div class="modal-field-label" style="cursor:pointer" onclick="toggleStopHist()">
            &#x25B6; Historia zmian stopu (<span id="m-mg-hist-count">0</span>)
          </div>
          <div id="m-mg-hist" style="display:none;margin-top:8px;max-height:180px;overflow:auto">
            <table style="width:100%;font-size:11px"><thead><tr>
              <th style="text-align:left">Sesja</th><th style="text-align:right">Z</th>
              <th style="text-align:right">Na</th><th style="text-align:left">Status</th>
              <th style="text-align:right">Max%</th>
            </tr></thead><tbody id="m-mg-hist-body"></tbody></table>
          </div>
        </div>
      </div>
      <div id="m-reason-section" class="modal-section" style="display:none">
        <div class="modal-section-title">Uzasadnienie sygnalu</div>
        <div class="modal-reason" id="m-reason"></div>
      </div>
    </div>
  </div>
</div>

<script>
{build_trades_js(live, bt)}

(function(){{
  var HASH="{pw_hash}",KEY="mept_v3";
  function unlock(){{
    document.getElementById("lock-screen").style.display="none";
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

var MODE="live", TAB="overview", ENGINE="swing";

// Sidebar navigation
document.addEventListener("DOMContentLoaded", function(){{
  document.querySelectorAll('.nav-item[data-tab]').forEach(function(el){{
    el.addEventListener('click', function(){{
      var tab = el.dataset.tab;
      document.querySelectorAll('.nav-item').forEach(function(x){{ x.classList.remove('active'); }});
      el.classList.add('active');
      setTabDirect(tab);
    }});
  }});
}});

function setEngine(e){{
  ENGINE=e;
  document.getElementById("btn-swing").classList.toggle("active",e==="swing");
  document.getElementById("btn-intraday").classList.toggle("active",e==="intraday");
  document.getElementById("rp-swing").style.display=(e==="swing")?"":"none";
  document.getElementById("rp-intraday").style.display=(e==="intraday")?"":"none";
  // signals nav item only in live mode
  var sigBtn=document.getElementById("sidebar-signals-btn");
  if(sigBtn) sigBtn.style.display=(MODE==="live")?"":"none";
  updatePanels();
}}

function setMode(m){{
  MODE=m;
  document.querySelectorAll(".mode-tab").forEach(function(b){{b.classList.toggle("active",b.dataset.mode===m)}});
  // swing right panel
  document.getElementById("rp-signals-live").style.display=(m==="live")?"":"none";
  document.getElementById("rp-signals-bt").style.display=(m==="backtest")?"":"none";
  document.getElementById("rp-positions-live").style.display=(m==="live")?"":"none";
  document.getElementById("rp-positions-bt").style.display=(m==="backtest")?"":"none";
  // intraday right panel
  document.getElementById("rp-id-signals-live").style.display=(m==="live")?"":"none";
  document.getElementById("rp-id-signals-bt").style.display=(m==="backtest")?"":"none";
  document.getElementById("rp-id-positions-live").style.display=(m==="live")?"":"none";
  document.getElementById("rp-id-positions-bt").style.display=(m==="backtest")?"":"none";
  // hide signals nav item in backtest
  var sigBtn=document.getElementById("sidebar-signals-btn");
  if(sigBtn) sigBtn.style.display=(m==="live")?"":"none";
  if(m==="backtest" && TAB==="signals") {{
    setTabDirect("overview");
    document.querySelectorAll('.nav-item').forEach(function(x){{ x.classList.remove('active'); }});
    var overviewItem=document.querySelector('.nav-item[data-tab="overview"]');
    if(overviewItem) overviewItem.classList.add('active');
  }} else {{
    updatePanels();
  }}
}}

function setTabDirect(t){{
  TAB=t;
  updatePanels();
}}

function updatePanels(){{
  var prefix=(ENGINE==="swing")?"p-"+MODE+"-"+TAB:"p-intraday-"+MODE+"-"+TAB;
  document.querySelectorAll(".tab-panel").forEach(function(p){{
    p.classList.toggle("active",p.id===prefix);
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
function _clsEl(el,v){{
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
  // Show ACTIVE stop in the entry section (falls back to stop_loss for legacy).
  var slShown=(t.active_stop_loss!==""&&t.active_stop_loss!=null)?t.active_stop_loss:t.stop_loss;
  document.getElementById("m-sl").textContent=slShown?parseFloat(slShown).toFixed(2)+" USD":"—";
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
  _clsEl(pnlEl,t.pnl_pln);
  var pnlPctEl=document.getElementById("m-pnl-pct");
  pnlPctEl.textContent=t.pnl_pct?_sign(t.pnl_pct,"pct"):"—";
  _clsEl(pnlPctEl,t.pnl_pct);
  var rEl=document.getElementById("m-r");
  rEl.textContent=t.r_multiple?(parseFloat(t.r_multiple)>=0?"+":"")+parseFloat(t.r_multiple).toFixed(2)+"R":"—";
  _clsEl(rEl,t.r_multiple);
  // Trade Management (SIMPLE_DYNAMIC_EXIT_V1 trades only)
  var mgSec=document.getElementById("m-mgmt-section");
  var ver=t.exit_logic_version||"";
  if(ver==="SIMPLE_DYNAMIC_EXIT_V1"){{
    mgSec.style.display="";
    var entry=parseFloat(t.entry_price)||0;
    var initStop=parseFloat(t.initial_stop_loss)||0;
    var actStop=parseFloat(t.active_stop_loss)||initStop;
    var tp=parseFloat(t.take_profit)||0;
    var cur=t.status==="closed"?(parseFloat(t.exit_price)||entry):(parseFloat(t.current_price)||entry);
    var hh=parseFloat(t.highest_high_since_entry)||entry;
    var hc=parseFloat(t.highest_close_since_entry)||entry;
    document.getElementById("m-mg-entry").textContent=entry.toFixed(2)+" USD";
    document.getElementById("m-mg-init-stop").textContent=initStop?initStop.toFixed(2)+" USD":"—";
    document.getElementById("m-mg-active-stop").textContent=actStop?actStop.toFixed(2)+" USD":"—";
    document.getElementById("m-mg-tp").textContent=tp?tp.toFixed(2)+" USD":"—";
    document.getElementById("m-mg-dist-stop").textContent=(entry&&actStop)?(((entry-actStop)/entry*100).toFixed(2)+"%"):"—";
    document.getElementById("m-mg-dist-tp").textContent=(entry&&tp)?(((tp-entry)/entry*100).toFixed(2)+"%"):"—";
    document.getElementById("m-mg-hh").textContent=hh.toFixed(2)+" USD";
    document.getElementById("m-mg-hc").textContent=hc.toFixed(2)+" USD";
    document.getElementById("m-mg-maxprofit").textContent=((parseFloat(t.max_profit_pct)||0)*100).toFixed(2)+"%";
    document.getElementById("m-mg-locked").textContent=((parseFloat(t.locked_profit_pct)||0)*100).toFixed(2)+"%";
    document.getElementById("m-mg-status").textContent=(t.stop_status||"INITIAL").replace(/_/g," ");
    document.getElementById("m-mg-version").textContent=ver;
    document.getElementById("m-mg-sessions").textContent=t.holding_days?t.holding_days:"—";
    // progress bar: map SL..TP to 0..100%
    var lo=Math.min(actStop,initStop,entry),hi=Math.max(tp,cur,entry);
    var span=(hi-lo)||1;
    function pos(v){{return Math.max(0,Math.min(100,(v-lo)/span*100));}}
    document.getElementById("m-mg-bar-stop").style.left=pos(actStop)+"%";
    document.getElementById("m-mg-bar-cur").style.left=pos(cur)+"%";
    document.getElementById("m-mg-bar-tp").style.left=pos(tp)+"%";
    document.getElementById("m-mg-bar-tp").style.right="auto";
    document.getElementById("m-mg-bar-fill").style.width=pos(cur)+"%";
    // stop history
    var hist=(typeof STOP_HISTORY!=="undefined")?(STOP_HISTORY[String(id)]||[]):[];
    var hw=document.getElementById("m-mg-hist-wrap");
    if(hist.length){{
      hw.style.display="";
      document.getElementById("m-mg-hist-count").textContent=hist.length;
      var body=document.getElementById("m-mg-hist-body");
      body.innerHTML=hist.map(function(h){{
        return "<tr><td>"+h.session_date+"</td><td style='text-align:right'>"+parseFloat(h.previous_stop).toFixed(2)+
          "</td><td style='text-align:right'>"+parseFloat(h.new_stop).toFixed(2)+
          "</td><td>"+(h.stop_status||"").replace(/_/g," ")+"</td><td style='text-align:right'>"+
          ((parseFloat(h.max_profit_pct)||0)*100).toFixed(1)+"%</td></tr>";
      }}).join("");
    }}else{{hw.style.display="none";}}
  }}else{{mgSec.style.display="none";}}
  // reason
  var rSec=document.getElementById("m-reason-section");
  if(t.entry_reason){{
    rSec.style.display="";
    document.getElementById("m-reason").textContent=t.entry_reason;
  }}else{{rSec.style.display="none";}}
  document.getElementById("trade-modal").classList.add("open");
  document.body.style.overflow="hidden";
}}

function toggleStopHist(){{
  var el=document.getElementById("m-mg-hist");
  el.style.display=el.style.display==="none"?"":"none";
}}

function closeModal(){{
  document.getElementById("trade-modal").classList.remove("open");
  document.body.style.overflow="";
}}

document.addEventListener("keydown",function(e){{if(e.key==="Escape")closeModal();}});

window.addEventListener("DOMContentLoaded",function(){{
  setMode("live");
  updatePanels();
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

    # Intraday data (optional — fails gracefully if tables don't exist)
    intraday = None
    try:
        import generate_intraday_dashboard as _id
        id_live = _id.gather("live")
        id_bt   = _id.gather("backtest")
        intraday = {
            "live":   id_live,
            "live_m": _id.compute_intraday_metrics(id_live),
            "bt":     id_bt,
            "bt_m":   _id.compute_intraday_metrics(id_bt),
        }
        print("  Intraday data gathered OK")
    except Exception as e:
        print(f"  [warn] Intraday data unavailable: {e}")

    updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    html = build_html(live, live_m, bt, bt_m, benchmark=benchmark, updated=updated, intraday=intraday)

    out_path = os.path.join(DOCS_DIR, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    open(os.path.join(DOCS_DIR, ".nojekyll"), "w").close()
    print(f"Dashboard written -> {out_path}  ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
