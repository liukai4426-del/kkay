#!/usr/bin/env python3
"""Generate a self-contained interactive K-line trade-review webpage for a backtest artifact.

Source backtest: V1.7.0 4H Neutral+1 ShortDrift020 360D (#35384662171).
The page embeds the audited 5m BTC-USDT-SWAP candle snapshot plus all backtest trades.
15m and 1H views are aggregated client-side from the same 5m source.
"""
from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path

import run_v170_4h_neutralplus1_short_drift020_360d as runner
from research_backtest_data_cache import load_market

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = Path(os.environ.get("TRADE_VIEWER_SOURCE_DIR", "source_artifact"))
OUT_DIR = Path(os.environ.get("TRADE_VIEWER_OUT", "backtest_trade_viewer_v170_360d"))
OUT_HTML = OUT_DIR / "KAYTRADE-V1.7.0-360D-Trade-Viewer.html"

TRADE_CSV_NAME = "trades_v170_4h_neutralplus1_short_drift020_360d.csv"
RESULT_JSON_NAME = "result_v170_4h_neutralplus1_short_drift020_360d.json"

NUMERIC_FIELDS = {
    "score", "entry", "stop", "target", "exit", "quantity_btc", "net_pnl", "gross_pnl",
    "entry_fee", "exit_fee", "funding_pnl", "realized_r", "mfe_r", "mae_r", "front_r",
    "cost_r", "hold_min", "direction_pullback", "entry_near_zone", "structure_overlap",
    "macd_improving", "trend4h", "ema50_15m_move", "volume5", "kdj5_signal", "rsi5",
    "signal_age_min", "entry_drift_atr5", "atr5_pct", "atr1h_pct", "position_multiplier",
    "base_position_notional_cap", "requested_notional_cap", "effective_notional_cap",
    "signal_volume_ratio", "signal_window_ms", "boll_outer_score",
    "boll5_overext035_first_atr_ratio", "boll5_overext035_latest_atr_ratio",
    "short_entry_drift_hard_gate_max_atr5", "exit_limit_protection_bps",
    "pee_mfe_cutoff_r", "early_exit_current_r", "early_exit_mfe_r",
    "early_exit_hard_count", "early_exit_soft_score",
}
INT_FIELDS = {"entry_time", "exit_time", "boll5_overext035_first_bar_t"}


def _finite(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _convert_trade(row):
    out = {}
    for key, value in row.items():
        if value is None or value == "":
            out[key] = None
            continue
        if key in INT_FIELDS:
            try:
                out[key] = int(float(value))
            except (TypeError, ValueError):
                out[key] = None
            continue
        if key in NUMERIC_FIELDS:
            out[key] = _finite(value)
            continue
        if key in {"score_components", "trigger_combination", "early_exit_soft_components"}:
            try:
                out[key] = json.loads(value)
            except Exception:
                out[key] = value
            continue
        if value in ("True", "False"):
            out[key] = value == "True"
            continue
        out[key] = value
    return out


def _normalize_candle(row):
    if isinstance(row, dict):
        t = row.get("t", row.get("ts"))
        o = row.get("o", row.get("open"))
        h = row.get("h", row.get("high"))
        l = row.get("l", row.get("low"))
        c = row.get("c", row.get("close"))
    else:
        # Fallback for tuple/list snapshots.
        t, o, h, l, c = row[:5]
    try:
        return [int(float(t)), float(o), float(h), float(l), float(c)]
    except (TypeError, ValueError, IndexError):
        return None


def _load_source():
    trade_csv = SOURCE_DIR / TRADE_CSV_NAME
    result_json = SOURCE_DIR / RESULT_JSON_NAME
    if not trade_csv.exists():
        raise FileNotFoundError(f"missing backtest trade CSV: {trade_csv}")
    if not result_json.exists():
        raise FileNotFoundError(f"missing backtest result JSON: {result_json}")

    with trade_csv.open("r", encoding="utf-8-sig", newline="") as fh:
        trades = [_convert_trade(row) for row in csv.DictReader(fh)]

    result = json.loads(result_json.read_text(encoding="utf-8"))
    return trades, result


def _load_candles():
    runner._patch()
    start, end = runner.src.configure()
    runner.verify(start, end)

    _, market, _, manifest = load_market(
        runner.src.base,
        timeframes=("5m",),
        root=os.environ.get("BACKTEST_MARKET_CACHE", ".backtest_cache"),
    )

    candles = []
    for row in market["5m"]:
        item = _normalize_candle(row)
        if item is None:
            continue
        # Keep the exact research window, plus one day of visual context on each side if available.
        if int(start.timestamp() * 1000) - 86_400_000 <= item[0] <= int(end.timestamp() * 1000) + 86_400_000:
            candles.append(item)
    candles.sort(key=lambda x: x[0])
    if not candles:
        raise RuntimeError("no 5m candles available for trade viewer")
    return candles, manifest, start, end


def _json_script(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _html(candles, trades, result, manifest, start, end):
    payload = {
        "candles5m": candles,
        "trades": trades,
        "meta": {
            "title": "KAYTRADE V1.7.0 · 360D 回测交易K线审查",
            "source_run": 35384662171,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "trade_count": len(trades),
            "metrics": result.get("metrics", {}),
            "strategy_lock": result.get("strategy_lock", {}),
            "cache": manifest,
        },
    }
    data = _json_script(payload)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KAYTRADE V1.7.0 · 360D 回测交易K线审查</title>
<style>
:root {{
  color-scheme: dark;
  --bg:#0b0d10; --panel:#12161b; --panel2:#171c22; --line:#2a323c;
  --text:#e7edf3; --muted:#8e9aa8; --green:#28c78f; --red:#f35b67;
  --blue:#63a6ff; --amber:#f2b84b; --purple:#aa84ff;
}}
*{{box-sizing:border-box}}
html,body{{margin:0;height:100%;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
button,select,input{{font:inherit}}
#app{{height:100%;display:grid;grid-template-columns:340px 1fr;overflow:hidden}}
.sidebar{{background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column;min-width:0}}
.brand{{padding:18px 18px 12px;border-bottom:1px solid var(--line)}}
.brand h1{{font-size:17px;margin:0 0 6px;font-weight:700}}
.brand .sub{{font-size:12px;color:var(--muted);line-height:1.5}}
.filters{{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:12px;border-bottom:1px solid var(--line)}}
.filters select,.filters input{{width:100%;background:#0e1217;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:8px}}
.filters input{{grid-column:1/-1}}
.trade-list{{flex:1;overflow:auto;padding:8px}}
.trade-item{{border:1px solid transparent;border-radius:10px;padding:10px;margin-bottom:6px;background:#0e1217;cursor:pointer}}
.trade-item:hover{{border-color:#36414d}}
.trade-item.active{{border-color:var(--blue);background:#121b26}}
.trade-top{{display:flex;justify-content:space-between;gap:8px;align-items:center}}
.badge{{font-size:11px;font-weight:700;border-radius:6px;padding:3px 6px}}
.badge.long{{background:rgba(40,199,143,.14);color:var(--green)}}
.badge.short{{background:rgba(243,91,103,.14);color:var(--red)}}
.pnl.pos{{color:var(--green);font-weight:700}} .pnl.neg{{color:var(--red);font-weight:700}}
.trade-meta{{margin-top:6px;color:var(--muted);font-size:11px;display:flex;justify-content:space-between}}
.main{{display:flex;flex-direction:column;min-width:0;min-height:0}}
.toolbar{{height:58px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:8px;padding:0 14px;background:#0e1217;flex-wrap:wrap}}
.toolbar button,.toolbar select,.toolbar label{{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:8px;padding:7px 10px}}
.toolbar button{{cursor:pointer}} .toolbar button:hover{{border-color:#4a5868}}
.toolbar .spacer{{flex:1}}
.toolbar label{{font-size:12px;display:flex;align-items:center;gap:6px}}
.chart-wrap{{position:relative;flex:1;min-height:360px;background:#090b0e;overflow:hidden}}
#chart{{width:100%;height:100%;display:block}}
#tooltip{{position:absolute;pointer-events:none;display:none;background:rgba(10,13,17,.94);border:1px solid #38424d;border-radius:8px;padding:8px 10px;font-size:12px;line-height:1.5;z-index:5;min-width:170px}}
.legend{{position:absolute;left:12px;top:10px;z-index:3;background:rgba(9,11,14,.76);border:1px solid rgba(70,80,92,.5);border-radius:8px;padding:7px 9px;font-size:11px;color:var(--muted)}}
.bottom{{height:218px;border-top:1px solid var(--line);display:grid;grid-template-columns:1.2fr 1fr;background:var(--panel);min-height:160px}}
.details,.stats{{padding:14px 16px;overflow:auto}}
.details{{border-right:1px solid var(--line)}}
.details h3,.stats h3{{margin:0 0 10px;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(110px,1fr));gap:8px 12px}}
.kv{{background:#0e1217;border-radius:8px;padding:8px 10px;min-width:0}}
.kv .k{{color:var(--muted);font-size:10px;margin-bottom:4px}} .kv .v{{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.stats-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}
.stat{{background:#0e1217;border-radius:8px;padding:10px}} .stat .n{{font-weight:750;font-size:16px}} .stat .l{{font-size:10px;color:var(--muted);margin-top:4px}}
.note{{margin-top:10px;color:var(--muted);font-size:11px;line-height:1.55}}
@media(max-width:900px){{#app{{grid-template-columns:260px 1fr}} .grid{{grid-template-columns:repeat(2,1fr)}} .bottom{{grid-template-columns:1fr;height:280px}} .details{{border-right:0;border-bottom:1px solid var(--line)}}}}
</style>
</head>
<body>
<div id="app">
  <aside class="sidebar">
    <div class="brand">
      <h1>KAYTRADE · 回测交易K线审查</h1>
      <div class="sub">V1.7.0 · Run #35384662171<br>点击订单后自动定位开仓/平仓位置。</div>
    </div>
    <div class="filters">
      <select id="sideFilter"><option value="all">全部方向</option><option value="做多">只看做多</option><option value="做空">只看做空</option></select>
      <select id="pnlFilter"><option value="all">全部盈亏</option><option value="win">只看盈利</option><option value="loss">只看亏损</option></select>
      <input id="search" placeholder="搜索订单号 / 退出原因 / 时间">
    </div>
    <div id="tradeList" class="trade-list"></div>
  </aside>
  <main class="main">
    <div class="toolbar">
      <button id="prevBtn">← 上一笔</button>
      <button id="nextBtn">下一笔 →</button>
      <select id="tf">
        <option value="300000">5m</option>
        <option value="900000" selected>15m</option>
        <option value="3600000">1H</option>
      </select>
      <select id="context">
        <option value="3">前后 3h</option>
        <option value="6" selected>前后 6h</option>
        <option value="12">前后 12h</option>
        <option value="24">前后 24h</option>
      </select>
      <label><input id="bollToggle" type="checkbox" checked> BOLL20</label>
      <label><input id="emaToggle" type="checkbox"> EMA20/50</label>
      <button id="resetBtn">重置视图</button>
      <div class="spacer"></div>
      <span id="orderLabel" style="font-size:12px;color:var(--muted)"></span>
    </div>
    <div class="chart-wrap" id="chartWrap">
      <canvas id="chart"></canvas>
      <div class="legend" id="legend">滚轮缩放 · 拖拽平移 · 悬浮查看OHLC</div>
      <div id="tooltip"></div>
    </div>
    <div class="bottom">
      <section class="details">
        <h3>当前订单</h3>
        <div id="detailGrid" class="grid"></div>
      </section>
      <section class="stats">
        <h3>回测摘要</h3>
        <div id="statsRows" class="stats-row"></div>
        <div class="note">开仓标记与平仓标记使用回测成交时间；SL / TP / Entry 为该笔订单固定水平线。15m 与 1H K线由同一份审计5m历史数据聚合得到。</div>
      </section>
    </div>
  </main>
</div>
<script>
const DATA={data};
const raw5=DATA.candles5m;
const allTrades=DATA.trades;
const meta=DATA.meta;

const canvas=document.getElementById('chart');
const wrap=document.getElementById('chartWrap');
const ctx=canvas.getContext('2d');
const tooltip=document.getElementById('tooltip');
const tradeList=document.getElementById('tradeList');
const sideFilter=document.getElementById('sideFilter');
const pnlFilter=document.getElementById('pnlFilter');
const searchInput=document.getElementById('search');
const tfSelect=document.getElementById('tf');
const contextSelect=document.getElementById('context');
const bollToggle=document.getElementById('bollToggle');
const emaToggle=document.getElementById('emaToggle');
const detailGrid=document.getElementById('detailGrid');
const orderLabel=document.getElementById('orderLabel');

let filtered=[];
let selectedOriginalIndex=0;
let candles=[];
let viewStart=0, viewEnd=100;
let drag=false, dragX=0, dragStart=0, dragEnd=0;
let mouse={x:-1,y:-1,inside:false};

const COLORS={{
  bg:'#090b0e', grid:'#1b222b', text:'#8391a0', up:'#28c78f', down:'#f35b67',
  blue:'#63a6ff', amber:'#f2b84b', purple:'#aa84ff', white:'#e7edf3'
}};

function num(v,d=2){{const n=Number(v);return Number.isFinite(n)?n.toLocaleString('en-US',{{maximumFractionDigits:d,minimumFractionDigits:0}}):'—';}}
function signed(v,d=2){{const n=Number(v);return Number.isFinite(n)?(n>=0?'+':'')+num(n,d):'—';}}
function sideClass(t){{return t.side==='做多'?'long':'short';}}
function fmtTime(ms){{if(!ms)return '—';const d=new Date(Number(ms));return new Intl.DateTimeFormat('zh-CN',{{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}}).format(d);}}
function fmtFull(ms){{if(!ms)return '—';const d=new Date(Number(ms));return new Intl.DateTimeFormat('zh-CN',{{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}}).format(d);}}

function aggregate(src,ms){{
  if(ms===300000)return src;
  const out=[]; let cur=null, bucket=null;
  for(const r of src){{
    const b=Math.floor(r[0]/ms)*ms;
    if(b!==bucket){{
      if(cur)out.push(cur);
      bucket=b; cur=[b,r[1],r[2],r[3],r[4]];
    }}else{{
      cur[2]=Math.max(cur[2],r[2]); cur[3]=Math.min(cur[3],r[3]); cur[4]=r[4];
    }}
  }}
  if(cur)out.push(cur); return out;
}}

function calcIndicators(src){{
  const closes=src.map(x=>x[4]);
  const ema=(p)=>{{const a=2/(p+1),out=[];let v=closes[0];for(let i=0;i<closes.length;i++){{v=i===0?closes[i]:closes[i]*a+v*(1-a);out.push(v);}}return out;}};
  const e20=ema(20),e50=ema(50);
  const mid=[],upper=[],lower=[];
  let sum=0,sumSq=0,q=[];
  for(let i=0;i<closes.length;i++){{
    const x=closes[i]; q.push(x); sum+=x; sumSq+=x*x;
    if(q.length>20){{const y=q.shift();sum-=y;sumSq-=y*y;}}
    if(q.length===20){{const m=sum/20;const variance=Math.max(0,sumSq/20-m*m);const sd=Math.sqrt(variance);mid.push(m);upper.push(m+2*sd);lower.push(m-2*sd);}}
    else{{mid.push(null);upper.push(null);lower.push(null);}}
  }}
  return {{e20,e50,mid,upper,lower}};
}}
let indicators=null;

function rebuildCandles(reset=true){{
  const ms=Number(tfSelect.value); candles=aggregate(raw5,ms); indicators=calcIndicators(candles);
  if(reset)focusTrade();
  else draw();
}}

function nearestIndex(ts){{
  let lo=0,hi=candles.length-1;
  while(lo<hi){{const m=(lo+hi)>>1;if(candles[m][0]<ts)lo=m+1;else hi=m;}}
  if(lo>0 && Math.abs(candles[lo-1][0]-ts)<Math.abs(candles[lo][0]-ts))return lo-1;
  return lo;
}}

function currentTrade(){{
  return allTrades[selectedOriginalIndex] || allTrades[0];
}}

function focusTrade(){{
  const t=currentTrade(); if(!t||!candles.length)return;
  const ctxH=Number(contextSelect.value)||6;
  const ms=Number(tfSelect.value);
  const a=nearestIndex(Number(t.entry_time)-ctxH*3600000);
  const b=nearestIndex(Number(t.exit_time)+ctxH*3600000);
  viewStart=Math.max(0,a); viewEnd=Math.min(candles.length-1,Math.max(b,a+30));
  draw(); updateDetails(); highlightList(); updateOrderLabel();
}}

function applyFilters(){{
  const side=sideFilter.value,pnl=pnlFilter.value,q=searchInput.value.trim().toLowerCase();
  filtered=allTrades.map((t,i)=>({{t,i}})).filter(({t,i})=>{{
    if(side!=='all'&&t.side!==side)return false;
    const p=Number(t.net_pnl);
    if(pnl==='win'&&!(p>0))return false;
    if(pnl==='loss'&&!(p<0))return false;
    if(q){{
      const text=[i+1,t.reason,t.opportunity_id,fmtFull(t.entry_time),fmtFull(t.exit_time),t.side].join(' ').toLowerCase();
      if(!text.includes(q))return false;
    }}
    return true;
  }});
  renderTradeList();
  if(!filtered.some(x=>x.i===selectedOriginalIndex) && filtered.length){{selectedOriginalIndex=filtered[0].i;focusTrade();}}
}}

function renderTradeList(){{
  tradeList.innerHTML='';
  for(const {{t,i}} of filtered){{
    const div=document.createElement('div');
    div.className='trade-item'+(i===selectedOriginalIndex?' active':'');
    const pnl=Number(t.net_pnl)||0;
    div.innerHTML=`<div class="trade-top"><span><span class="badge \${{sideClass(t)}}">\${{t.side}}</span> #\${{i+1}}</span><span class="pnl \${{pnl>=0?'pos':'neg'}}">\${{signed(pnl,2)}}U</span></div>
      <div class="trade-meta"><span>\${{fmtTime(t.entry_time)}} → \${{fmtTime(t.exit_time)}}</span><span>\${{t.reason||'—'}}</span></div>`;
    div.onclick=()=>{{selectedOriginalIndex=i;focusTrade();}};
    tradeList.appendChild(div);
  }}
}}

function highlightList(){{
  [...tradeList.children].forEach((el,k)=>{{el.classList.toggle('active',filtered[k]&&filtered[k].i===selectedOriginalIndex);}});
  const idx=filtered.findIndex(x=>x.i===selectedOriginalIndex);
  if(idx>=0 && tradeList.children[idx])tradeList.children[idx].scrollIntoView({{block:'nearest'}});
}}

function updateOrderLabel(){{
  const t=currentTrade();
  orderLabel.textContent=`#\${{selectedOriginalIndex+1}} · \${{t.side}} · \${{signed(t.net_pnl,2)}}U · \${{t.reason||'—'}}`;
}}

function updateDetails(){{
  const t=currentTrade(); if(!t)return;
  const items=[
    ['方向',t.side],['评分',num(t.score,1)],['净收益',signed(t.net_pnl,2)+' U'],['Realized R',signed(t.realized_r,2)+'R'],
    ['开仓时间',fmtFull(t.entry_time)],['平仓时间',fmtFull(t.exit_time)],['持仓',num(t.hold_min,0)+' min'],['退出原因',t.reason||'—'],
    ['开仓价',num(t.entry,2)],['平仓价',num(t.exit,2)],['止损',num(t.stop,2)],['止盈',num(t.target,2)],
    ['MFE',signed(t.mfe_r,2)+'R'],['MAE',signed(t.mae_r,2)+'R'],['Entry Drift',t.entry_drift_atr5==null?'—':signed(t.entry_drift_atr5,3)+' ATR5'],['4H状态',t['4h_trend_state_actual']||'—'],
  ];
  detailGrid.innerHTML=items.map(([k,v])=>`<div class="kv"><div class="k">\${{k}}</div><div class="v" title="\${{String(v)}}">\${{v}}</div></div>`).join('');
}}

function updateStats(){{
  const m=meta.metrics||{{}};
  const stats=[
    ['交易数',m.trades??allTrades.length],['胜率',m.win_rate_pct!=null?num(m.win_rate_pct,2)+'%':'—'],['净收益',signed(m.net_pnl,2)+'U'],
    ['PF',num(m.profit_factor,3)],['最大回撤',m.max_drawdown_pct!=null?num(m.max_drawdown_pct,2)+'%':'—'],['初始资金','2000U']
  ];
  document.getElementById('statsRows').innerHTML=stats.map(([l,n])=>`<div class="stat"><div class="n">\${{n}}</div><div class="l">\${{l}}</div></div>`).join('');
}}

function resize(){{
  const r=wrap.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
  canvas.width=Math.max(1,Math.floor(r.width*dpr)); canvas.height=Math.max(1,Math.floor(r.height*dpr));
  canvas.style.width=r.width+'px'; canvas.style.height=r.height+'px';
  ctx.setTransform(dpr,0,0,dpr,0,0); draw();
}}

function visible(){{
  const a=Math.max(0,Math.floor(viewStart)),b=Math.min(candles.length-1,Math.ceil(viewEnd));
  return [a,b];
}}

function draw(){{
  if(!candles.length)return;
  const W=canvas.clientWidth,H=canvas.clientHeight;
  ctx.clearRect(0,0,W,H);ctx.fillStyle=COLORS.bg;ctx.fillRect(0,0,W,H);
  const left=14,right=76,top=18,bottom=28;
  const cw=W-left-right,ch=H-top-bottom;
  let [a,b]=visible(); if(b<=a)return;
  const t=currentTrade();
  let lo=Infinity,hi=-Infinity;
  for(let i=a;i<=b;i++){{lo=Math.min(lo,candles[i][3]);hi=Math.max(hi,candles[i][2]);
    if(bollToggle.checked&&indicators.mid[i]!=null){{lo=Math.min(lo,indicators.lower[i]);hi=Math.max(hi,indicators.upper[i]);}}
    if(emaToggle.checked){{lo=Math.min(lo,indicators.e20[i],indicators.e50[i]);hi=Math.max(hi,indicators.e20[i],indicators.e50[i]);}}
  }}
  for(const v of [t.entry,t.exit,t.stop,t.target]){{const n=Number(v);if(Number.isFinite(n)){{lo=Math.min(lo,n);hi=Math.max(hi,n);}}}}
  if(!Number.isFinite(lo)||!Number.isFinite(hi)||hi<=lo)return;
  const pad=(hi-lo)*.07||1;lo-=pad;hi+=pad;
  const x=i=>left+(i-a)/(b-a+1)*cw;
  const y=p=>top+(hi-p)/(hi-lo)*ch;

  ctx.strokeStyle=COLORS.grid;ctx.lineWidth=1;ctx.font='10px -apple-system, sans-serif';ctx.fillStyle=COLORS.text;
  for(let k=0;k<=5;k++){{const yy=top+k*ch/5;ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(left+cw,yy);ctx.stroke();
    const p=hi-k*(hi-lo)/5;ctx.fillText(num(p,2),left+cw+7,yy+3);}}
  for(let k=0;k<=6;k++){{const xx=left+k*cw/6;ctx.beginPath();ctx.moveTo(xx,top);ctx.lineTo(xx,top+ch);ctx.stroke();
    const idx=Math.min(b,Math.max(a,Math.round(a+k*(b-a)/6)));ctx.fillText(fmtTime(candles[idx][0]),Math.max(left,xx-25),H-8);}}

  function path(values,color,width=1.2,dash=[]){{
    ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);ctx.beginPath();let started=false;
    for(let i=a;i<=b;i++){{const v=values[i];if(v==null||!Number.isFinite(v)){{started=false;continue;}}const xx=x(i),yy=y(v);if(!started){{ctx.moveTo(xx,yy);started=true;}}else ctx.lineTo(xx,yy);}}
    ctx.stroke();ctx.setLineDash([]);
  }}
  if(bollToggle.checked){{path(indicators.upper,'rgba(99,166,255,.75)',1);path(indicators.mid,'rgba(99,166,255,.35)',1,[4,4]);path(indicators.lower,'rgba(99,166,255,.75)',1);}}
  if(emaToggle.checked){{path(indicators.e20,'rgba(242,184,75,.9)',1.2);path(indicators.e50,'rgba(170,132,255,.9)',1.2);}}

  const barW=Math.max(1,Math.min(10,cw/(b-a+1)*.72));
  for(let i=a;i<=b;i++){{
    const r=candles[i],xx=x(i),yo=y(r[1]),yh=y(r[2]),yl=y(r[3]),yc=y(r[4]);
    const up=r[4]>=r[1];ctx.strokeStyle=up?COLORS.up:COLORS.down;ctx.fillStyle=ctx.strokeStyle;
    ctx.beginPath();ctx.moveTo(xx,yh);ctx.lineTo(xx,yl);ctx.stroke();
    const topBody=Math.min(yo,yc),bodyH=Math.max(1,Math.abs(yc-yo));ctx.fillRect(xx-barW/2,topBody,barW,bodyH);
  }}

  function hline(price,color,label,dash=[5,4]){{
    const p=Number(price);if(!Number.isFinite(p))return;const yy=y(p);ctx.strokeStyle=color;ctx.lineWidth=1;ctx.setLineDash(dash);
    ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(left+cw,yy);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=color;ctx.fillText(label+' '+num(p,2),left+6,yy-4);
  }}
  hline(t.entry,COLORS.blue,'ENTRY');hline(t.stop,COLORS.red,'SL',[3,3]);hline(t.target,COLORS.green,'TP',[3,3]);

  function marker(ts,price,color,label,shape){{
    const idx=nearestIndex(Number(ts));if(idx<a||idx>b)return;const xx=x(idx),yy=y(Number(price));ctx.fillStyle=color;ctx.strokeStyle=color;ctx.lineWidth=2;
    if(shape==='tri'){{ctx.beginPath();if(t.side==='做多'){{ctx.moveTo(xx,yy-10);ctx.lineTo(xx-7,yy+4);ctx.lineTo(xx+7,yy+4);}}else{{ctx.moveTo(xx,yy+10);ctx.lineTo(xx-7,yy-4);ctx.lineTo(xx+7,yy-4);}}ctx.closePath();ctx.fill();}}
    else{{ctx.beginPath();ctx.arc(xx,yy,5,0,Math.PI*2);ctx.stroke();ctx.beginPath();ctx.moveTo(xx-4,yy-4);ctx.lineTo(xx+4,yy+4);ctx.moveTo(xx+4,yy-4);ctx.lineTo(xx-4,yy+4);ctx.stroke();}}
    ctx.font='bold 10px -apple-system,sans-serif';ctx.fillStyle=color;ctx.fillText(label,xx+8,yy-8);
  }}
  marker(t.entry_time,t.entry,t.side==='做多'?COLORS.green:COLORS.red,'开仓','tri');
  marker(t.exit_time,t.exit,Number(t.net_pnl)>=0?COLORS.green:COLORS.red,'平仓','x');

  if(mouse.inside){{
    const i=Math.round(a+(mouse.x-left)/cw*(b-a+1));
    if(i>=a&&i<=b){{
      const xx=x(i);ctx.strokeStyle='rgba(220,230,240,.35)';ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(xx,top);ctx.lineTo(xx,top+ch);ctx.stroke();ctx.setLineDash([]);
    }}
  }}
}}

function showTooltip(ev){{
  const rect=canvas.getBoundingClientRect();mouse.x=ev.clientX-rect.left;mouse.y=ev.clientY-rect.top;mouse.inside=true;
  const W=canvas.clientWidth,left=14,right=76,cw=W-left-right;let[a,b]=visible();const i=Math.round(a+(mouse.x-left)/cw*(b-a+1));
  if(i<a||i>b){{tooltip.style.display='none';draw();return;}}
  const r=candles[i];
  tooltip.innerHTML=`<b>\${{fmtFull(r[0])}}</b><br>O \${{num(r[1],2)}}<br>H \${{num(r[2],2)}}<br>L \${{num(r[3],2)}}<br>C \${{num(r[4],2)}}`;
  tooltip.style.display='block';
  tooltip.style.left=Math.min(canvas.clientWidth-190,mouse.x+14)+'px';tooltip.style.top=Math.max(8,Math.min(canvas.clientHeight-120,mouse.y+12))+'px';draw();
}}

canvas.addEventListener('mousemove',e=>{{if(drag){{const dx=e.clientX-dragX;const bars=(dragEnd-dragStart+1);const shift=-dx/Math.max(1,canvas.clientWidth-90)*bars;viewStart=dragStart+shift;viewEnd=dragEnd+shift;
  if(viewStart<0){{viewEnd-=viewStart;viewStart=0;}}if(viewEnd>candles.length-1){{const d=viewEnd-(candles.length-1);viewStart-=d;viewEnd-=d;}}draw();}}else showTooltip(e);}});
canvas.addEventListener('mouseleave',()=>{{mouse.inside=false;tooltip.style.display='none';draw();}});
canvas.addEventListener('mousedown',e=>{{drag=true;dragX=e.clientX;dragStart=viewStart;dragEnd=viewEnd;canvas.style.cursor='grabbing';}});
window.addEventListener('mouseup',()=>{{drag=false;canvas.style.cursor='crosshair';}});
canvas.addEventListener('wheel',e=>{{e.preventDefault();const rect=canvas.getBoundingClientRect();const px=(e.clientX-rect.left-14)/Math.max(1,canvas.clientWidth-90);const center=viewStart+Math.max(0,Math.min(1,px))*(viewEnd-viewStart);
  const span=Math.max(20,(viewEnd-viewStart)*(e.deltaY>0?1.18:.84));viewStart=center-span*Math.max(0,Math.min(1,px));viewEnd=viewStart+span;
  if(viewStart<0){{viewEnd-=viewStart;viewStart=0;}}if(viewEnd>candles.length-1){{const d=viewEnd-(candles.length-1);viewStart-=d;viewEnd-=d;}}draw();}},{{passive:false}});

function step(dir){{
  if(!filtered.length)return;let k=filtered.findIndex(x=>x.i===selectedOriginalIndex);if(k<0)k=0;k=(k+dir+filtered.length)%filtered.length;selectedOriginalIndex=filtered[k].i;focusTrade();
}}
document.getElementById('prevBtn').onclick=()=>step(-1);
document.getElementById('nextBtn').onclick=()=>step(1);
document.getElementById('resetBtn').onclick=focusTrade;
tfSelect.onchange=()=>rebuildCandles(true);contextSelect.onchange=focusTrade;
bollToggle.onchange=draw;emaToggle.onchange=draw;
sideFilter.onchange=applyFilters;pnlFilter.onchange=applyFilters;searchInput.oninput=applyFilters;
window.addEventListener('resize',resize);
window.addEventListener('keydown',e=>{{if(e.key==='ArrowLeft')step(-1);if(e.key==='ArrowRight')step(1);}});

updateStats();filtered=allTrades.map((t,i)=>({{t,i}}));renderTradeList();rebuildCandles(true);resize();
</script>
</body>
</html>"""


def main():
    trades, result = _load_source()
    candles, manifest, start, end = _load_candles()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html = _html(candles, trades, result, manifest, start, end)
    OUT_HTML.write_text(html, encoding="utf-8")
    print("TRADE_VIEWER", OUT_HTML)
    print("TRADES", len(trades), "CANDLES_5M", len(candles), "BYTES", OUT_HTML.stat().st_size)


if __name__ == "__main__":
    main()
