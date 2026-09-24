from pathlib import Path


def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit(f'missing patch target: {label}')
    return text.replace(old, new, 1)


app_path = Path('app.py')
visual_path = Path('visual.py')
build_path = Path('.github/workflows/build-mac.yml')

app = app_path.read_text()
visual = visual_path.read_text()
build = build_path.read_text()

# --- app.py: V1.3.1 visual-only wiring ---
app = replace_once(
    app,
    "from visual import theme, Card, Tabs, mark, RoundedButton, label as card_label, BG, PANEL, PANEL_ALT, FIELD, MUTED, GREEN, RED",
    "from visual import theme, Card, Tabs, mark, RoundedButton, RoundedEntry, RoundedCombobox, AnimatedScoreBar, ScoreTable, label as card_label, BG, PANEL, PANEL_ALT, FIELD, MUTED, GREEN, RED",
    'visual imports',
)
app = replace_once(
    app,
    "self.root.title('KAYTRADE 1.3 · BTC 策略控制台'); self.root.geometry('1200x920'); self.root.minsize(1040,840)",
    "self.root.title('KAYTRADE 1.3.1 · BTC 策略控制台'); self.root.geometry('1200x920'); self.root.minsize(1040,840)",
    'window version',
)
app = replace_once(
    app,
    "ttk.Label(brand,text='BTC / USDT   ·   V1.3 分层结构策略版',style='Muted.TLabel').pack(anchor='w')",
    "ttk.Label(brand,text='BTC / USDT   ·   V1.3.1 视觉优化 · V1.3 分层结构策略',style='Muted.TLabel').pack(anchor='w')",
    'brand subtitle',
)
app = replace_once(
    app,
    "widget=ttk.Combobox(connection,textvariable=var,values=values,state='readonly',width=48) if values else ttk.Entry(connection,textvariable=var,show='•',width=50)",
    "widget=RoundedCombobox(connection,textvariable=var,values=values,width=48) if values else RoundedEntry(connection,textvariable=var,show='•',width=50)",
    'connection rounded fields',
)
app = replace_once(
    app,
    "tk.Entry(risk,textvariable=v,width=12,bg=FIELD,fg='#e5eef5',insertbackground='#e5eef5',font=('Helvetica',14),highlightthickness=0,bd=0,relief='flat').grid(row=row,column=col+1,padx=8,pady=10,ipady=8)",
    "RoundedEntry(risk,textvariable=v,width=12,height=38,font=('Helvetica',14)).grid(row=row,column=col+1,padx=8,pady=10)",
    'risk rounded entries',
)
app = replace_once(
    app,
    "self.score_bars[side]=ttk.Progressbar(card,maximum=18,style=('Long' if side=='做多' else 'Short')+'.Horizontal.TProgressbar'); self.score_bars[side].pack(fill='x',pady=5)",
    "self.score_bars[side]=AnimatedScoreBar(card,maximum=18,color=color,height=9); self.score_bars[side].pack(fill='x',pady=7)",
    'animated score bars',
)
app = replace_once(
    app,
    "self.score_table=ttk.Treeview(score_tab,columns=('long','short','max'),show='tree headings',height=8)",
    "self.score_table=ScoreTable(score_tab,columns=('long','short','max'),show='tree headings',height=8)",
    'colored score table',
)
app = replace_once(
    app,
    "selector=ttk.Combobox(filterbar,textvariable=self.log_filter,values=('全部','警报'),state='readonly',width=10)",
    "selector=RoundedCombobox(filterbar,textvariable=self.log_filter,values=('全部','警报'),width=10,height=34)",
    'rounded log selector',
)

# --- visual.py: darker rounded controls, cell-specific score colors, animated score energy bars ---
visual = replace_once(
    visual,
    "    s.configure('Vertical.TScrollbar',background='#26353d',troughcolor=PANEL,borderwidth=0,arrowsize=10,relief='flat')",
    "    s.layout('Vertical.TScrollbar',[('Vertical.Scrollbar.trough',{'sticky':'ns','children':[('Vertical.Scrollbar.thumb',{'expand':'1','sticky':'nswe'})]})])\n    s.configure('Vertical.TScrollbar',background='#31424b',troughcolor=PANEL,borderwidth=0,arrowsize=0,relief='flat',width=9)\n    s.map('Vertical.TScrollbar',background=[('active','#40545f'),('pressed','#4b616c')])",
    'dark scrollbar layout',
)

components = r'''

class RoundedEntry(tk.Canvas):
    """Dark rounded input field that avoids native white field chrome."""
    def __init__(self,parent,textvariable=None,show=None,width=20,height=38,font=None,**kwargs):
        self.variable=textvariable or tk.StringVar()
        self.radius=12
        self.field_height=height
        self.pixel_width=width if isinstance(width,(int,float)) and width>80 else max(112,int(width)*8+34)
        try: parent_bg=parent.cget('bg')
        except Exception: parent_bg=BG
        super().__init__(parent,width=self.pixel_width,height=height,bg=parent_bg,highlightthickness=0,borderwidth=0,**kwargs)
        self.entry=tk.Entry(self,textvariable=self.variable,show=show or '',bg=FIELD,fg=TEXT,
                            insertbackground=TEXT,selectbackground='#275448',selectforeground=TEXT,
                            font=font or ('Helvetica',13),bd=0,relief='flat',highlightthickness=0)
        self.window=self.create_window(14,height/2,anchor='w',window=self.entry,height=max(18,height-12))
        self.entry.bind('<FocusIn>',lambda event:self._draw(True))
        self.entry.bind('<FocusOut>',lambda event:self._draw(False))
        self.bind('<Configure>',lambda event:self._resize())
        self._focused=False
        self._draw(False)

    def _shape(self,x1,y1,x2,y2,r,**kwargs):
        points=[x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
        return self.create_polygon(points,smooth=True,splinesteps=24,**kwargs)

    def _draw(self,focused=None):
        if focused is not None:self._focused=focused
        self.delete('surface')
        w=max(2,self.winfo_width() or self.pixel_width); h=max(2,self.winfo_height() or self.field_height)
        fill='#20333d' if self._focused else FIELD
        self._shape(1,1,w-1,h-1,min(self.radius,h//2-1),fill=fill,outline='',tags='surface')
        self.tag_lower('surface')
        self.entry.configure(bg=fill)

    def _resize(self):
        w=max(30,self.winfo_width())
        self.itemconfigure(self.window,width=max(20,w-28))
        self._draw()

    def get(self):return self.entry.get()
    def focus_set(self):return self.entry.focus_set()


class RoundedCombobox(tk.Canvas):
    """Rounded readonly option field with a dark popup and no native white arrow block."""
    def __init__(self,parent,textvariable=None,values=(),width=20,height=38,**kwargs):
        self.variable=textvariable or tk.StringVar()
        self.values=tuple(values)
        self.radius=12
        self.field_height=height
        self.pixel_width=width if isinstance(width,(int,float)) and width>80 else max(118,int(width)*8+42)
        self.hover=False
        try: parent_bg=parent.cget('bg')
        except Exception: parent_bg=BG
        super().__init__(parent,width=self.pixel_width,height=height,bg=parent_bg,highlightthickness=0,borderwidth=0,cursor='hand2',takefocus=1,**kwargs)
        self.bind('<Enter>',lambda event:self._set_hover(True))
        self.bind('<Leave>',lambda event:self._set_hover(False))
        self.bind('<Button-1>',self._popup)
        self.bind('<Return>',self._popup)
        self.bind('<space>',self._popup)
        self.bind('<Configure>',lambda event:self._draw())
        try:self.variable.trace_add('write',lambda *args:self._draw())
        except Exception:pass
        self._draw()

    def _shape(self,x1,y1,x2,y2,r,**kwargs):
        points=[x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
        return self.create_polygon(points,smooth=True,splinesteps=24,**kwargs)

    def _set_hover(self,value):
        self.hover=value; self._draw()

    def _draw(self):
        self.delete('all')
        w=max(2,self.winfo_width() or self.pixel_width); h=max(2,self.winfo_height() or self.field_height)
        fill='#20333d' if self.hover else FIELD
        self._shape(1,1,w-1,h-1,min(self.radius,h//2-1),fill=fill,outline='')
        self.create_text(14,h/2,text=self.variable.get(),fill=TEXT,font=('Helvetica',12),anchor='w')
        x=w-18; y=h/2
        self.create_line(x-5,y-2,x,y+3,x+5,y-2,fill=MUTED,width=2,capstyle='round',joinstyle='round')

    def _popup(self,event=None):
        self.focus_set()
        menu=tk.Menu(self,tearoff=0,bg=PANEL_ALT,fg=TEXT,activebackground='#244039',activeforeground=TEXT,
                     bd=0,relief='flat',font=('Helvetica',12))
        for value in self.values:
            menu.add_command(label=value,command=lambda v=value:self._select(v))
        try:menu.tk_popup(self.winfo_rootx(),self.winfo_rooty()+self.winfo_height())
        finally:menu.grab_release()

    def _select(self,value):
        self.variable.set(value)
        self.event_generate('<<ComboboxSelected>>')

    def get(self):return self.variable.get()


class AnimatedScoreBar(tk.Canvas):
    """Rounded score energy bar with smooth value motion and a subtle moving sheen."""
    def __init__(self,parent,maximum=18,color=GREEN,height=9,**kwargs):
        self.maximum=max(float(maximum),1.0)
        self.color=color
        self.track='#202b32'
        self.current=0.0
        self.target=0.0
        self.phase=0.0
        self.bar_height=height
        try: parent_bg=parent.cget('bg')
        except Exception: parent_bg=PANEL
        super().__init__(parent,height=height,bg=parent_bg,highlightthickness=0,borderwidth=0,**kwargs)
        self.bind('<Configure>',lambda event:self._draw())
        self.after(70,self._tick)

    def _shape(self,x1,y1,x2,y2,r,**kwargs):
        if x2<=x1:return None
        points=[x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
        return self.create_polygon(points,smooth=True,splinesteps=18,**kwargs)

    def _draw(self):
        self.delete('all')
        w=max(2,self.winfo_width()); h=max(4,self.winfo_height() or self.bar_height); r=max(2,h/2)
        self._shape(0,0,w,h,r,fill=self.track,outline='')
        ratio=max(0.0,min(1.0,self.current/self.maximum))
        end=w*ratio
        if end>2:
            self._shape(0,0,end,h,min(r,end/2),fill=self.color,outline='')
            # Low-frequency loading sheen. It stays inside the colored portion and never flashes white.
            sheen='#54efc0' if self.color==GREEN else '#ff9aae'
            sx=2+(max(end-6,1)*self.phase)
            self.create_line(sx,2,sx,max(2,h-2),fill=sheen,width=max(1,int(h/3)),capstyle='round')

    def _tick(self):
        delta=self.target-self.current
        if abs(delta)>0.015:
            self.current += delta*0.22
        else:
            self.current=self.target
        self.phase=(self.phase+0.035)%1.0
        if self.winfo_exists():
            self._draw(); self.after(70,self._tick)

    def set_value(self,value):
        try:self.target=max(0.0,min(self.maximum,float(value)))
        except Exception:self.target=0.0

    def __setitem__(self,key,value):
        if key=='value':self.set_value(value)
        else:super().__setitem__(key,value)

    def __getitem__(self,key):
        if key=='value':return self.target
        return super().__getitem__(key)


class ScoreTable(tk.Frame):
    """Score detail grid with direction-aware cell colors; API mirrors the Treeview calls used by App."""
    def __init__(self,parent,columns=(),show=None,height=8,**kwargs):
        try: parent_bg=parent.cget('bg')
        except Exception: parent_bg=BG
        super().__init__(parent,bg=parent_bg,bd=0,highlightthickness=0,**kwargs)
        self.columns=('#0',)+tuple(columns)
        self.titles={key:key for key in self.columns}
        self.widths={'#0':300,**{key:130 for key in columns}}
        self.rows=[]
        self._counter=0
        self.header=tk.Canvas(self,height=38,bg=PANEL_ALT,highlightthickness=0,borderwidth=0)
        self.header.pack(fill='x')
        self.canvas=tk.Canvas(self,height=max(1,height)*29,bg=PANEL,highlightthickness=0,borderwidth=0)
        self.canvas.pack(fill='both',expand=True)
        self.header.bind('<Configure>',lambda event:self._redraw_header())
        self.canvas.bind('<Configure>',lambda event:self._redraw_rows())

    def heading(self,key,text='',anchor='w',**kwargs):
        self.titles[key]=text; self._redraw_header()

    def column(self,key,width=130,anchor='w',**kwargs):
        self.widths[key]=width; self._redraw_header(); self._redraw_rows()

    def configure(self,cnf=None,**kwargs):
        if cnf:kwargs.update(cnf)
        yscroll=kwargs.pop('yscrollcommand',None)
        if yscroll is not None:self.canvas.configure(yscrollcommand=yscroll)
        if kwargs:return super().configure(**kwargs)
    config=configure

    def yview(self,*args):return self.canvas.yview(*args)

    def get_children(self,*args):return tuple(row['iid'] for row in self.rows)

    def delete(self,*items):
        wanted=set(items)
        self.rows=[row for row in self.rows if row['iid'] not in wanted]
        self._redraw_rows()

    def insert(self,parent,index,iid=None,text='',values=(),tags=(),**kwargs):
        self._counter+=1
        iid=iid or f'S{self._counter:04d}'
        self.rows.append({'iid':iid,'text':text,'values':tuple(values)})
        self._redraw_rows()
        return iid

    def _positions(self):
        x=0; out=[]
        for key in self.columns:
            width=self.widths.get(key,130); out.append((key,x,width)); x+=width
        return out,x

    def _redraw_header(self):
        if not self.header.winfo_exists():return
        self.header.delete('all')
        for key,x,width in self._positions()[0]:
            self.header.create_text(x+9,19,text=self.titles.get(key,key),fill=MUTED,font=('Helvetica',11,'bold'),anchor='w')

    @staticmethod
    def _score_color(value,side):
        try:number=float(value)
        except Exception:return TEXT
        if abs(number)<1e-12:return MUTED
        if side=='long':return GREEN if number>0 else RED
        if side=='short':return RED if number>0 else GREEN
        return TEXT

    def _redraw_rows(self):
        if not self.canvas.winfo_exists():return
        self.canvas.delete('all')
        positions,total_width=self._positions(); row_h=29
        for i,row in enumerate(self.rows):
            y=i*row_h
            if i%2:self.canvas.create_rectangle(0,y,max(total_width,self.canvas.winfo_width()),y+row_h,fill=PANEL_ALT,outline='')
            cells=(row['text'],)+row['values']
            for idx,(key,x,width) in enumerate(positions):
                value=cells[idx] if idx<len(cells) else ''
                color=self._score_color(value,key) if key in ('long','short') else TEXT if key=='#0' else MUTED
                self.canvas.create_text(x+9,y+row_h/2,text=str(value),fill=color,font=('Helvetica',12),anchor='w')
        content_h=max(self.canvas.winfo_height(),len(self.rows)*row_h)
        self.canvas.configure(scrollregion=(0,0,max(total_width,self.canvas.winfo_width()),content_h))
'''
visual = replace_once(visual, "\ndef mark(parent):", components + "\n\ndef mark(parent):", 'visual components')

# --- build workflow: isolate V1.3.1 artifact and retain all V1.3 regression tests ---
build = build.replace('Build KAYTRADE Intel Mac 1.3', 'Build KAYTRADE Intel Mac 1.3.1', 1)
build = build.replace('      - codex/v1-3-strategy', '      - codex/v1-3-1-visual', 1)
build = build.replace('Test V1.3 strategy, structure, execution and safety regressions', 'Test V1.3 strategy/safety plus V1.3.1 visual regressions', 1)
build = build.replace("          cp RELEASE-1.3.md installer/RELEASE-1.3.md", "          cp RELEASE-1.3.md installer/RELEASE-1.3.md\n          cp RELEASE-1.3.1.md installer/RELEASE-1.3.1.md", 1)
build = build.replace('hdiutil create -volname KAYTRADE-1.3 -srcfolder installer -ov -format UDZO KAYTRADE-1.3-Intel-test.dmg', 'hdiutil create -volname KAYTRADE-1.3.1 -srcfolder installer -ov -format UDZO KAYTRADE-1.3.1-Intel-test.dmg', 1)
build = build.replace('shasum -a 256 KAYTRADE-1.3-Intel-test.dmg > SHA256.txt', 'shasum -a 256 KAYTRADE-1.3.1-Intel-test.dmg > SHA256.txt', 1)
build = build.replace('name: KAYTRADE-1.3-Intel-test', 'name: KAYTRADE-1.3.1-Intel-test', 1)
build = build.replace('KAYTRADE-1.3-Intel-test.dmg', 'KAYTRADE-1.3.1-Intel-test.dmg', 1)

# Add a source-level V1.3.1 visual smoke check before packaging.
needle = "      - name: Build KAYTRADE Intel application\n        run: python -m PyInstaller --noconfirm OKXLocal.spec"
visual_test = "      - name: Verify V1.3.1 visual-only UI wiring\n        run: |\n          python -m py_compile app.py visual.py\n          python - <<'PY'\n          from pathlib import Path\n          a=Path('app.py').read_text(); v=Path('visual.py').read_text()\n          for token in ('ScoreTable(', 'AnimatedScoreBar(', 'RoundedEntry(', 'RoundedCombobox(', 'KAYTRADE 1.3.1'):\n              assert token in a, token\n          for token in ('class ScoreTable', 'class AnimatedScoreBar', 'class RoundedEntry', 'class RoundedCombobox'):\n              assert token in v, token\n          assert \"if side=='long':return GREEN if number>0 else RED\" in v\n          assert \"if side=='short':return RED if number>0 else GREEN\" in v\n          print('V1.3.1 visual wiring OK')\n          PY\n      - name: Build KAYTRADE Intel application\n        run: python -m PyInstaller --noconfirm OKXLocal.spec"
build = replace_once(build, needle, visual_test, 'build visual smoke')

release = '''# KAYTRADE V1.3.1 — Visual UI Update\n\nVisual-only update based on KAYTRADE V1.3. Trading strategy, scoring rules, order execution and fail-closed risk controls are unchanged.\n\n## Changes\n- Score detail colors are direction-aware: long positive = green, long negative = red; short positive = red, short negative = green; zero = neutral.\n- Input fields and option controls use dark rounded surfaces instead of native white blocks.\n- Vertical scrollbars use a dark thumb/trough without white arrow blocks.\n- Long/short score energy bars have no white outline and use smooth value transitions plus a subtle moving sheen.\n\n## Safety\n- No V1.3 strategy thresholds, indicators, position sizing, order routing, TP/SL, reconciliation, candle safety, network fail-closed logic or China-day risk guards were changed.\n- Test in OKX Demo before live use.\n'''

app_path.write_text(app)
visual_path.write_text(visual)
build_path.write_text(build)
Path('RELEASE-1.3.1.md').write_text(release)
print('V1.3.1 visual patch applied')
