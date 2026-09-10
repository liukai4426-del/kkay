from pathlib import Path


def replace(path, old, new):
    p=Path(path)
    text=p.read_text()
    if old not in text:
        raise SystemExit(f'missing token in {path}: {old[:100]!r}')
    p.write_text(text.replace(old,new,1))

# visual.py: use a real Canvas scrollbar so macOS/Aqua cannot collapse its width.
p=Path('visual.py')
text=p.read_text()
marker='\n\nclass ScoreTable(tk.Frame):\n'
if marker not in text:
    raise SystemExit('ScoreTable marker not found')
if 'class WideScrollbar(tk.Canvas):' not in text:
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
        super().__init__(parent,width=self.bar_width,bg=parent_bg,highlightthickness=0,
                         borderwidth=0,takefocus=0,**kwargs)
        self.configure(width=self.bar_width)
        self.bind('<Configure>',lambda event:self._draw())
        self.bind('<ButtonPress-1>',self._press)
        self.bind('<B1-Motion>',self._drag)
        self.bind('<ButtonRelease-1>',self._release)
        self._draw()

    def set(self,first,last):
        try:
            self.first=max(0.0,min(1.0,float(first)))
            self.last=max(self.first,min(1.0,float(last)))
        except Exception:
            self.first,self.last=0.0,1.0
        self._draw()

    def _bounds(self):
        h=max(1,self.winfo_height())
        margin=3
        track=max(1,h-2*margin)
        span=max(0.0,min(1.0,self.last-self.first))
        thumb=max(36.0,track*span)
        thumb=min(track,thumb)
        travel=max(0.0,track-thumb)
        max_first=max(1e-9,1.0-span)
        y1=margin+(travel*(self.first/max_first) if travel else 0)
        return y1,y1+thumb,track,thumb,span

    def _draw(self):
        if not self.winfo_exists():return
        self.delete('all')
        w=max(self.bar_width,self.winfo_width()); h=max(1,self.winfo_height())
        self.create_rectangle(2,0,w-2,h,fill='#18242b',outline='')
        y1,y2,_,_,_=self._bounds()
        self.create_rectangle(4,y1,w-4,y2,fill='#4b616c',outline='')

    def _press(self,event):
        y1,y2,_,_,_=self._bounds()
        if y1<=event.y<=y2:
            self.drag_y=event.y; self.drag_first=self.first
        elif self.command:
            self.command('scroll',-1 if event.y<y1 else 1,'pages')

    def _drag(self,event):
        if self.drag_y is None or not self.command:return
        y1,y2,track,thumb,span=self._bounds()
        travel=max(1.0,track-thumb)
        max_first=max(0.0,1.0-span)
        fraction=max(0.0,min(max_first,self.drag_first+(event.y-self.drag_y)/travel*max_first))
        self.command('moveto',fraction)

    def _release(self,event):
        self.drag_y=None
'''
    text=text.replace(marker,custom+marker,1)
p.write_text(text)

# ScoreTable: trackpad/mouse-wheel scrolling is required because it is a custom Canvas table.
replace('visual.py',
"        self.canvas.bind('<Configure>',lambda event:self._redraw_rows())\n\n    def heading(self,key,text='',anchor='w',**kwargs):\n",
"        self.canvas.bind('<Configure>',lambda event:self._redraw_rows())\n        self.canvas.bind('<MouseWheel>',self._on_mousewheel)\n        self.canvas.bind('<Button-4>',lambda event:self.canvas.yview_scroll(-1,'units'))\n        self.canvas.bind('<Button-5>',lambda event:self.canvas.yview_scroll(1,'units'))\n\n    def _on_mousewheel(self,event):\n        delta=getattr(event,'delta',0)\n        if delta:\n            units=-1 if delta>0 else 1\n            if abs(delta)>=120: units=int(-delta/120)\n            self.canvas.yview_scroll(units,'units')\n        return 'break'\n\n    def heading(self,key,text='',anchor='w',**kwargs):\n")

# app.py: replace only the score-detail and indicator scrollbars with fixed-width custom controls.
replace('app.py',
"from visual import theme, Card, Tabs, mark, RoundedButton, RoundedEntry, RoundedCombobox, AnimatedScoreBar, ScoreTable, label as card_label, BG, PANEL, PANEL_ALT, FIELD, MUTED, GREEN, RED\n",
"from visual import theme, Card, Tabs, mark, RoundedButton, RoundedEntry, RoundedCombobox, AnimatedScoreBar, ScoreTable, WideScrollbar, label as card_label, BG, PANEL, PANEL_ALT, FIELD, MUTED, GREEN, RED\n")
replace('app.py',
"        score_scroll=ttk.Scrollbar(score_tab,orient='vertical',command=self.score_table.yview)\n        self.score_table.configure(yscrollcommand=score_scroll.set); score_scroll.pack(side='right',fill='y')\n",
"        self.score_scroll=WideScrollbar(score_tab,command=self.score_table.yview,width=20)\n        self.score_table.configure(yscrollcommand=self.score_scroll.set); self.score_scroll.pack(side='right',fill='y',padx=(5,0))\n")
replace('app.py',
"        scroll=ttk.Scrollbar(indicator_tab,orient='vertical',command=self.matrix.yview)\n        self.matrix.configure(yscrollcommand=scroll.set); scroll.pack(side='right',fill='y')\n",
"        self.indicator_scroll=WideScrollbar(indicator_tab,command=self.matrix.yview,width=20)\n        self.matrix.configure(yscrollcommand=self.indicator_scroll.set); self.indicator_scroll.pack(side='right',fill='y',padx=(5,0))\n")

# desktop.py: prove actual widget width and prove the custom score table can change its y-view.
replace('desktop.py',
"                assert int(ui.score_bars['做空'].cget('highlightthickness')) == 0\n",
"                assert int(ui.score_bars['做空'].cget('highlightthickness')) == 0\n                assert ui.score_scroll.winfo_width() >= 18\n                assert ui.indicator_scroll.winfo_width() >= 18\n                extra=[]\n                for i in range(24):\n                    extra.append(ui.score_table.insert('','end',text=f'滚动测试 {i}',values=(0,0,0)))\n                root.update()\n                ui.score_table.yview('moveto',1.0); root.update()\n                assert ui.score_table.yview()[0] > 0, 'Score detail table did not scroll'\n                ui.score_table.yview('moveto',0.0)\n                for iid in extra: ui.score_table.delete(iid)\n")

print('V1.3.2 custom wide scrollbar + score-table scrolling patch applied')
