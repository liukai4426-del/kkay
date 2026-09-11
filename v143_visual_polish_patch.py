"""KAYTRADE V1.4.3 scrollbar and button-shape polish.

Preserves the V1.4.3 trading rule while making every visible vertical scrollbar
use the same rounded dark treatment and increasing every RoundedButton to the
largest useful corner radius for its height.
"""
from v143_score_arbitration_patch import apply as apply_v143
apply_v143()

from tkinter import ttk

import app
import engine
import visual

SCROLL_TRACK=app.BG
SCROLL_THUMB=app.PANEL_ALT
SCROLL_THUMB_ACTIVE=app.FIELD


def _button_radius(height,current=None):
    height=max(2,int(height or 42))
    return max(int(current or 0),max(1,height//2-1))


def _round_rect(canvas,x1,y1,x2,y2,r,**kwargs):
    r=max(0.0,min(float(r),(x2-x1)/2.0,(y2-y1)/2.0))
    points=[x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,
            x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
    return canvas.create_polygon(points,smooth=True,splinesteps=24,**kwargs)


def _scrollbar_draw(self):
    if not self.winfo_exists():return
    self.delete('all')
    width=max(self.bar_width,self.winfo_width()); height=max(1,self.winfo_height())
    track_inset=2
    _round_rect(self,track_inset,0,width-track_inset,height,max(1,(width-2*track_inset)//2),
                fill=SCROLL_TRACK,outline='',tags='track')
    y1,y2,_,_,_=self._bounds()
    thumb_inset=4
    thumb_fill=SCROLL_THUMB_ACTIVE if getattr(self,'drag_y',None) is not None else SCROLL_THUMB
    _round_rect(self,thumb_inset,y1,width-thumb_inset,y2,max(1,(width-2*thumb_inset)//2),
                fill=thumb_fill,outline='',tags='thumb')


def apply():
    if getattr(engine.Engine,'_kaytrade_v143_visual_polish_applied',False):
        return

    previous_button_init=app.RoundedButton.__init__
    previous_scroll_init=visual.WideScrollbar.__init__
    previous_app_init=app.App.__init__

    def rounded_button_init(self,*args,**kwargs):
        height=int(kwargs.get('height',42) or 42)
        kwargs['radius']=_button_radius(height,kwargs.get('radius'))
        return previous_button_init(self,*args,**kwargs)

    def scrollbar_init(self,*args,**kwargs):
        result=previous_scroll_init(self,*args,**kwargs)
        self.configure(bg=SCROLL_TRACK)
        self._draw()
        return result

    def app_init(self,*args,**kwargs):
        native_scrollbar=ttk.Scrollbar
        def rounded_scrollbar(parent,*scroll_args,**scroll_kwargs):
            command=scroll_kwargs.pop('command',None)
            scroll_kwargs.pop('orient',None)
            return visual.WideScrollbar(parent,command=command,width=16,**scroll_kwargs)
        ttk.Scrollbar=rounded_scrollbar
        try:return previous_app_init(self,*args,**kwargs)
        finally:ttk.Scrollbar=native_scrollbar

    app.RoundedButton.__init__=rounded_button_init
    visual.RoundedButton.__init__=rounded_button_init
    visual.WideScrollbar._draw=_scrollbar_draw
    app.WideScrollbar._draw=_scrollbar_draw
    visual.WideScrollbar.__init__=scrollbar_init
    app.WideScrollbar.__init__=scrollbar_init
    app.App.__init__=app_init
    engine.Engine._kaytrade_v143_visual_polish_applied=True


apply()
