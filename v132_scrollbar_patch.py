from pathlib import Path

p=Path('visual.py')
text=p.read_text()
old="s.configure('Vertical.TScrollbar',background='#31424b',troughcolor=PANEL,borderwidth=0,arrowsize=0,relief='flat',width=9)"
new="s.configure('Vertical.TScrollbar',background='#31424b',troughcolor=PANEL,borderwidth=0,arrowsize=0,relief='flat',width=16)"
if old not in text:
    raise SystemExit('Expected V1.3.2 scrollbar style token not found')
p.write_text(text.replace(old,new,1))
print('V1.3.2 scrollbar width updated: 9 -> 16 px')
