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
    s.configure('Vertical.TScrollbar',background='#26353d',troughcolor=PANEL,borderwidth=0,arrowsize=10,relief='flat')
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


def label(parent,text=None,variable=None,size=13,color=TEXT,bold=False,bg=None):
    return tk.Label(parent,text=text,textvariable=variable,bg=bg or parent.cget('bg'),fg=color,
                    font=('Helvetica',size,'bold' if bold else 'normal'),anchor='w',bd=0,highlightthickness=0)


class RoundedButton(tk.Canvas):
    """True rounded Tk button with tonal hover/pressed states and no outline."""
    PALETTES={
        'neutral':('#1a2931','#223640','#2a414c',TEXT),
        'accent':('#0b7f61','#0da97d','#096f55','#ecfff8'),
        'danger':('#8b3347','#aa4056','#712b3b','#fff1f4'),
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


def mark(parent):
    c=tk.Canvas(parent,width=48,height=48,bg=BG,highlightthickness=0,borderwidth=0)
    c.create_oval(3,3,45,45,fill='#12362b',outline='')
    c.create_line(15,13,15,35,fill=GREEN,width=3,capstyle='round')
    c.create_line(16,25,31,13,fill=GREEN,width=3,capstyle='round')
    c.create_line(18,24,33,35,fill=GREEN,width=3,capstyle='round')
    return c


class Tabs(tk.Frame):
    """Plain page navigation avoids Aqua ttk notebook painting artifacts."""
    def __init__(self,parent,**kwargs):
        super().__init__(parent,bg=BG,bd=0,highlightthickness=0,**kwargs)
        self.nav=tk.Frame(self,bg=BG,bd=0,highlightthickness=0)
        self.nav.pack(fill='x',pady=(0,10))
        self.pages=[]; self.buttons=[]; self.active=None

    def add(self,page,text):
        index=len(self.pages)
        self.pages.append(page)
        button=tk.Label(self.nav,text=text,bg=BG,fg=MUTED,font=('Helvetica',13),padx=20,pady=10,cursor='hand2',bd=0,highlightthickness=0)
        button.pack(side='left',padx=(0,6))
        button.bind('<Button-1>',lambda event:self.select(index))
        self.buttons.append(button)
        if self.active is None:self.select(index)

    def select(self,page=None):
        if page is None:return str(self.pages[self.active])
        index=page if isinstance(page,int) else self.pages.index(page)
        for p in self.pages:p.pack_forget()
        self.pages[index].pack(fill='both',expand=True)
        self.pages[index].lift()
        self.active=index
        for i,b in enumerate(self.buttons):
            b.configure(bg='#15382e' if i==index else BG,fg=GREEN if i==index else MUTED)
        self.event_generate('<<NotebookTabChanged>>')
        self.after_idle(lambda:self.repaint(self.pages[index]))

    def repaint(self,widget):
        widget.event_generate('<Expose>',when='tail')
        for child in widget.winfo_children():self.repaint(child)
