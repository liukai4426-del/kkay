"""KAYTRADE V1.4.1 historical return chart axes.

- x-axis: closed-round date
- y-axis: cumulative return percentage
- return baseline: current strategy capital budget
- keep the historical detail table in USDT
"""
import math

from v141_dialog_signal_positions_patch import apply as apply_v141
apply_v141()

import app
import engine


def _history_points(rows, capital):
    """Return [(timestamp, cumulative_return_pct), ...] for visible closed rounds."""
    base=float(capital)
    if not math.isfinite(base) or base<=0:
        return []
    out=[]
    for row in rows or []:
        try:
            stamp=str(row.get('time') or '')
            cumulative=float(row.get('cumulative'))
            if not stamp or not math.isfinite(cumulative):
                continue
            out.append((stamp,cumulative/base*100.0))
        except (TypeError,ValueError,AttributeError):
            continue
    return out


def _date_label(stamp):
    text=str(stamp or '')
    if len(text)>=10 and text[4]=='-' and text[7]=='-':
        return text[5:10]
    return text[:10] or '—'


def _rate_label(value):
    return f'{float(value):+.2f}%'


def _capital_for_chart(ui):
    try:
        value=float(ui.fields['capital'].get())
        if math.isfinite(value) and value>0:return value
    except Exception:
        pass
    try:
        value=float(ui.engine.settings.capital)
        if math.isfinite(value) and value>0:return value
    except Exception:
        pass
    return 0.0


def _draw_curve(self):
    chart=self.chart
    chart.delete('all')
    points=list(getattr(self,'_v141_history_return_points',[]) or [])
    capital=float(getattr(self,'_v141_history_return_capital',0.0) or 0.0)
    if not points:
        chart.create_text(24,65,anchor='w',text='完成首轮交易后显示累计收益率曲线',fill='#8fa4b3')
        return

    width=max(320,int(chart.winfo_width() or 0))
    height=max(150,int(chart.winfo_height() or 0))
    left,right,top,bottom=62,18,26,30
    plot_w=max(120,width-left-right)
    plot_h=max(70,height-top-bottom)
    rates=[float(rate) for _,rate in points]
    low=min(min(rates),0.0); high=max(max(rates),0.0)
    if abs(high-low)<1e-9:
        pad=max(abs(high)*0.1,0.5)
        low-=pad; high+=pad
    else:
        pad=(high-low)*0.10
        low-=pad; high+=pad
    span=max(high-low,1e-9)

    grid='#314047'
    axis='#60727b'
    label='#8fa4b3'
    # Y-axis: return percentage.
    for step in range(5):
        frac=step/4
        value=high-frac*span
        y=top+frac*plot_h
        chart.create_line(left,y,left+plot_w,y,fill=grid,width=1)
        chart.create_text(left-8,y,anchor='e',text=_rate_label(value),fill=label,font=('Helvetica',9))
    chart.create_line(left,top,left,top+plot_h,fill=axis,width=1)
    chart.create_line(left,top+plot_h,left+plot_w,top+plot_h,fill=axis,width=1)

    # X-axis: dates. Keep labels sparse so they remain readable on smaller Mac windows.
    count=len(points)
    tick_indices=sorted(set([0,count-1]+[round((count-1)*f) for f in (0.25,0.5,0.75)]))
    for idx in tick_indices:
        x=left if count==1 else left+idx*plot_w/(count-1)
        chart.create_line(x,top+plot_h,x,top+plot_h+4,fill=axis,width=1)
        chart.create_text(x,top+plot_h+9,anchor='n',text=_date_label(points[idx][0]),fill=label,font=('Helvetica',9))

    coords=[]
    for i,(_,rate) in enumerate(points):
        x=left+plot_w/2 if count==1 else left+i*plot_w/(count-1)
        y=top+(high-float(rate))/span*plot_h
        coords.extend((x,y))
    final=rates[-1]
    color=app.GREEN if final>0 else app.RED if final<0 else app.MUTED
    if len(coords)>=4:
        chart.create_line(*coords,fill=color,width=2,smooth=False)
    x_last=coords[-2]; y_last=coords[-1]
    chart.create_oval(x_last-3,y_last-3,x_last+3,y_last+3,fill=color,outline=color)
    chart.create_text(left,5,anchor='nw',text=f'累计收益率 {_rate_label(final)}',fill=color,font=('Helvetica',10,'bold'))
    chart.create_text(left+plot_w,5,anchor='ne',text=f'基准：策略资金预算 {capital:,.2f} USDT',fill=label,font=('Helvetica',9))
    chart.create_text(8,top,anchor='nw',text='收益率',fill=label,font=('Helvetica',9))
    chart.create_text(left+plot_w,top+plot_h+24,anchor='se',text='日期',fill=label,font=('Helvetica',9))


def apply():
    if getattr(engine.Engine,'_kaytrade_v141_history_chart_applied',False):
        return
    previous_emit=app.App.emit

    def emit(self,kind,data):
        if kind=='history' and isinstance(data,dict):
            capital=_capital_for_chart(self)
            self._v141_history_return_capital=capital
            self._v141_history_return_points=_history_points(data.get('rows') or [],capital)
        return previous_emit(self,kind,data)

    app.App.emit=emit
    app.App.draw_curve=_draw_curve
    engine.Engine._kaytrade_v141_history_chart_applied=True


apply()
