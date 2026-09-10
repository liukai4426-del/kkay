"""V1.2 presentation components. No trading or network dependencies."""
import tkinter as tk
from tkinter import ttk

BG = '#080d10'
PANEL = '#10181e'
EDGE = '#223038'
TEXT = '#eef5f7'
MUTED = '#8b9ca6'
GREEN = '#0ddb9f'
RED = '#ff637f'

def theme(root):
    root.configure(bg=BG)
    s=ttk.Style(root)
    s.theme_use('clam')
    s.configure('.',background=BG,foreground=TEXT,font=('Helvetica',13))
    s.configure('TFrame',background=BG)
    s.configure('TLabel',background=BG,foreground=TEXT)
    s.configure('Muted.TLabel',foreground=MUTED)
    s.configure('Title.TLabel',foreground=TEXT,font=('Helvetica',24,'bold'))
    s.configure('TButton',background='#1b2930',foreground=TEXT,padding=(15,10),borderwidth=0,relief='flat')
    s.map('TButton',background=[('active','#283c45'),('pressed','#324b55')])
    s.configure('Accent.TButton',background=GREEN,foreground='#05261d',font=('Helvetica',13,'bold'))
    s.map('Accent.TButton',background=[('active','#54edc0'),('pressed','#0bb985')])
    s.configure('Danger.TButton',background='#342029',foreground=RED)
    s.map('Danger.TButton',background=[('active','#502733')])
    s.configure('TEntry',fieldbackground=PANEL,foreground=TEXT,padding=9,borderwidth=1,lightcolor=EDGE,darkcolor=EDGE,bordercolor=EDGE)
    s.configure('TCombobox',fieldbackground=PANEL,background=PANEL,foreground=TEXT,padding=7,arrowsize=12)
    s.map('TCombobox',fieldbackground=[('readonly',PANEL)],foreground=[('readonly',TEXT)])
    s.configure('TNotebook',background=BG,borderwidth=0,tabmargins=(0,0,0,10))
    s.layout('TNotebook.Tab',[('Notebook.tab',{'sticky':'nswe','children':[('Notebook.padding',{'side':'top','sticky':'nswe','children':[('Notebook.label',{'side':'top','sticky':''})]})]})])
    s.configure('TNotebook.Tab',padding=(20,10),borderwidth=0)
    s.map('TNotebook.Tab',background=[('selected','#15382e'),('!selected',BG)],foreground=[('selected',GREEN),('!selected',MUTED)])
    s.layout('Treeview',[('Treeview.treearea',{'sticky':'nswe'})])
    s.configure('Treeview',background=PANEL,fieldbackground=PANEL,foreground=TEXT,rowheight=27,borderwidth=0)
    s.configure('Treeview.Heading',background='#17232a',foreground=MUTED,font=('Helvetica',11,'bold'),relief='flat',borderwidth=0,padding=7)
    s.map('Treeview',background=[('selected','#214039')],foreground=[('selected',TEXT)])
    s.configure('Vertical.TScrollbar',background='#26353d',troughcolor=PANEL,borderwidth=0,arrowsize=10)
    for name,color in [('Long',GREEN),('Short',RED)]:
        s.configure(name+'.Horizontal.TProgressbar',background=color,troughcolor='#202b32',borderwidth=0,lightcolor=color,darkcolor=color,thickness=5)

class Card(tk.Canvas):
    """Rounded backdrop with ordinary Tk content, redrawn only on resize."""
    def __init__(self,parent,height=120,**kwargs):
        super().__init__(parent,height=height,bg=BG,highlightthickness=0,**kwargs)
        self.body=tk.Frame(self,bg=PANEL)
        self.window=self.create_window(18,16,anchor='nw',window=self.body)
        self.bind('<Configure>',self.resize)

    def resize(self,event):
        w,h=event.width,event.height
        self.delete('surface')
        r=20
        points=[r,1,w-r,1,w-1,1,w-1,r,w-1,h-r,w-1,h-1,w-r,h-1,r,h-1,1,h-1,1,h-r,1,r,1,1]
        self.create_polygon(points,smooth=True,splinesteps=24,fill=PANEL,outline=EDGE,tags='surface')
        self.tag_lower('surface')
        self.itemconfigure(self.window,width=max(1,w-36),height=max(1,h-32))

def label(parent,text=None,variable=None,size=13,color=TEXT,bold=False):
    return tk.Label(parent,text=text,textvariable=variable,bg=PANEL,fg=color,font=('Helvetica',size,'bold' if bold else 'normal'),anchor='w')

def mark(parent):
    c=tk.Canvas(parent,width=48,height=48,bg=BG,highlightthickness=0)
    c.create_oval(3,3,45,45,fill='#12362b',outline='#245044')
    c.create_line(14,30,23,18,30,26,36,15,fill=GREEN,width=3,joinstyle='round')
    return c

class Tabs(tk.Frame):
    """Plain page navigation avoids Aqua ttk notebook painting artifacts."""
    def __init__(self,parent,**kwargs):
        super().__init__(parent,bg=BG,**kwargs)
        self.nav=tk.Frame(self,bg=BG)
        self.nav.pack(fill='x',pady=(0,10))
        self.pages=[]; self.buttons=[]; self.active=None

    def add(self,page,text):
        index=len(self.pages)
        self.pages.append(page)
        button=tk.Label(self.nav,text=text,bg=BG,fg=MUTED,font=('Helvetica',13),padx=20,pady=10,cursor='hand2')
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
