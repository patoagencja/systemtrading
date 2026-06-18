"""Generate a static HTML dashboard (docs/index.html) from the database.

Self-contained page (Plotly via CDN) suitable for GitHub Pages. Two tabs:
- Backtest  — historical 12-month simulation
- Live      — real-time paper trading from today onwards
"""
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))

import plotly.graph_objects as go

from app.database import db_cursor
from app.config import INITIAL_CAPITAL_PLN

DOCS_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "docs"))
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "trading2025")


def _fmt(x, dec=0):
    return f"{x:,.{dec}f}".replace(",", " ")


def gather(mode: str) -> dict:
    d = {}
    with db_cursor() as cur:
        cur.execute(
            "SELECT snapshot_date, total_value_pln, cash_pln, invested_pln "
            "FROM portfolio_snapshots WHERE run_mode=? ORDER BY snapshot_date",
            (mode,)
        )
        d["snapshots"] = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT COUNT(*) c FROM trades WHERE status='open' AND run_mode=?", (mode,))
        d["open_count"] = cur.fetchone()["c"]

        cur.execute("SELECT * FROM trades WHERE status='open' AND run_mode=? ORDER BY entry_date DESC", (mode,))
        d["open_trades"] = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM trades WHERE pnl_pln IS NOT NULL AND run_mode=?", (mode,))
        d["closed"] = [dict(r) for r in cur.fetchall()]

        cur.execute("""SELECT strategy, COUNT(*) n, SUM(pnl_pln) pnl,
                       100.0*SUM(CASE WHEN pnl_pln>0 THEN 1 ELSE 0 END)/COUNT(*) wr
                       FROM trades WHERE pnl_pln IS NOT NULL AND run_mode=?
                       GROUP BY strategy ORDER BY pnl DESC""", (mode,))
        d["by_strategy"] = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT ticker,strategy,pnl_pln,pnl_pct,exit_date FROM trades "
                    "WHERE pnl_pln IS NOT NULL AND run_mode=? ORDER BY pnl_pln DESC LIMIT 5", (mode,))
        d["best"] = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT ticker,strategy,pnl_pln,pnl_pct,exit_date FROM trades "
                    "WHERE pnl_pln IS NOT NULL AND run_mode=? ORDER BY pnl_pln ASC LIMIT 5", (mode,))
        d["worst"] = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT ticker,strategy,entry_date,exit_date,entry_price,exit_price,"
                    "pnl_pln,pnl_pct,r_multiple,exit_reason,status FROM trades "
                    "WHERE run_mode=? ORDER BY entry_date DESC LIMIT 50", (mode,))
        d["all_trades"] = [dict(r) for r in cur.fetchall()]
    return d


def compute_kpis(d: dict) -> dict:
    closed = d["closed"]
    snaps = d["snapshots"]
    k = {"start": INITIAL_CAPITAL_PLN, "n_closed": len(closed), "n_open": d["open_count"]}
    k["end"] = snaps[-1]["total_value_pln"] if snaps else INITIAL_CAPITAL_PLN
    k["cash"] = snaps[-1]["cash_pln"] if snaps else INITIAL_CAPITAL_PLN
    k["invested"] = snaps[-1]["invested_pln"] if snaps else 0.0

    peak, mdd = -1e18, 0.0
    for s in snaps:
        v = s["total_value_pln"]
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak)
    k["mdd"] = mdd * 100

    k["ret"] = (k["end"] / k["start"] - 1) * 100
    k["pnl"] = k["end"] - k["start"]
    wins = [t for t in closed if t["pnl_pln"] > 0]
    losses = [t for t in closed if t["pnl_pln"] <= 0]
    k["win_rate"] = (len(wins) / len(closed) * 100) if closed else 0
    gp = sum(t["pnl_pln"] for t in wins)
    gl = abs(sum(t["pnl_pln"] for t in losses))
    k["pf"] = (gp / gl) if gl > 0 else float("inf")
    return k


def make_charts(d: dict, prefix: str) -> str:
    snaps = d["snapshots"]
    parts = []
    if snaps:
        dates = [s["snapshot_date"] for s in snaps]
        vals = [s["total_value_pln"] for s in snaps]
        peak, dd = -1e18, []
        for v in vals:
            peak = max(peak, v)
            dd.append((v - peak) / peak * 100)

        eq = go.Figure()
        eq.add_trace(go.Scatter(x=dates, y=vals, mode="lines", name="Wartość portfela",
                                line=dict(color="#2563eb", width=2)))
        eq.add_hline(y=INITIAL_CAPITAL_PLN, line_dash="dash", line_color="#94a3b8",
                     annotation_text="Kapitał startowy")
        eq.update_layout(title="Krzywa kapitału", height=340,
                         margin=dict(l=40, r=20, t=44, b=20), template="plotly_white",
                         yaxis_title="PLN")
        parts.append(eq.to_html(full_html=False, include_plotlyjs=False,
                                config={"displayModeBar": False}))

        ddf = go.Figure()
        ddf.add_trace(go.Scatter(x=dates, y=dd, fill="tozeroy", mode="lines",
                                 line=dict(color="#dc2626", width=1), name="Drawdown"))
        ddf.update_layout(title="Drawdown (%)", height=220,
                          margin=dict(l=40, r=20, t=44, b=20), template="plotly_white",
                          yaxis_title="%")
        parts.append(ddf.to_html(full_html=False, include_plotlyjs=False,
                                 config={"displayModeBar": False}))

    if d["by_strategy"]:
        names = [r["strategy"] for r in d["by_strategy"]]
        pnls = [r["pnl"] for r in d["by_strategy"]]
        bar = go.Figure(go.Bar(x=pnls, y=names, orientation="h",
                               marker_color=["#16a34a" if p >= 0 else "#dc2626" for p in pnls]))
        bar.update_layout(title="P&L wg strategii", height=260,
                          margin=dict(l=40, r=20, t=44, b=20), template="plotly_white",
                          xaxis_title="PLN")
        parts.append(bar.to_html(full_html=False, include_plotlyjs=False,
                                 config={"displayModeBar": False}))
    return "\n".join(parts)


def _tr(row: dict, cols) -> str:
    tds = "".join(f"<td>{fn(row[c])}</td>" for c, fn in cols)
    return f"<tr>{tds}</tr>"


def _money(v): return f"{_fmt(v)} PLN" if v is not None else "-"
def _pct(v): return f"{v:+.1f}%" if v is not None else "-"
def _txt(v): return str(v) if v is not None else "-"
def _rnum(v): return f"{v:+.2f}" if v is not None else "-"
def _colored_money(v): cls = "pos" if v >= 0 else "neg"; return f"<span class='{cls}'>{_money(v)}</span>"
def _colored_pct(v): cls = "pos" if v >= 0 else "neg"; return f"<span class='{cls}'>{_pct(v)}</span>"


def render_tab(d: dict, k: dict, mode: str) -> str:
    pf = "∞" if k["pf"] == float("inf") else f"{k['pf']:.2f}"
    ret_cls = "pos" if k["ret"] >= 0 else "neg"

    snaps = d["snapshots"]
    period = f"{snaps[0]['snapshot_date']} → {snaps[-1]['snapshot_date']}" if snaps else "—"

    strat_rows = "\n".join(
        f"<tr><td>{r['strategy']}</td><td>{r['n']}</td><td>{r['wr']:.1f}%</td>"
        f"<td class='{'pos' if r['pnl'] >= 0 else 'neg'}'>{_fmt(r['pnl'])} PLN</td></tr>"
        for r in d["by_strategy"]
    ) or "<tr><td colspan=4>Brak danych</td></tr>"

    best_rows = "\n".join(_tr(r, [
        ("ticker", _txt), ("strategy", _txt),
        ("pnl_pln", _colored_money), ("pnl_pct", _colored_pct)
    ]) for r in d["best"]) or "<tr><td colspan=4>—</td></tr>"

    worst_rows = "\n".join(_tr(r, [
        ("ticker", _txt), ("strategy", _txt),
        ("pnl_pln", _colored_money), ("pnl_pct", _colored_pct)
    ]) for r in d["worst"]) or "<tr><td colspan=4>—</td></tr>"

    # All trades table (open + closed)
    all_rows = []
    for r in d["all_trades"]:
        st = r["status"]
        badge = f"<span class='badge {'badge-open' if st=='open' else 'badge-closed'}'>{st.upper()}</span>"
        pnl_cell = _colored_money(r["pnl_pln"]) if r["pnl_pln"] is not None else "<span class='mut'>live</span>"
        pct_cell = _colored_pct(r["pnl_pct"]) if r["pnl_pct"] is not None else "-"
        all_rows.append(
            f"<tr><td>{badge}</td><td><b>{_txt(r['ticker'])}</b></td>"
            f"<td>{_txt(r['strategy'])}</td>"
            f"<td>{_txt(r['entry_date'])}</td><td>{_txt(r['exit_date'])}</td>"
            f"<td>{pnl_cell}</td><td>{pct_cell}</td>"
            f"<td>{_rnum(r['r_multiple']) if r['r_multiple'] is not None else '-'}</td>"
            f"<td>{_txt(r['exit_reason'])}</td></tr>"
        )
    trades_html = "\n".join(all_rows) or "<tr><td colspan=9>Brak transakcji — robot nie uruchomił się jeszcze</td></tr>"

    charts_html = make_charts(d, mode)

    live_empty_note = ""
    if mode == "live" and not d["snapshots"]:
        live_empty_note = """
        <div class="live-empty">
          <div style="font-size:48px">🤖</div>
          <h3>Robot nie uruchomił się jeszcze</h3>
          <p>Pierwsze transakcje pojawią się po dzisiejszym skanie (23:00 UTC w dni robocze).<br>
          Możesz też uruchomić skan ręcznie w zakładce Actions na GitHubie.</p>
        </div>"""

    return f"""
    <div class="period-badge">Okres: {period}</div>
    {live_empty_note}
    <div class="kpis">
      <div class="kpi"><div class="label">Kapitał</div><div class="val">{_fmt(k['end'])} <span class="unit">PLN</span></div></div>
      <div class="kpi"><div class="label">Cash</div><div class="val">{_fmt(k['cash'])} <span class="unit">PLN</span></div></div>
      <div class="kpi"><div class="label">Zainwestowane</div><div class="val">{_fmt(k['invested'])} <span class="unit">PLN</span></div></div>
      <div class="kpi"><div class="label">Zwrot</div><div class="val {ret_cls}">{k['ret']:+.1f}%</div></div>
      <div class="kpi"><div class="label">P&amp;L</div><div class="val {ret_cls}">{_fmt(k['pnl'])} PLN</div></div>
      <div class="kpi"><div class="label">Win rate</div><div class="val">{k['win_rate']:.1f}%</div></div>
      <div class="kpi"><div class="label">Profit factor</div><div class="val">{pf}</div></div>
      <div class="kpi"><div class="label">Max drawdown</div><div class="val neg">{k['mdd']:.1f}%</div></div>
      <div class="kpi"><div class="label">Zamknięte</div><div class="val">{k['n_closed']}</div></div>
      <div class="kpi"><div class="label">Otwarte</div><div class="val">{k['n_open']}</div></div>
    </div>

    <div class="card">{charts_html if charts_html else '<p class="mut" style="padding:20px">Brak danych do wykresów.</p>'}</div>

    <div class="card">
      <h2>Wyniki wg strategii</h2>
      <table><thead><tr><th>Strategia</th><th>Transakcje</th><th>Win rate</th><th>P&amp;L</th></tr></thead>
      <tbody>{strat_rows}</tbody></table>
    </div>

    <div class="grid2">
      <div class="card"><h2>🏆 Top 5 zyski</h2>
        <table><thead><tr><th>Ticker</th><th>Strategia</th><th>P&amp;L</th><th>%</th></tr></thead>
        <tbody>{best_rows}</tbody></table></div>
      <div class="card"><h2>📉 Top 5 straty</h2>
        <table><thead><tr><th>Ticker</th><th>Strategia</th><th>P&amp;L</th><th>%</th></tr></thead>
        <tbody>{worst_rows}</tbody></table></div>
    </div>

    <div class="card">
      <h2>{'📡 Wszystkie transakcje live (otwarte i zamknięte)' if mode == 'live' else '📋 Ostatnie 50 transakcji'}</h2>
      <div style="overflow-x:auto">
      <table><thead><tr><th>Status</th><th>Ticker</th><th>Strategia</th><th>Wejście</th><th>Wyjście</th><th>P&amp;L</th><th>%</th><th>R</th><th>Powód wyjścia</th></tr></thead>
      <tbody>{trades_html}</tbody></table>
      </div>
    </div>"""


def build_html(d_bt: dict, k_bt: dict, d_lv: dict, k_lv: dict) -> str:
    pw_hash = __import__("hashlib").sha256(DASHBOARD_PASSWORD.encode()).hexdigest()
    updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    tab_bt = render_tab(d_bt, k_bt, "backtest")
    tab_lv = render_tab(d_lv, k_lv, "live")

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Edge Paper Trader</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<script>
/* Sync SHA-256 — works in file:// and https:// */
function _sha256(s){{
  function n(t,e){{return t>>>e|t<<32-e}}
  var r=[],o=[],i=0,a=8;
  for(var p=2;i<a;p++){{var f=!0;for(var c=2;c<=Math.sqrt(p);c++)if(p%c==0){{f=!1;break}}if(f){{i<a&&(r[i]=Math.pow(p,.5)%1*4294967296>>>0),o[i]=Math.pow(p,1/3)%1*4294967296>>>0,i++}}}}
  var h=[],l=function(t){{return t.charCodeAt?t.split("").map(function(t){{return t.charCodeAt(0)}}):t}},u=l(s);
  u.push(128);while(u.length%64!==56)u.push(0);
  var m=8*(s.length);u.push(0,0,0,0);
  for(var d=3;d>=0;d--)u.push(m>>>8*d&255);
  for(var y=0;y<u.length;y+=64){{
    for(var v=y,g=[],b=0;b<16;b++,v+=4)g[b]=u[v]<<24|u[v+1]<<16|u[v+2]<<8|u[v+3];
    for(var b=16;b<64;b++){{var w=g[b-15],x=g[b-2];g[b]=((n(w,7)^n(w,18)^w>>>3)+(g[b-7]>>>0)+((n(x,17)^n(x,19)^x>>>10)>>>0)+(g[b-16]>>>0))>>>0}}
    var S=r.slice(0);
    for(var b=0;b<64;b++){{var E=S[7]+(n(S[4],6)^n(S[4],11)^n(S[4],25))+(S[4]&S[5]^~S[4]&S[6])+o[b]+g[b],k=S[7]=(n(S[0],2)^n(S[0],13)^n(S[0],22))+(S[0]&S[1]^S[0]&S[2]^S[1]&S[2]);S=[E+k>>>0,S[0],S[1],S[2],S[3]+E>>>0,S[4],S[5],S[6]]}}
    for(var b=0;b<8;b++)r[b]=r[b]+S[b]>>>0
  }}
  return r.map(function(t){{return("00000000"+t.toString(16)).slice(-8)}}).join("")
}}
(function(){{
  var HASH="{pw_hash}",KEY="mept_auth";
  function unlock(){{
    document.getElementById("lock").style.display="none";
    document.getElementById("app").style.display="block";
    setTimeout(function(){{window.dispatchEvent(new Event("resize"))}},120);
  }}
  function check(pw){{
    if(_sha256(pw)===HASH){{sessionStorage.setItem(KEY,"1");unlock();}}
    else{{document.getElementById("pw-err").style.display="";document.getElementById("pw-in").value="";document.getElementById("pw-in").focus();}}
  }}
  window._checkPw=check;
  window.addEventListener("DOMContentLoaded",function(){{
    if(sessionStorage.getItem(KEY)==="1"){{unlock();return;}}
    document.getElementById("pw-in").addEventListener("keydown",function(e){{if(e.key==="Enter")check(this.value);}});
  }});
}})();
function switchTab(tab){{
  document.querySelectorAll(".tab-btn").forEach(function(b){{b.classList.remove("active")}});
  document.querySelectorAll(".tab-panel").forEach(function(p){{p.style.display="none"}});
  document.getElementById("tab-"+tab).style.display="block";
  document.querySelector(".tab-btn[data-tab='"+tab+"']").classList.add("active");
  setTimeout(function(){{window.dispatchEvent(new Event("resize"))}},80);
}}
</script>
<style>
:root{{--bg:#0f172a;--card:#1e293b;--txt:#e2e8f0;--mut:#94a3b8}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#f1f5f9;color:#0f172a}}
/* lock */
#lock{{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:9999}}
.lock-box{{background:var(--card);border-radius:16px;padding:40px 36px;text-align:center;max-width:340px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5)}}
.lock-box h2{{color:var(--txt);margin:0 0 6px;font-size:20px}}
.lock-box p{{color:var(--mut);font-size:13px;margin:0 0 24px}}
#pw-in{{width:100%;padding:11px 14px;border-radius:8px;border:1px solid #334155;background:var(--bg);color:var(--txt);font-size:15px;outline:none;box-sizing:border-box}}
#pw-in:focus{{border-color:#3b82f6}}
.lock-btn{{margin-top:12px;width:100%;padding:11px;border:none;border-radius:8px;background:#3b82f6;color:#fff;font-size:15px;font-weight:600;cursor:pointer}}
.lock-btn:hover{{background:#2563eb}}
#pw-err{{display:none;margin-top:10px;color:#f87171;font-size:13px}}
#app{{display:none}}
/* layout */
header{{background:var(--bg);color:#fff;padding:18px 20px}}
header h1{{margin:0 0 2px;font-size:20px}}
header p{{margin:0;color:var(--mut);font-size:12px}}
.wrap{{max-width:1140px;margin:0 auto;padding:18px}}
/* tabs */
.tabs{{display:flex;gap:4px;margin-bottom:18px;border-bottom:2px solid #e2e8f0;padding-bottom:0}}
.tab-btn{{padding:10px 28px;border:none;background:none;font-size:14px;font-weight:600;color:#64748b;cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px;transition:.15s}}
.tab-btn.active{{color:#2563eb;border-bottom-color:#2563eb}}
.tab-btn:hover:not(.active){{color:#0f172a}}
/* kpis */
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:16px}}
.kpi{{background:#fff;border-radius:12px;padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
.kpi .label{{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.04em}}
.kpi .val{{font-size:20px;font-weight:700;margin-top:4px}}
.unit{{font-size:12px;color:#64748b;font-weight:400}}
.pos{{color:#16a34a}} .neg{{color:#dc2626}} .mut{{color:#94a3b8}}
.card{{background:#fff;border-radius:12px;padding:16px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
h2{{font-size:15px;margin:0 0 10px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #e2e8f0;white-space:nowrap}}
th{{color:#64748b;font-weight:600}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:720px){{.grid2{{grid-template-columns:1fr}}}}
.period-badge{{display:inline-block;background:#e0e7ff;color:#3730a3;border-radius:6px;padding:3px 10px;font-size:12px;font-weight:600;margin-bottom:14px}}
.live-empty{{text-align:center;padding:48px 20px;color:#64748b}}
.live-empty h3{{margin:12px 0 8px;color:#0f172a}}
.badge{{display:inline-block;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:700}}
.badge-open{{background:#dcfce7;color:#15803d}}
.badge-closed{{background:#f1f5f9;color:#475569}}
.note{{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:12px 14px;font-size:12px;color:#92400e;margin-top:4px}}
</style>
</head>
<body>
<div id="lock">
  <div class="lock-box">
    <h2>📈 Paper Trader</h2>
    <p>Wpisz haslo, zeby zobaczyc dashboard</p>
    <input id="pw-in" type="password" placeholder="Haslo..." autofocus>
    <button class="lock-btn" onclick="_checkPw(document.getElementById('pw-in').value)">Wejdz</button>
    <div id="pw-err">Nieprawidlowe haslo. Sprobuj ponownie.</div>
  </div>
</div>
<div id="app">
<header>
  <h1>📈 Market Edge Paper Trader</h1>
  <p>Symulacja paper trading (wirtualny kapital, brak prawdziwych transakcji) &nbsp;·&nbsp; Aktualizacja: {updated}</p>
</header>
<div class="wrap">
  <div class="tabs">
    <button class="tab-btn active" data-tab="live" onclick="switchTab('live')">📡 Live Trading</button>
    <button class="tab-btn" data-tab="backtest" onclick="switchTab('backtest')">📊 Backtest (12 mies.)</button>
  </div>

  <div id="tab-live" class="tab-panel">
    {tab_lv}
  </div>
  <div id="tab-backtest" class="tab-panel" style="display:none">
    {tab_bt}
  </div>

  <div class="note">
    ⚠️ Symulacja edukacyjna — nie porada inwestycyjna. Brak prowizji, slippage i podatkow. Kurs USD/PLN uproszczony.
    Wynik z krotKiego okresu niczego nie dowodzi — wiarygodnosc daje dopiero 100–200+ transakcji.
  </div>
</div>
</div><!-- /app -->
</body>
</html>"""


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)
    d_bt = gather("backtest")
    k_bt = compute_kpis(d_bt)
    d_lv = gather("live")
    k_lv = compute_kpis(d_lv)
    html = build_html(d_bt, k_bt, d_lv, k_lv)
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    open(os.path.join(DOCS_DIR, ".nojekyll"), "w").close()
    print(f"Dashboard written → {DOCS_DIR}/index.html")


if __name__ == "__main__":
    main()
