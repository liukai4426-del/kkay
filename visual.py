"""KAYTRADE presentation components. No trading or network dependencies."""
import tkinter as tk
from tkinter import ttk

BG = '#080d10'
PANEL = '#10181e'
PANEL_ALT = '#152128'
FIELD = '#192832'
TEXT = '#eef5f7'
MUTED = '#8b9ca6'
GREEN = '#0ddb9f'
RED = '#ff637f'


def theme(root):
    root.configure(bg=BG)
    s=ttk.Style(root)
    s.theme_use('clam')
    s.configure('.',background=BG,foreground=TEXT,font=('Helvetica',13),borderwidth=0)
    s.configure('TFrame',background=BG,borderwidth=0)
    s.configure('TLabel',background=BG,foreground=TEXT,borderwidth=0)
    s.configure('Muted.TLabel',foreground=MUTED)
    s.configure('Title.TLabel',foreground=TEXT,font=('Helvetica',24,'bold'))
    # Native ttk buttons remain flat as a fallback; visible app actions use RoundedButton.
    s.configure('TButton',background=PANEL_ALT,foreground=TEXT,padding=(15,10),borderwidth=0,relief='flat')
    s.map('TButton',background=[('active','#20313a'),('pressed','#263b45')])
    s.configure('TEntry',fieldbackground=FIELD,background=FIELD,foreground=TEXT,padding=9,borderwidth=0,relief='flat')
    s.configure('TCombobox',fieldbackground=FIELD,background=FIELD,foreground=TEXT,padding=7,arrowsize=12,borderwidth=0,relief='flat')
    s.map('TCombobox',fieldbackground=[('readonly',FIELD)],background=[('readonly',FIELD)],foreground=[('readonly',TEXT)])
    s.configure('TNotebook',background=BG,borderwidth=0,tabmargins=(0,0,0,10))
    s.layout('TNotebook.Tab',[('Notebook.tab',{'sticky':'nswe','children':[('Notebook.padding',{'side':'top','sticky':'nswe','children':[('Notebook.label',{'side':'top','sticky':''})]})]})])
    s.configure('TNotebook.Tab',padding=(20,10),borderwidth=0)
    s.map('TNotebook.Tab',background=[('selected','#15382e'),('!selected',BG)],foreground=[('selected',GREEN),('!selected',MUTED)])
    s.layout('Treeview',[('Treeview.treearea',{'sticky':'nswe'})])
    s.configure('Treeview',background=PANEL,fieldbackground=PANEL,foreground=TEXT,rowheight=29,borderwidth=0,relief='flat')
    s.configure('Treeview.Heading',background=PANEL_ALT,foreground=MUTED,font=('Helvetica',11,'bold'),relief='flat',borderwidth=0,padding=8)
    s.map('Treeview',background=[('selected','#214039')],foreground=[('selected',TEXT)])
    s.layout('Vertical.TScrollbar',[('Vertical.Scrollbar.trough',{'sticky':'ns','children':[('Vertical.Scrollbar.thumb',{'expand':'1','sticky':'nswe'})]})])
    s.configure('Vertical.TScrollbar',background='#31424b',troughcolor=PANEL,borderwidth=0,arrowsize=0,relief='flat',width=16)
    s.map('Vertical.TScrollbar',background=[('active','#40545f'),('pressed','#4b616c')])
    for name,color in [('Long',GREEN),('Short',RED)]:
        s.configure(name+'.Horizontal.TProgressbar',background=color,troughcolor='#202b32',borderwidth=0,lightcolor=color,darkcolor=color,thickness=5)


class Card(tk.Canvas):
    """Rounded surface separated by tonal depth rather than outlines."""
    def __init__(self,parent,height=120,fill=PANEL,**kwargs):
        super().__init__(parent,height=height,bg=BG,highlightthickness=0,borderwidth=0,**kwargs)
        self.fill=fill
        self.body=tk.Frame(self,bg=fill,bd=0,highlightthickness=0)
        self.window=self.create_window(18,16,anchor='nw',window=self.body)
        self.bind('<Configure>',self.resize)

    def resize(self,event):
        w,h=event.width,event.height
        self.delete('surface')
        r=min(20,max(8,h//3))
        points=[r,1,w-r,1,w-1,1,w-1,r,w-1,h-r,w-1,h-1,w-r,h-1,r,h-1,1,h-1,1,h-r,1,r,1,1]
        self.create_polygon(points,smooth=True,splinesteps=24,fill=self.fill,outline='',tags='surface')
        self.tag_lower('surface')
        self.itemconfigure(self.window,width=max(1,w-36),height=max(1,h-32))



class ScrollablePage(tk.Frame):
    """Whole-page vertical scrolling for compact Mac windows; nested tables keep their own wheel events."""
    def __init__(self,parent,**kwargs):
        super().__init__(parent,bg=BG,bd=0,highlightthickness=0,**kwargs)
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0,borderwidth=0,yscrollincrement=24)
        self.scroll=WideScrollbar(self,command=self.canvas.yview,width=16)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side='right',fill='y'); self.canvas.pack(side='left',fill='both',expand=True)
        self.body=tk.Frame(self.canvas,bg=BG,bd=0,highlightthickness=0)
        self.window=self.canvas.create_window(0,0,anchor='nw',window=self.body)
        self.body.bind('<Configure>',self._sync)
        self.canvas.bind('<Configure>',self._resize)
        self.bind_all('<MouseWheel>',self._wheel,add='+')
        self.bind_all('<Button-4>',lambda event:self._linux_wheel(event,-1),add='+')
        self.bind_all('<Button-5>',lambda event:self._linux_wheel(event,1),add='+')

    def _sync(self,event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all') or (0,0,1,1))

    def _resize(self,event):
        self.canvas.itemconfigure(self.window,width=max(1,event.width))
        self._sync()

    def _nested_scroll(self,widget):
        w=widget
        while w is not None:
            if isinstance(w,(ScoreTable,ttk.Treeview,tk.Text)):
                return True
            if w is self:return False
            try:w=w.master
            except Exception:return False
        return False

    def _wheel(self,event):
        if not self.winfo_ismapped() or self._nested_scroll(getattr(event,'widget',None)):
            return
        delta=getattr(event,'delta',0)
        if not delta:return
        units=(-1 if delta>0 else 1) if abs(delta)<120 else int(-delta/120)
        self.canvas.yview_scroll(units,'units'); return 'break'

    def _linux_wheel(self,event,units):
        if self.winfo_ismapped() and not self._nested_scroll(getattr(event,'widget',None)):
            self.canvas.yview_scroll(units,'units'); return 'break'


class MetricTile(Card):
    def __init__(self,parent,title,variable=None,value='—',accent=TEXT,height=76,**kwargs):
        super().__init__(parent,height=height,fill=PANEL_ALT,**kwargs)
        label(self.body,text=title,color=MUTED,size=9).pack(anchor='w')
        label(self.body,text=value,variable=variable,color=accent,size=16,bold=True).pack(anchor='w',pady=(5,0))

def label(parent,text=None,variable=None,size=13,color=TEXT,bold=False,bg=None):
    return tk.Label(parent,text=text,textvariable=variable,bg=bg or parent.cget('bg'),fg=color,
                    font=('Helvetica',size,'bold' if bold else 'normal'),anchor='w',bd=0,highlightthickness=0)


class RoundedButton(tk.Canvas):
    """True rounded Tk button with tonal hover/pressed states and no outline."""
    PALETTES={
        'neutral':('#1a2931','#223640','#2a414c',TEXT),
        'accent':('#0b7f61','#0da97d','#096f55','#ecfff8'),
        'danger':('#8b3347','#aa4056','#712b3b','#fff1f4'),
        # Page tabs: unselected tabs visually return to the flat V1.3.2 header,
        # while only the selected tab gets a rounded highlight.
        'tab':(BG,'#10181e','#10181e',MUTED),
        'tab_active':('#15382e','#184338','#12352d',GREEN),
    }

    def __init__(self,parent,text,command=None,variant='neutral',width=None,height=42,radius=14,font=None,**kwargs):
        self.text=text; self.command=command; self.variant=variant; self.state='normal'; self.hover=False; self.pressed=False
        self.radius=radius; self.button_height=height; self.font=font or ('Helvetica',12,'bold')
        estimated=max(112,32+len(text)*14)
        self.button_width=width or estimated
        try:
            parent_bg=parent.cget('bg')
        except Exception:
            parent_bg=BG
        bg=kwargs.pop('bg',parent_bg)
        super().__init__(parent,width=self.button_width,height=height,bg=bg,highlightthickness=0,borderwidth=0,cursor='hand2',**kwargs)
        self.bind('<Enter>',self._enter); self.bind('<Leave>',self._leave)
        self.bind('<ButtonPress-1>',self._press); self.bind('<ButtonRelease-1>',self._release)
        self.bind('<Configure>',lambda event:self._draw())
        self._draw()

    def _round_rect(self,x1,y1,x2,y2,r,**kwargs):
        points=[x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
        return self.create_polygon(points,smooth=True,splinesteps=24,**kwargs)

    def _draw(self):
        self.delete('all')
        base,hover,pressed,fg=self.PALETTES.get(self.variant,self.PALETTES['neutral'])
        fill=pressed if self.pressed else hover if self.hover else base
        if self.state=='disabled': fill=PANEL_ALT; fg=MUTED
        w=max(2,self.winfo_width() or self.button_width); h=max(2,self.winfo_height() or self.button_height)
        self._round_rect(1,1,w-1,h-1,min(self.radius,h//2-1),fill=fill,outline='')
        self.create_text(w/2,h/2,text=self.text,fill=fg,font=self.font,anchor='center')

    def _enter(self,event):
        if self.state!='disabled': self.hover=True; self._draw()

    def _leave(self,event):
        self.hover=False; self.pressed=False; self._draw()

    def _press(self,event):
        if self.state!='disabled': self.pressed=True; self._draw()

    def _release(self,event):
        if self.state=='disabled': return
        active=self.pressed and 0<=event.x<=self.winfo_width() and 0<=event.y<=self.winfo_height()
        self.pressed=False; self._draw()
        if active and self.command: self.command()

    def configure(self,cnf=None,**kwargs):
        if cnf: kwargs.update(cnf)
        changed=False
        for key in ('text','command','variant','state'):
            if key in kwargs:
                setattr(self,key,kwargs.pop(key)); changed=True
        if 'width' in kwargs:
            self.button_width=kwargs['width']; changed=True
        if 'height' in kwargs:
            self.button_height=kwargs['height']; changed=True
        if kwargs: super().configure(**kwargs)
        if changed: self._draw()

    config=configure



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
        self.canvas=tk.Canvas(self,height=max(1,height)*29,bg=PANEL,highlightthickness=0,borderwidth=0,yscrollincrement=29)
        self.canvas.pack(fill='both',expand=True)
        self.header.bind('<Configure>',lambda event:self._redraw_header())
        self.canvas.bind('<Configure>',lambda event:self._redraw_rows())
        # ScoreTable is custom-drawn, so it must own its scrolling instead of relying on Treeview behavior.
        for widget in (self,self.header,self.canvas):
            widget.bind('<MouseWheel>',self._on_mousewheel)
            widget.bind('<Button-4>',lambda event:self._scroll_units(-1))
            widget.bind('<Button-5>',lambda event:self._scroll_units(1))

    def _scroll_units(self,units):
        before=self.canvas.yview()
        self.canvas.yview_scroll(int(units),'units')
        self.canvas.update_idletasks()
        return before!=self.canvas.yview()

    def _on_mousewheel(self,event):
        delta=getattr(event,'delta',0)
        if delta:
            # Aqua reports small trackpad deltas while classic wheels often report +/-120.
            units=(-1 if delta>0 else 1) if abs(delta)<120 else int(-delta/120)
            self._scroll_units(units)
        return 'break'

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

    def yview(self,*args):
        result=self.canvas.yview(*args)
        self.canvas.update_idletasks()
        return result

    def yview_moveto(self,fraction):
        self.canvas.yview_moveto(float(fraction)); self.canvas.update_idletasks()

    def yview_scroll(self,number,what='units'):
        self.canvas.yview_scroll(int(number),what); self.canvas.update_idletasks()

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
        # Keep the logical content height independent from the viewport height. This makes
        # Canvas yview fractions stable on macOS and allows the custom scrollbar to move.
        content_h=max(1,len(self.rows)*row_h)
        self.canvas.configure(scrollregion=(0,0,max(total_width,self.canvas.winfo_width()),content_h))
        self.canvas.update_idletasks()


def mark(parent):
    c=tk.Canvas(parent,width=48,height=48,bg=BG,highlightthickness=0,borderwidth=0)
    c.create_oval(3,3,45,45,fill='#12362b',outline='')
    c.create_line(15,13,15,35,fill=GREEN,width=3,capstyle='round')
    c.create_line(16,25,31,13,fill=GREEN,width=3,capstyle='round')
    c.create_line(18,24,33,35,fill=GREEN,width=3,capstyle='round')
    return c


class Tabs(tk.Frame):
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
