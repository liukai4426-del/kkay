from pathlib import Path

p = Path('visual.py')
v = p.read_text()

old_card = '''    def resize(self,event):
        w,h=event.width,event.height
        self.delete('surface'); self.delete('shadow'); self.delete('shine')
        r=min(20,max(10,h//3))
        shadow=[r+3,5,w-r+1,5,w-1,5,w-1,r+3,w-1,h-r,w-1,h-1,w-r+1,h-1,r+3,h-1,3,h-1,3,h-r,3,r+3,3,5]
        points=[r,1,w-r,1,w-1,1,w-1,r,w-1,h-r,w-1,h-1,w-r,h-1,r,h-1,1,h-1,1,h-r,1,r,1,1]
        self.create_polygon(shadow,smooth=True,splinesteps=24,fill='#05090c',outline='',tags='shadow')
        self.create_polygon(points,smooth=True,splinesteps=24,fill=self.fill,outline='#22323a',width=1,tags='surface')
        self.create_line(r+4,3,max(r+5,w-r-4),3,fill='#2a3b43',width=1,tags='shine')
        self.tag_lower('shadow'); self.tag_lower('surface'); self.tag_raise('shine')
        self.itemconfigure(self.window,width=max(1,w-36),height=max(1,h-32))
'''
new_card = '''    def resize(self,event):
        w,h=event.width,event.height
        self.delete('surface')
        r=min(20,max(8,h//3))
        points=[r,1,w-r,1,w-1,1,w-1,r,w-1,h-r,w-1,h-1,w-r,h-1,r,h-1,1,h-1,1,h-r,1,r,1,1]
        self.create_polygon(points,smooth=True,splinesteps=24,fill=self.fill,outline='',tags='surface')
        self.tag_lower('surface')
        self.itemconfigure(self.window,width=max(1,w-36),height=max(1,h-32))
'''
if old_card not in v:
    raise SystemExit('Current V1.3.3 glass Card block not found; refusing unsafe patch')
v = v.replace(old_card, new_card, 1)

old_palettes = """    PALETTES={
        'neutral':('#1a2931','#223640','#2a414c',TEXT),
        'accent':('#0b7f61','#0da97d','#096f55','#ecfff8'),
        'danger':('#8b3347','#aa4056','#712b3b','#fff1f4'),
    }
"""
new_palettes = """    PALETTES={
        'neutral':('#1a2931','#223640','#2a414c',TEXT),
        'accent':('#0b7f61','#0da97d','#096f55','#ecfff8'),
        'danger':('#8b3347','#aa4056','#712b3b','#fff1f4'),
        # Page tabs: unselected tabs visually return to the flat V1.3.2 header,
        # while only the selected tab gets a rounded highlight.
        'tab':(BG,'#10181e','#10181e',MUTED),
        'tab_active':('#15382e','#184338','#12352d',GREEN),
    }
"""
if old_palettes not in v:
    raise SystemExit('RoundedButton palette block not found')
v = v.replace(old_palettes, new_palettes, 1)

old_tabs = '''class Tabs(tk.Frame):
    """Rounded page navigation avoids Aqua ttk notebook artifacts."""
    def __init__(self,parent,**kwargs):
        super().__init__(parent,bg=BG,bd=0,highlightthickness=0,**kwargs)
        self.nav=tk.Frame(self,bg=BG,bd=0,highlightthickness=0)
        self.nav.pack(fill='x',pady=(0,10))
        self.pages=[]; self.buttons=[]; self.active=None

    def add(self,page,text):
        index=len(self.pages); self.pages.append(page)
        button=RoundedButton(self.nav,text=text,command=lambda i=index:self.select(i),variant='neutral',
                             width=max(108,52+len(text)*18),height=38,radius=12,font=('Helvetica',11,'bold'))
        button.pack(side='left',padx=(0,7))
        self.buttons.append(button)
        if self.active is None:self.select(index)

    def select(self,page=None):
        if page is None:return str(self.pages[self.active])
        index=page if isinstance(page,int) else self.pages.index(page)
        for p in self.pages:p.pack_forget()
        self.pages[index].pack(fill='both',expand=True); self.pages[index].lift(); self.active=index
        for i,b in enumerate(self.buttons):b.configure(variant='accent' if i==index else 'neutral')
        self.event_generate('<<NotebookTabChanged>>')
        self.after_idle(lambda:self.repaint(self.pages[index]))

    def repaint(self,widget):
        widget.event_generate('<Expose>',when='tail')
        for child in widget.winfo_children():self.repaint(child)
'''
new_tabs = '''class Tabs(tk.Frame):
    """V1.3.2-style flat page navigation with a rounded highlight only on the selected tab."""
    def __init__(self,parent,**kwargs):
        super().__init__(parent,bg=BG,bd=0,highlightthickness=0,**kwargs)
        self.nav=tk.Frame(self,bg=BG,bd=0,highlightthickness=0)
        self.nav.pack(fill='x',pady=(0,10))
        self.pages=[]; self.buttons=[]; self.active=None

    def add(self,page,text):
        index=len(self.pages); self.pages.append(page)
        button=RoundedButton(self.nav,text=text,command=lambda i=index:self.select(i),variant='tab',
                             width=max(108,52+len(text)*18),height=38,radius=12,font=('Helvetica',11,'bold'))
        button.pack(side='left',padx=(0,7))
        self.buttons.append(button)
        if self.active is None:self.select(index)

    def select(self,page=None):
        if page is None:return str(self.pages[self.active])
        index=page if isinstance(page,int) else self.pages.index(page)
        for p in self.pages:p.pack_forget()
        self.pages[index].pack(fill='both',expand=True); self.pages[index].lift(); self.active=index
        for i,b in enumerate(self.buttons):b.configure(variant='tab_active' if i==index else 'tab')
        self.event_generate('<<NotebookTabChanged>>')
        self.after_idle(lambda:self.repaint(self.pages[index]))

    def repaint(self,widget):
        widget.event_generate('<Expose>',when='tail')
        for child in widget.winfo_children():self.repaint(child)
'''
if old_tabs not in v:
    raise SystemExit('Current V1.3.3 Tabs block not found')
v = v.replace(old_tabs, new_tabs, 1)

p.write_text(v)

release = Path('RELEASE-1.3.3.md')
if release.exists():
    text = release.read_text()
    text = text.replace(
        '- 卡片、页签与输入控件统一圆角；信息卡增加克制的深色玻璃层次。',
        '- 主要面板恢复V1.3.2平整深色样式；输入控件保持圆角，当前选中页签使用圆角高亮。'
    )
    release.write_text(text)

# Strict post-patch invariants: execution/strategy files are untouched and glass-only Card paint is gone.
out = p.read_text()
assert "fill='#05090c'" not in out
assert "outline='#22323a'" not in out
assert "tags='shine'" not in out
assert "variant='tab_active' if i==index else 'tab'" in out
assert "class ScrollablePage" in out
print('V1.3.3 flat-style visual patch prepared')
