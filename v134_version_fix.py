from pathlib import Path
p=Path('v134_upgrade.py')
s=p.read_text()
old="p=Path('app.py'); s=p.read_text().replace('V1.3.3','V1.3.4')"
new="p=Path('app.py'); s=p.read_text().replace('V1.3.3','V1.3.4').replace('KAYTRADE 1.3.3','KAYTRADE 1.3.4')"
if old not in s:
    raise SystemExit('could not find app version migration line')
s=s.replace(old,new,1)
p.write_text(s)
print('V1.3.4 window title migration fixed')
