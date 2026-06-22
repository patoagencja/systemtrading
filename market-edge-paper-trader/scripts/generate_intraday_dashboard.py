"""Generate intraday dashboard → docs/intraday_dashboard.html.

Dark-themed trading terminal for the intraday paper trading engine.
Password-protected via crypto.subtle SHA-256.
Reads from intraday_* tables in SQLite. Shows placeholder when no data.
"""
import os
import sys
import json
import hashlib
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))

DOCS_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "docs"))
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "trading2025")

# ── helpers ──────────────────────────────────────────────────────────────────

def _fmt(x, dec=0):
    if x is None:
        return "—"
    try:
        return f"{float(x):,.{dec}f}".replace(",", " ")
    except Exception:
        return str(x)

def _sgn(v, unit="PLN", dec=0):
    if v is None:
        return "—"
    try:
        v = float(v)
    except Exception:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{_fmt(v, dec)} {unit}"

def _pct(v, dec=1):
    if v is None:
        return "—"
    try:
        v = float(v)
    except Exception:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{dec}f}%"

def _cls(v):
    if v is None:
        return ""
    try:
        v = float(v)
    except Exception:
        return ""
    if v == 0:
        return ""
    return "pos" if v > 0 else "neg"

def _safe_float(v, default=0.0):
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default

# ── data gathering ────────────────────────────────────────────────────────────

def gather(mode: str) -> dict:
    d = {"mode": mode, "open_positions": [], "signals": [], "trades": [],
         "by_strategy": [], "snapshots": [], "runs": []}
    try:
        from app.database import db_cursor
        with db_cursor() as cur:
            # Check tables exist
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='intraday_trades'")
            if not cur.fetchone():
                return d

            # Open positions (trades without exit)
            cur.execute(
                """SELECT * FROM intraday_trades
                   WHERE run_mode=? AND exit_timestamp IS NULL
                   ORDER BY entry_timestamp DESC""",
                (mode,)
            )
            d["open_positions"] = [dict(r) for r in cur.fetchall()]

            # All trades (closed)
            cur.execute(
                """SELECT * FROM intraday_trades
                   WHERE run_mode=? AND exit_timestamp IS NOT NULL
                   ORDER BY exit_timestamp DESC LIMIT 500""",
                (mode,)
            )
            d["trades"] = [dict(r) for r in cur.fetchall()]

            # Signals
            cur.execute(
                """SELECT * FROM intraday_signals
                   WHERE run_mode=?
                   ORDER BY signal_timestamp DESC LIMIT 200""",
                (mode,)
            )
            d["signals"] = [dict(r) for r in cur.fetchall()]

            # Per-strategy stats
            cur.execute(
                """SELECT strategy,
                          COUNT(*) n,
                          SUM(CASE WHEN net_pnl_pln > 0 THEN 1 ELSE 0 END) wins,
                          SUM(net_pnl_pln) total_pnl,
                          AVG(net_pnl_pln) avg_pnl,
                          AVG(holding_minutes) avg_hold_min,
                          100.0 * SUM(CASE WHEN net_pnl_pln > 0 THEN 1 ELSE 0 END) / COUNT(*) win_rate
                   FROM intraday_trades
                   WHERE run_mode=? AND exit_timestamp IS NOT NULL
                   GROUP BY strategy ORDER BY total_pnl DESC""",
                (mode,)
            )
            d["by_strategy"] = [dict(r) for r in cur.fetchall()]

            # Snapshots for equity curve
            cur.execute(
                """SELECT * FROM intraday_snapshots WHERE run_mode=?
                   ORDER BY timestamp""",
                (mode,)
            )
            d["snapshots"] = [dict(r) for r in cur.fetchall()]

            # Latest run info
            cur.execute(
                """SELECT * FROM intraday_runs WHERE run_mode=?
                   ORDER BY started_at DESC LIMIT 1""",
                (mode,)
            )
            row = cur.fetchone()
            d["last_run"] = dict(row) if row else None

    except Exception as e:
        print(f"  [warn] gather({mode}) failed: {e}")
    return d


def compute_intraday_metrics(d: dict) -> dict:
    """Compute aggregate metrics from gathered data."""
    trades = d["trades"]
    m = {
        "total_trades": len(trades),
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "total_pnl": 0.0,
        "gross_pnl": 0.0,
        "net_pnl": 0.0,
        "total_commission": 0.0,
        "total_slippage": 0.0,
        "total_costs": 0.0,
        "avg_hold_min": 0.0,
        "avg_pnl": 0.0,
        "max_win": 0.0,
        "max_loss": 0.0,
        "avg_r": 0.0,
        "today_pnl": 0.0,
        "today_trades": 0,
        "monthly_pnl": {},
        "hourly_pnl": {},
        "exit_reasons": {},
    }
    if not trades:
        return m

    today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    wins = [t for t in trades if _safe_float(t.get("net_pnl_pln")) > 0]
    losses = [t for t in trades if _safe_float(t.get("net_pnl_pln")) < 0]

    gross_wins = sum(_safe_float(t.get("gross_pnl_pln")) for t in wins)
    gross_losses = abs(sum(_safe_float(t.get("gross_pnl_pln")) for t in losses))
    pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    net_pnl = sum(_safe_float(t.get("net_pnl_pln")) for t in trades)
    gross_pnl = sum(_safe_float(t.get("gross_pnl_pln")) for t in trades)
    total_commission = sum(_safe_float(t.get("commission_pln")) for t in trades)
    total_slippage = sum(_safe_float(t.get("slippage_cost_pln")) for t in trades)
    total_costs = total_commission + total_slippage

    # Today
    today_trades = [t for t in trades if (t.get("exit_timestamp") or "")[:10] == today_str]
    today_pnl = sum(_safe_float(t.get("net_pnl_pln")) for t in today_trades)

    avg_hold = sum(_safe_float(t.get("holding_minutes")) for t in trades) / len(trades)
    r_vals = [_safe_float(t.get("r_multiple")) for t in trades if t.get("r_multiple") is not None]
    avg_r = sum(r_vals) / len(r_vals) if r_vals else 0.0

    # Monthly P&L
    monthly: dict = {}
    for t in trades:
        exit_ts = t.get("exit_timestamp") or ""
        if len(exit_ts) >= 7:
            key = exit_ts[:7]  # YYYY-MM
            monthly[key] = monthly.get(key, 0.0) + _safe_float(t.get("net_pnl_pln"))

    # Hourly P&L (entry hour ET)
    hourly: dict = {}
    for t in trades:
        entry_ts = t.get("entry_timestamp") or ""
        if len(entry_ts) >= 13:
            try:
                hour = int(entry_ts[11:13])
                # Convert from UTC to ET roughly (-4 or -5 hours)
                # Just use the hour as stored
                hourly.setdefault(hour, []).append(_safe_float(t.get("net_pnl_pln")))
            except Exception:
                pass
    hourly_avg = {h: sum(v) / len(v) for h, v in hourly.items()}

    # Exit reasons
    exit_reasons: dict = {}
    for t in trades:
        r = t.get("exit_reason") or "unknown"
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    m.update({
        "total_trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0.0,
        "profit_factor": pf,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "total_pnl": net_pnl,
        "total_commission": total_commission,
        "total_slippage": total_slippage,
        "total_costs": total_costs,
        "avg_hold_min": avg_hold,
        "avg_pnl": net_pnl / len(trades),
        "max_win": max((_safe_float(t.get("net_pnl_pln")) for t in trades), default=0.0),
        "max_loss": min((_safe_float(t.get("net_pnl_pln")) for t in trades), default=0.0),
        "avg_r": avg_r,
        "today_pnl": today_pnl,
        "today_trades": len(today_trades),
        "monthly_pnl": monthly,
        "hourly_pnl": hourly_avg,
        "exit_reasons": exit_reasons,
    })
    return m


# ── panel renderers ───────────────────────────────────────────────────────────

def render_overview_panel(d: dict, m: dict) -> str:
    snaps = d["snapshots"]
    last_snap = snaps[-1] if snaps else {}
    equity = _safe_float(last_snap.get("equity_pln"), 1_000_000)
    cash = _safe_float(last_snap.get("cash_pln"), equity)
    invested = _safe_float(last_snap.get("invested_pln"), 0)
    unrealized = _safe_float(last_snap.get("unrealized_pnl"), 0)  # column name in DB

    open_count = len(d["open_positions"])
    today_pnl = m["today_pnl"]
    total_pnl = m["total_pnl"]
    win_rate = m["win_rate"]
    pf = m["profit_factor"]
    pf_str = f"{pf:.2f}" if pf < 999 else "∞"

    exposure_pct = invested / equity * 100 if equity > 0 else 0.0

    if not snaps and not d["trades"]:
        no_data = """<div class="empty-state">
          <div class="empty-icon">📊</div>
          <div class="empty-title">Brak danych intraday</div>
          <div class="empty-sub">Uruchom backtest intraday lub poczekaj na pierwszy skan live.</div>
        </div>"""
        return no_data

    # Session info
    try:
        from app.intraday.session_manager import SessionManager
        import datetime
        from zoneinfo import ZoneInfo
        NY_TZ = ZoneInfo("America/New_York")
        now_et = datetime.datetime.now(tz=NY_TZ)
        market_status = SessionManager.get_market_status(now_et)
        et_str = now_et.strftime("%H:%M ET")
        status_color = {
            "OPEN": "var(--green)",
            "CLOSING_WINDOW": "var(--yellow)",
            "PRE-MARKET": "var(--muted)",
            "CLOSED": "var(--muted)",
        }.get(market_status, "var(--muted)")
    except Exception:
        market_status = "UNKNOWN"
        et_str = "—"
        status_color = "var(--muted)"

    last_run = d.get("last_run")
    run_status = "ACTIVE"
    if last_run:
        run_status = last_run.get("status", "ACTIVE").upper()

    # Build equity chart if we have snapshots
    equity_chart_html = ""
    if len(snaps) >= 2:
        try:
            import plotly.graph_objects as go
            xs = [s.get("timestamp", "")[:10] for s in snaps]
            ys = [_safe_float(s.get("equity_pln"), 0) for s in snaps]
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", name="Equity",
                line=dict(color="#388bfd", width=2),
                hovertemplate="%{x}<br>%{y:,.0f} PLN<extra></extra>",
            ))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#8b949e", size=11),
                xaxis=dict(gridcolor="#21262d", linecolor="#30363d"),
                yaxis=dict(gridcolor="#21262d", linecolor="#30363d"),
                margin=dict(l=50, r=16, t=36, b=28),
                height=250,
                title=dict(text="Krzywa kapitału (intraday)", font=dict(size=13, color="#8b949e")),
                hoverlabel=dict(bgcolor="#161b22", bordercolor="#30363d", font_color="#e6edf3"),
            )
            equity_chart_html = f"""<div class="chart-card" style="margin-top:16px">
{fig.to_html(full_html=False, include_plotlyjs=False, config={{"displayModeBar": False}})}
</div>"""
        except Exception as e:
            equity_chart_html = f"<p class='muted p16'>Wykres niedostępny: {e}</p>"

    return f"""
<div class="kpi-grid">
  <div class="kpi">
    <div class="kpi-label">Kapitał</div>
    <div class="kpi-value">{_fmt(equity)} <span class="kpi-unit">PLN</span></div>
  </div>
  <div class="kpi">
    <div class="kpi-label">P&amp;L dzisiaj</div>
    <div class="kpi-value {_cls(today_pnl)}">{_sgn(today_pnl)}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">P&amp;L łączny (net)</div>
    <div class="kpi-value {_cls(total_pnl)}">{_sgn(total_pnl)}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Niezrealizowany</div>
    <div class="kpi-value {_cls(unrealized)}">{_sgn(unrealized)}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Gotówka</div>
    <div class="kpi-value">{_fmt(cash)} <span class="kpi-unit">PLN</span></div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Otwarte pozycje</div>
    <div class="kpi-value">{open_count}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Win rate</div>
    <div class="kpi-value">{win_rate:.1f}%</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Profit factor</div>
    <div class="kpi-value">{pf_str}</div>
  </div>
</div>

<div class="grid2" style="margin-top:16px">
  <div class="metric-card">
    <div class="metric-label">Status rynku</div>
    <div class="metric-value" style="color:{status_color}">{market_status}</div>
    <div class="metric-sub">{et_str}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Status systemu</div>
    <div class="metric-value">{run_status}</div>
    <div class="metric-sub">Tylko Long · Bez overnight</div>
  </div>
  <div class="metric-card">
    <div class="kpi-label">Ekspozycja</div>
    <div class="metric-value">{exposure_pct:.1f}%</div>
    <div class="metric-sub">kapitału zainwestowanego</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Avg holding</div>
    <div class="metric-value">{m['avg_hold_min']:.0f}</div>
    <div class="metric-sub">minut / transakcję</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Transakcje dzisiaj</div>
    <div class="metric-value">{m['today_trades']}</div>
    <div class="metric-sub">zamkniętych</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Avg R-multiple</div>
    <div class="metric-value {_cls(m['avg_r'])}">{m['avg_r']:+.2f}R</div>
    <div class="metric-sub">zysk / ryzyko</div>
  </div>
</div>

<div class="info-badge" style="margin-top:16px">
  ⛔ BEZ POZYCJI OVERNIGHT — wszystkie zamykane przed 15:50 ET
</div>

{equity_chart_html}
"""


def render_positions_panel(d: dict) -> str:
    positions = d["open_positions"]
    if not positions:
        return """<div class="empty-state">
          <div class="empty-icon">📭</div>
          <div class="empty-title">Brak otwartych pozycji</div>
          <div class="empty-sub">Silnik intraday nie ma aktywnych pozycji.</div>
        </div>"""

    rows = []
    for p in positions:
        entry_price = _safe_float(p.get("entry_price"))
        current = entry_price
        unreal_pnl = 0.0  # unrealized P&L not stored until exit
        hold_min = _safe_float(p.get("holding_minutes"), 0)
        stop = _safe_float(p.get("stop_price"))
        target = _safe_float(p.get("target_price"))
        entry_ts = (p.get("entry_timestamp") or "")[:16]
        rows.append(
            f"<tr>"
            f"<td><b>{p.get('ticker','')}</b></td>"
            f"<td class='mono small'>{(p.get('strategy') or '').replace('_', ' ')}</td>"
            f"<td class='mono'>{entry_ts}</td>"
            f"<td class='mono'>{entry_price:.2f}</td>"
            f"<td class='mono'>{stop:.2f}</td>"
            f"<td class='mono'>{target:.2f}</td>"
            f"<td class='mono {_cls(unreal_pnl)}'>{_sgn(unreal_pnl)}</td>"
            f"<td class='mono'>{hold_min:.0f}</td>"
            f"</tr>"
        )

    return f"""<div class="table-scroll">
<table>
<thead><tr>
  <th>Ticker</th><th>Strategia</th><th>Wejście</th><th>Cena wej.</th>
  <th>Stop</th><th>Target</th><th>Unreal. P&amp;L</th><th>Min.</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>"""


def render_signals_panel(d: dict) -> str:
    signals = d["signals"]
    if not signals:
        return """<div class="empty-state">
          <div class="empty-icon">📡</div>
          <div class="empty-title">Brak sygnałów</div>
          <div class="empty-sub">Sygnały pojawiają się podczas skanowania rynku co 30 min.</div>
        </div>"""

    rows = []
    for s in signals[:100]:
        status = s.get("status") or "pending"
        badge_cls = {
            "filled": "badge-open",
            "pending": "badge-pending",
            "rejected": "badge-rejected",
            "cancelled": "badge-cancelled",
        }.get(status, "badge-cancelled")
        score = _safe_float(s.get("score"))
        rejection = (s.get("rejection_reason") or "")[:70]
        entry_ts = (s.get("signal_timestamp") or "")[:16]
        rows.append(
            f"<tr>"
            f"<td><span class='badge {badge_cls}'>{status.upper()}</span></td>"
            f"<td><b>{s.get('ticker','')}</b></td>"
            f"<td class='mono small'>{(s.get('strategy') or '').replace('_', ' ')}</td>"
            f"<td class='mono'>{entry_ts}</td>"
            f"<td class='mono'>{score:.0f}</td>"
            f"<td class='mono'>{_safe_float(s.get('planned_entry')):.2f}</td>"
            f"<td class='mono'>{_safe_float(s.get('stop_price')):.2f}</td>"
            f"<td class='mono small'>{rejection}</td>"
            f"</tr>"
        )

    return f"""<div class="table-scroll">
<table>
<thead><tr>
  <th>Status</th><th>Ticker</th><th>Strategia</th><th>Czas</th><th>Score</th>
  <th>Entry</th><th>Stop</th><th>Powód odrzucenia</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>"""


def render_trades_panel(d: dict) -> str:
    trades = d["trades"]
    if not trades:
        return "<p class='muted p16'>Brak zamkniętych transakcji.</p>"

    rows = []
    for t in trades[:200]:
        net_pnl = _safe_float(t.get("net_pnl_pln"))
        gross_pnl = _safe_float(t.get("gross_pnl_pln"))
        costs = _safe_float(t.get("commission_pln")) + _safe_float(t.get("slippage_cost_pln"))
        r = t.get("r_multiple")
        r_str = f"{_safe_float(r):+.2f}R" if r is not None else "—"
        entry_ts = (t.get("entry_timestamp") or "")[:16]
        exit_ts = (t.get("exit_timestamp") or "")[:16]
        hold_min = _safe_float(t.get("holding_minutes"), 0)
        exit_reason = (t.get("exit_reason") or "—")[:30]
        rows.append(
            f"<tr>"
            f"<td><b>{t.get('ticker','')}</b></td>"
            f"<td class='mono small'>{(t.get('strategy') or '').replace('_', ' ')}</td>"
            f"<td class='mono'>{entry_ts}</td>"
            f"<td class='mono'>{exit_ts}</td>"
            f"<td class='mono'>{hold_min:.0f}</td>"
            f"<td class='mono {_cls(gross_pnl)}'>{_sgn(gross_pnl)}</td>"
            f"<td class='mono neg'>-{_fmt(costs)}</td>"
            f"<td class='mono {_cls(net_pnl)}'>{_sgn(net_pnl)}</td>"
            f"<td class='mono'>{r_str}</td>"
            f"<td class='mono small'>{exit_reason}</td>"
            f"</tr>"
        )

    return f"""<div class="table-scroll">
<table>
<thead><tr>
  <th>Ticker</th><th>Strategia</th><th>Wejście</th><th>Wyjście</th><th>Min.</th>
  <th>Gross P&amp;L</th><th>Koszty</th><th>Net P&amp;L</th><th>R</th><th>Powód wyjścia</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>"""


def render_strategies_panel(d: dict, m: dict) -> str:
    strats = d["by_strategy"]
    if not strats:
        return "<p class='muted p16'>Brak danych o strategiach.</p>"

    rows = []
    for s in strats:
        n = int(s.get("n") or 0)
        wins = int(s.get("wins") or 0)
        total_pnl = _safe_float(s.get("total_pnl"))
        avg_pnl = _safe_float(s.get("avg_pnl"))
        wr = _safe_float(s.get("win_rate"))
        avg_hold = _safe_float(s.get("avg_hold_min"))
        losses = n - wins
        rows.append(
            f"<tr>"
            f"<td><b>{(s.get('strategy') or '').replace('_', ' ')}</b></td>"
            f"<td class='mono'>{n}</td>"
            f"<td class='mono'>{wins} / {losses}</td>"
            f"<td class='mono'>{wr:.1f}%</td>"
            f"<td class='mono {_cls(total_pnl)}'>{_sgn(total_pnl)}</td>"
            f"<td class='mono {_cls(avg_pnl)}'>{_sgn(avg_pnl)}</td>"
            f"<td class='mono'>{avg_hold:.0f}</td>"
            f"</tr>"
        )

    # Strategy PnL bar chart
    bar_chart = ""
    try:
        import plotly.graph_objects as go
        names = [(s.get("strategy") or "").replace("_", " ") for s in strats]
        pnls = [_safe_float(s.get("total_pnl")) for s in strats]
        colors = ["#2ea043" if p >= 0 else "#f85149" for p in pnls]
        fig = go.Figure(go.Bar(
            x=pnls, y=names, orientation="h",
            marker_color=colors,
            hovertemplate="%{y}<br>%{x:,.0f} PLN<extra></extra>",
        ))
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#8b949e", size=11),
            xaxis=dict(gridcolor="#21262d", linecolor="#30363d"),
            yaxis=dict(gridcolor="#21262d", linecolor="#30363d"),
            margin=dict(l=50, r=16, t=36, b=28),
            height=max(180, 50 * len(strats)),
            title=dict(text="P&L wg strategii", font=dict(size=13, color="#8b949e")),
            showlegend=False,
            hoverlabel=dict(bgcolor="#161b22", bordercolor="#30363d", font_color="#e6edf3"),
        )
        bar_chart = f"""<div class="chart-card" style="margin-top:16px">
{fig.to_html(full_html=False, include_plotlyjs=False, config={{"displayModeBar": False}})}
</div>"""
    except Exception:
        pass

    return f"""<div class="table-scroll">
<table>
<thead><tr>
  <th>Strategia</th><th>Transakcje</th><th>W/L</th><th>Win rate</th>
  <th>Total P&amp;L</th><th>Avg P&amp;L</th><th>Avg min.</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>
{bar_chart}"""


def render_analysis_panel(d: dict, m: dict) -> str:
    # Hourly heatmap
    hourly = m.get("hourly_pnl", {})
    hourly_rows = ""
    if hourly:
        for hour in sorted(hourly.keys()):
            avg = hourly[hour]
            cls = "pos" if avg >= 0 else "neg"
            sign = "+" if avg >= 0 else ""
            hourly_rows += (
                f"<tr><td class='mono'>{hour:02d}:xx ET</td>"
                f"<td class='mono {cls}'>{sign}{avg:.0f} PLN</td></tr>"
            )
    hourly_html = f"""<div class="table-scroll">
<table>
<thead><tr><th>Godzina (ET)</th><th>Avg P&amp;L</th></tr></thead>
<tbody>{hourly_rows if hourly_rows else "<tr><td colspan=2 class='muted'>Brak danych</td></tr>"}</tbody>
</table></div>"""

    # Cost analysis
    gross = m.get("gross_pnl", 0.0)
    commission = m.get("total_commission", 0.0)
    slippage = m.get("total_slippage", 0.0)
    total_costs = commission + slippage
    net = m.get("net_pnl", 0.0)

    cost_html = f"""<div class="stat-list">
  <div class="stat-row"><span>Gross P&amp;L</span><span class='mono {_cls(gross)}'>{_sgn(gross)}</span></div>
  <div class="stat-row"><span>Prowizja</span><span class='mono neg'>-{_fmt(commission)}</span></div>
  <div class="stat-row"><span>Slippage</span><span class='mono neg'>-{_fmt(slippage)}</span></div>
  <div class="stat-row"><span>Łączne koszty</span><span class='mono neg'>-{_fmt(total_costs)}</span></div>
  <div class="stat-row"><span style="font-weight:700">Net P&amp;L</span><span class='mono {_cls(net)}' style="font-weight:700">{_sgn(net)}</span></div>
</div>"""

    # Monthly P&L
    monthly = m.get("monthly_pnl", {})
    monthly_rows = ""
    for key in sorted(monthly.keys(), reverse=True)[:24]:
        v = monthly[key]
        cls = "pos" if v >= 0 else "neg"
        sign = "+" if v >= 0 else ""
        monthly_rows += (
            f"<tr><td class='mono'>{key}</td>"
            f"<td class='mono {cls}'>{sign}{v:.0f} PLN</td></tr>"
        )
    monthly_html = f"""<div class="table-scroll">
<table>
<thead><tr><th>Miesiąc</th><th>P&amp;L</th></tr></thead>
<tbody>{monthly_rows if monthly_rows else "<tr><td colspan=2 class='muted'>Brak danych</td></tr>"}</tbody>
</table></div>"""

    # Exit reasons
    exit_reasons = m.get("exit_reasons", {})
    reasons_html = ""
    if exit_reasons:
        for reason, cnt in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            reasons_html += (
                f"<div class='stat-row'><span>{reason}</span><span class='mono'>{cnt}</span></div>"
            )
    else:
        reasons_html = "<p class='muted'>Brak danych</p>"

    return f"""<div class="grid2">
  <div>
    <h3 class="section-title">Godzinowy P&amp;L (ET)</h3>
    {hourly_html}
  </div>
  <div>
    <h3 class="section-title">Analiza kosztów</h3>
    {cost_html}
  </div>
</div>

<div class="grid2" style="margin-top:20px">
  <div>
    <h3 class="section-title">Miesięczny P&amp;L</h3>
    {monthly_html}
  </div>
  <div>
    <h3 class="section-title">Powody wyjścia</h3>
    <div class="stat-list">{reasons_html}</div>
  </div>
</div>"""


# ── HTML assembly ─────────────────────────────────────────────────────────────

def build_html(live: dict, live_m: dict, bt: dict, bt_m: dict, updated: str = "") -> str:
    pw_hash = hashlib.sha256(DASHBOARD_PASSWORD.encode()).hexdigest()
    if not updated:
        updated = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    live_overview = render_overview_panel(live, live_m)
    live_positions = render_positions_panel(live)
    live_signals = render_signals_panel(live)
    live_trades = render_trades_panel(live)
    live_strategies = render_strategies_panel(live, live_m)
    live_analysis = render_analysis_panel(live, live_m)

    bt_overview = render_overview_panel(bt, bt_m)
    bt_positions = render_positions_panel(bt)
    bt_trades = render_trades_panel(bt)
    bt_strategies = render_strategies_panel(bt, bt_m)
    bt_analysis = render_analysis_panel(bt, bt_m)

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Edge — Intraday Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
:root{{
  --bg:#0d1117;
  --surface:#161b22;
  --surface2:#1c2128;
  --border:#21262d;
  --border2:#30363d;
  --text:#e6edf3;
  --muted:#7d8590;
  --accent:#388bfd;
  --green:#2ea043;
  --red:#f85149;
  --yellow:#d29922;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
html{{height:100%}}
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--text);font-size:13px;line-height:1.5;height:100%}}
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
#app{{display:none;height:100%;flex-direction:column}}
.header{{background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px;padding:0 20px;height:52px;flex-shrink:0}}
.brand{{display:flex;align-items:center;gap:8px;flex:1}}
.brand-icon{{color:var(--accent);font-size:18px;font-weight:700}}
.brand-name{{font-size:15px;font-weight:700;color:var(--text)}}
.brand-sep{{color:var(--border2)}}
.brand-sub{{color:var(--muted);font-size:12px}}
.brand-tag{{background:rgba(56,139,253,.15);color:var(--accent);border:1px solid rgba(56,139,253,.3);border-radius:4px;padding:1px 8px;font-size:11px;font-weight:700}}
.mode-toggle{{display:flex;gap:4px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:3px}}
.mode-btn{{padding:4px 14px;border:none;border-radius:6px;background:transparent;color:var(--muted);font-size:12px;font-weight:600;cursor:pointer;transition:.15s}}
.mode-btn.active{{background:var(--accent);color:#fff}}
.header-meta{{color:var(--muted);font-size:11px;white-space:nowrap}}
.swing-link{{color:var(--muted);font-size:11px;text-decoration:none;border:1px solid var(--border);border-radius:6px;padding:3px 10px}}
.swing-link:hover{{color:var(--text);border-color:var(--border2)}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--green);display:inline-block;margin-right:5px;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.nav{{background:var(--surface);border-bottom:1px solid var(--border);display:flex;gap:2px;padding:0 16px;flex-shrink:0}}
.nav-tab{{padding:12px 16px;border:none;background:none;color:var(--muted);font-size:13px;font-weight:500;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;transition:.15s;white-space:nowrap}}
.nav-tab.active{{color:var(--text);border-bottom-color:var(--accent)}}
.nav-tab:hover:not(.active){{color:var(--text)}}
.content{{flex:1;overflow-y:auto;padding:20px}}
.panel{{display:none;max-width:1200px;margin:0 auto}}
.panel.visible{{display:block}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:16px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px}}
.kpi-label{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px}}
.kpi-value{{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}}
.kpi-unit{{font-size:12px;color:var(--muted);font-weight:400}}
.metrics-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px;margin-top:16px}}
.metric-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px}}
.metric-label{{font-size:11px;color:var(--muted);margin-bottom:4px}}
.metric-value{{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}}
.metric-sub{{font-size:10px;color:var(--muted);margin-top:2px}}
.chart-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 8px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:680px){{.grid2{{grid-template-columns:1fr}}}}
.table-scroll{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border2);color:var(--muted);font-weight:600;white-space:nowrap;font-size:11px}}
td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}}
tr:hover td{{background:var(--surface2)}}
.mono{{font-variant-numeric:tabular-nums;font-size:12px}}
.small{{font-size:11px;color:var(--muted)}}
.badge{{display:inline-block;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:700}}
.badge-open{{background:rgba(46,160,67,.15);color:var(--green);border:1px solid rgba(46,160,67,.3)}}
.badge-pending{{background:rgba(56,139,253,.15);color:var(--accent);border:1px solid rgba(56,139,253,.3)}}
.badge-rejected{{background:rgba(248,81,73,.15);color:var(--red);border:1px solid rgba(248,81,73,.3)}}
.badge-cancelled{{background:rgba(139,148,158,.12);color:var(--muted);border:1px solid rgba(139,148,158,.2)}}
.pos{{color:var(--green)}}
.neg{{color:var(--red)}}
.muted{{color:var(--muted)}}
.stat-list{{display:flex;flex-direction:column;gap:6px}}
.stat-row{{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;background:var(--surface2);border-radius:6px;font-size:12px}}
.stat-row span:first-child{{color:var(--muted)}}
.section-title{{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}}
.empty-state{{text-align:center;padding:60px 20px;color:var(--muted)}}
.empty-icon{{font-size:40px;margin-bottom:12px}}
.empty-title{{font-size:16px;font-weight:600;color:var(--text);margin-bottom:6px}}
.empty-sub{{font-size:13px}}
.p16{{padding:16px 0}}
.info-badge{{background:rgba(248,81,73,.08);border:1px solid rgba(248,81,73,.2);border-radius:8px;padding:8px 14px;font-size:12px;color:var(--red);font-weight:600;text-align:center}}
.disclaimer{{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:10px 14px;font-size:11px;color:var(--muted);margin-top:20px}}
</style>
</head>
<body>
<div id="lock">
  <div class="lock-box">
    <div class="lock-logo">▲</div>
    <div class="lock-title">Market Edge</div>
    <div class="lock-sub">Intraday Dashboard — wpisz hasło</div>
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
      <span class="brand-sub">Intraday Trader</span>
      <span class="brand-tag">INTRADAY</span>
    </div>
    <div class="mode-toggle">
      <button class="mode-btn active" data-mode="live" onclick="setMode('live')">Live</button>
      <button class="mode-btn" data-mode="backtest" onclick="setMode('backtest')">Backtest</button>
    </div>
    <a href="index.html" class="swing-link">Swing →</a>
    <div class="header-meta">
      <span class="dot"></span>{updated}
    </div>
  </div>

  <div class="nav" id="nav">
    <button class="nav-tab active" data-tab="overview" onclick="setTab('overview')">Przegląd</button>
    <button class="nav-tab" data-tab="positions" onclick="setTab('positions')">Pozycje</button>
    <button class="nav-tab" data-tab="signals" onclick="setTab('signals')" id="tab-signals-btn">Sygnały</button>
    <button class="nav-tab" data-tab="trades" onclick="setTab('trades')">Transakcje</button>
    <button class="nav-tab" data-tab="strategies" onclick="setTab('strategies')">Strategie</button>
    <button class="nav-tab" data-tab="analysis" onclick="setTab('analysis')">Analiza</button>
  </div>

  <div class="content">
    <div class="panel" id="p-live-overview">{live_overview}</div>
    <div class="panel" id="p-live-positions">{live_positions}</div>
    <div class="panel" id="p-live-signals">{live_signals}</div>
    <div class="panel" id="p-live-trades">{live_trades}</div>
    <div class="panel" id="p-live-strategies">{live_strategies}</div>
    <div class="panel" id="p-live-analysis">{live_analysis}</div>

    <div class="panel" id="p-backtest-overview">{bt_overview}</div>
    <div class="panel" id="p-backtest-positions">{bt_positions}</div>
    <div class="panel" id="p-backtest-signals"><p class='muted p16'>Sygnały dostępne tylko w trybie Live.</p></div>
    <div class="panel" id="p-backtest-trades">{bt_trades}</div>
    <div class="panel" id="p-backtest-strategies">{bt_strategies}</div>
    <div class="panel" id="p-backtest-analysis">{bt_analysis}</div>

    <div class="disclaimer">
      Silnik intraday — paper trading only. Brak realnych transakcji. Wszystkie pozycje zamykane przed 15:50 ET.
    </div>
  </div>
</div>

<script>
(function(){{
  var HASH="{pw_hash}",KEY="meipt_v1";
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

    live_m = compute_intraday_metrics(live)
    bt_m = compute_intraday_metrics(bt)

    updated = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = build_html(live, live_m, bt, bt_m, updated=updated)

    out_path = os.path.join(DOCS_DIR, "intraday_dashboard.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Intraday dashboard written → {out_path}  ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
