from pathlib import Path
p=Path('v134_upgrade.py')
s=p.read_text()
old="s=replace_once(s,'    return min(1.5,sum(components.values())), components','    return min(1.0,sum(components.values())), components','trigger cap')"
new="""trigger_anchor=\"\"\"    components={'ema_reclaim':.5*int(ema_reclaim),'kdj':.5*int(kdj),'reversal':.5*int(reversal),'ema_direction':.5*int(ema_direction)}\n    return min(1.5,sum(components.values())), components\n\"\"\"\ntrigger_replacement=\"\"\"    components={'ema_reclaim':.5*int(ema_reclaim),'kdj':.5*int(kdj),'reversal':.5*int(reversal),'ema_direction':.5*int(ema_direction)}\n    return min(1.0,sum(components.values())), components\n\"\"\"\ns=replace_once(s,trigger_anchor,trigger_replacement,'trigger cap')"""
if old not in s:
    raise SystemExit('trigger anchor fixer could not find target')
s=s.replace(old,new,1)
p.write_text(s)
print('V1.3.4 trigger anchor fixed')
