from pathlib import Path


def replace_if_present(path, old, new):
    p=Path(path); text=p.read_text()
    if old in text:
        p.write_text(text.replace(old,new,1))
        return True
    return False

# visual.py may already contain these changes from an earlier partial UI patch.
p=Path('visual.py'); text=p.read_text()
if 'class WideScrollbar(tk.Canvas):' not in text:
    marker='\n\nclass ScoreTable(tk.Frame):\n'
    if marker not in text: raise SystemExit('ScoreTable marker not found')
    custom=r'''

class WideScrollbar(tk.Canvas):
    """Fixed-width dark scrollbar; independent of native Aqua ttk sizing."""
    def __init__(self,parent,command=None,width=20,**kwargs):
        self.command=command
        self.bar_width=int(width)
        self.first=0.0; self.last=1.0
        self.drag_y=None; self.drag_first=0.0
        try: parent_bg=parent.cget('bg')
        except Exception: parent_bg=BG
        super().__init__(parent,width=self.bar_width,bg=parent_bg,highlightthickness=0,borderwidth=0,takefocus=0,**kwargs)
        self.configure(width=self.bar_width)
        self.bind('<Configure>',lambda event:self._draw())
        self.bind('<ButtonPress-1>',self._press)
        self.bind('<B1-Motion>',self._drag)
        self.bind('<ButtonRelease-1>',self._release)
        self._draw()

    def set(self,first,last):
        try:
            self.first=max(0.0,min(1.0,float(first))); self.last=max(self.first,min(1.0,float(last)))
        except Exception:
            self.first,self.last=0.0,1.0
        self._draw()

    def _bounds(self):
        h=max(1,self.winfo_height()); margin=3; track=max(1,h-2*margin)
        span=max(0.0,min(1.0,self.last-self.first)); thumb=min(track,max(36.0,track*span))
        travel=max(0.0,track-thumb); max_first=max(1e-9,1.0-span)
        y1=margin+(travel*(self.first/max_first) if travel else 0)
        return y1,y1+thumb,track,thumb,span

    def _draw(self):
        if not self.winfo_exists():return
        self.delete('all'); w=max(self.bar_width,self.winfo_width()); h=max(1,self.winfo_height())
        self.create_rectangle(2,0,w-2,h,fill='#18242b',outline='')
        y1,y2,_,_,_=self._bounds(); self.create_rectangle(4,y1,w-4,y2,fill='#4b616c',outline='')

    def _press(self,event):
        y1,y2,_,_,_=self._bounds()
        if y1<=event.y<=y2:self.drag_y=event.y; self.drag_first=self.first
        elif self.command:self.command('scroll',-1 if event.y<y1 else 1,'pages')

    def _drag(self,event):
        if self.drag_y is None or not self.command:return
        _,_,track,thumb,span=self._bounds(); travel=max(1.0,track-thumb); max_first=max(0.0,1.0-span)
        fraction=max(0.0,min(max_first,self.drag_first+(event.y-self.drag_y)/travel*max_first))
        self.command('moveto',fraction)

    def _release(self,event):self.drag_y=None
'''
    p.write_text(text.replace(marker,custom+marker,1))

text=p.read_text()
if "self.canvas.bind('<MouseWheel>',self._on_mousewheel)" not in text:
    old="        self.canvas.bind('<Configure>',lambda event:self._redraw_rows())\n\n    def heading(self,key,text='',anchor='w',**kwargs):\n"
    new="        self.canvas.bind('<Configure>',lambda event:self._redraw_rows())\n        self.canvas.bind('<MouseWheel>',self._on_mousewheel)\n        self.canvas.bind('<Button-4>',lambda event:self.canvas.yview_scroll(-1,'units'))\n        self.canvas.bind('<Button-5>',lambda event:self.canvas.yview_scroll(1,'units'))\n\n    def _on_mousewheel(self,event):\n        delta=getattr(event,'delta',0)\n        if delta:\n            units=-1 if delta>0 else 1\n            if abs(delta)>=120: units=int(-delta/120)\n            self.canvas.yview_scroll(units,'units')\n        return 'break'\n\n    def heading(self,key,text='',anchor='w',**kwargs):\n"
    if not replace_if_present('visual.py',old,new): raise SystemExit('ScoreTable wheel hook token not found')

# app.py: actually use the custom scrollbar in both detail panes.
replace_if_present('app.py',
"from visual import theme, Card, Tabs, mark, RoundedButton, RoundedEntry, RoundedCombobox, AnimatedScoreBar, ScoreTable, label as card_label, BG, PANEL, PANEL_ALT, FIELD, MUTED, GREEN, RED\n",
"from visual import theme, Card, Tabs, mark, RoundedButton, RoundedEntry, RoundedCombobox, AnimatedScoreBar, ScoreTable, WideScrollbar, label as card_label, BG, PANEL, PANEL_ALT, FIELD, MUTED, GREEN, RED\n")
replace_if_present('app.py',
"        score_scroll=ttk.Scrollbar(score_tab,orient='vertical',command=self.score_table.yview)\n        self.score_table.configure(yscrollcommand=score_scroll.set); score_scroll.pack(side='right',fill='y')\n",
"        self.score_scroll=WideScrollbar(score_tab,command=self.score_table.yview,width=20)\n        self.score_table.configure(yscrollcommand=self.score_scroll.set); self.score_scroll.pack(side='right',fill='y',padx=(5,0))\n")
replace_if_present('app.py',
"        scroll=ttk.Scrollbar(indicator_tab,orient='vertical',command=self.matrix.yview)\n        self.matrix.configure(yscrollcommand=scroll.set); scroll.pack(side='right',fill='y')\n",
"        self.indicator_scroll=WideScrollbar(indicator_tab,command=self.matrix.yview,width=20)\n        self.matrix.configure(yscrollcommand=self.indicator_scroll.set); self.indicator_scroll.pack(side='right',fill='y',padx=(5,0))\n")
app=Path('app.py').read_text()
for token in ('WideScrollbar','self.score_scroll=WideScrollbar','self.indicator_scroll=WideScrollbar'):
    if token not in app: raise SystemExit('App custom scrollbar wiring missing: '+token)

# Packaged UI smoke test must prove both actual width and score-table y movement.
d=Path('desktop.py'); text=d.read_text()
if "Score detail table did not scroll" not in text:
    old="                assert int(ui.score_bars['做空'].cget('highlightthickness')) == 0\n"
    new="                assert int(ui.score_bars['做空'].cget('highlightthickness')) == 0\n                assert ui.score_scroll.winfo_width() >= 18\n                assert ui.indicator_scroll.winfo_width() >= 18\n                extra=[]\n                for i in range(24): extra.append(ui.score_table.insert('','end',text=f'滚动测试 {i}',values=(0,0,0)))\n                root.update(); ui.score_table.yview('moveto',1.0); root.update()\n                assert ui.score_table.yview()[0] > 0, 'Score detail table did not scroll'\n                ui.score_table.yview('moveto',0.0)\n                for iid in extra: ui.score_table.delete(iid)\n"
    if old not in text: raise SystemExit('desktop smoke insertion token not found')
    d.write_text(text.replace(old,new,1))

print('V1.3.2 functional custom scrollbars fully wired')
