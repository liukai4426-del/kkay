from pathlib import Path

p=Path('visual.py')
text=p.read_text()
old="""        self.canvas=tk.Canvas(self,height=max(1,height)*29,bg=PANEL,highlightthickness=0,borderwidth=0)\n        self.canvas.pack(fill='both',expand=True)\n        self.header.bind('<Configure>',lambda event:self._redraw_header())\n        self.canvas.bind('<Configure>',lambda event:self._redraw_rows())\n        self.canvas.bind('<MouseWheel>',self._on_mousewheel)\n        self.canvas.bind('<Button-4>',lambda event:self.canvas.yview_scroll(-1,'units'))\n        self.canvas.bind('<Button-5>',lambda event:self.canvas.yview_scroll(1,'units'))\n"""
new="""        self.canvas=tk.Canvas(self,height=max(1,height)*29,bg=PANEL,highlightthickness=0,borderwidth=0,yscrollincrement=29)\n        self.canvas.pack(fill='both',expand=True)\n        self.header.bind('<Configure>',lambda event:self._redraw_header())\n        self.canvas.bind('<Configure>',lambda event:self._redraw_rows())\n        # ScoreTable is custom-drawn, so it must own its scrolling instead of relying on Treeview behavior.\n        for widget in (self,self.header,self.canvas):\n            widget.bind('<MouseWheel>',self._on_mousewheel)\n            widget.bind('<Button-4>',lambda event:self._scroll_units(-1))\n            widget.bind('<Button-5>',lambda event:self._scroll_units(1))\n"""
if old not in text:
    raise SystemExit('ScoreTable canvas block not found')
text=text.replace(old,new)
old="""    def _on_mousewheel(self,event):\n        delta=getattr(event,'delta',0)\n        if delta:\n            units=-1 if delta>0 else 1\n            if abs(delta)>=120: units=int(-delta/120)\n            self.canvas.yview_scroll(units,'units')\n        return 'break'\n"""
new="""    def _scroll_units(self,units):\n        before=self.canvas.yview()\n        self.canvas.yview_scroll(int(units),'units')\n        self.canvas.update_idletasks()\n        return before!=self.canvas.yview()\n\n    def _on_mousewheel(self,event):\n        delta=getattr(event,'delta',0)\n        if delta:\n            # Aqua reports small trackpad deltas while classic wheels often report +/-120.\n            units=(-1 if delta>0 else 1) if abs(delta)<120 else int(-delta/120)\n            self._scroll_units(units)\n        return 'break'\n"""
if old not in text:
    raise SystemExit('mousewheel block not found')
text=text.replace(old,new)
old="""    def yview(self,*args):return self.canvas.yview(*args)\n"""
new="""    def yview(self,*args):\n        result=self.canvas.yview(*args)\n        self.canvas.update_idletasks()\n        return result\n\n    def yview_moveto(self,fraction):\n        self.canvas.yview_moveto(float(fraction)); self.canvas.update_idletasks()\n\n    def yview_scroll(self,number,what='units'):\n        self.canvas.yview_scroll(int(number),what); self.canvas.update_idletasks()\n"""
if old not in text:
    raise SystemExit('yview block not found')
text=text.replace(old,new)
old="""        content_h=max(self.canvas.winfo_height(),len(self.rows)*row_h)\n        self.canvas.configure(scrollregion=(0,0,max(total_width,self.canvas.winfo_width()),content_h))\n"""
new="""        # Keep the logical content height independent from the viewport height. This makes\n        # Canvas yview fractions stable on macOS and allows the custom scrollbar to move.\n        content_h=max(1,len(self.rows)*row_h)\n        self.canvas.configure(scrollregion=(0,0,max(total_width,self.canvas.winfo_width()),content_h))\n        self.canvas.update_idletasks()\n"""
if old not in text:
    raise SystemExit('scrollregion block not found')
text=text.replace(old,new)
p.write_text(text)

p=Path('desktop.py')
text=p.read_text()
needle="""                assert ui.score_vars['做多'].get().endswith('/ 10')\n"""
insert="""                assert ui.score_vars['做多'].get().endswith('/ 10')\n                # Score details are custom Canvas rows: prove that the viewport can actually move.\n                for i in range(20):\n                    ui.score_table.insert('','end',text=f'滚动测试 {i}',values=(0,0,.5))\n                root.update_idletasks()\n                top_before=ui.score_table.canvas.yview()[0]\n                ui.score_table.yview_moveto(.55)\n                root.update_idletasks()\n                top_after=ui.score_table.canvas.yview()[0]\n                assert top_after > top_before + .05, (top_before,top_after,ui.score_table.canvas.cget('scrollregion'))\n                ui.score_table.yview_moveto(0)\n"""
if needle not in text:
    raise SystemExit('desktop score assertion anchor not found')
text=text.replace(needle,insert)
p.write_text(text)
print('score scroll fix applied')
