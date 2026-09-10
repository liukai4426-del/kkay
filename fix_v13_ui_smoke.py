from pathlib import Path

p=Path('app.py'); s=p.read_text()
repls={
    "\\nUTC日回撤{s.daily_loss} USDT，连亏{s.consecutive_losses}次停止新开仓": "\\n中国时间日回撤{s.daily_loss} USDT，连亏{s.consecutive_losses}次停止新开仓",
    "self.score_vars[side].set(f\"{score['total']} / 19\")": "self.score_vars[side].set(f\"{score['total']} / {data.get('score_max',18)}\")",
}
for old,new in repls.items():
    if s.count(old)!=1: raise RuntimeError(f'app replacement expected once: {old!r}, got {s.count(old)}')
    s=s.replace(old,new,1)
if ' / 19' in s: raise RuntimeError('stale /19 score display remains in app.py')
if 'UTC日回撤' in s: raise RuntimeError('stale UTC daily-risk label remains in app.py')
p.write_text(s)

p=Path('desktop.py'); s=p.read_text()
repls={
    "assert '1.2' in root.title()": "assert '1.3' in root.title()",
    "assert len(ui.score_table.get_children()) == 8": "assert len(ui.score_table.get_children()) == 13",
    "assert ui.score_vars['做多'].get().endswith('/ 19')": "assert ui.score_vars['做多'].get().endswith('/ 18')",
    "PASS: V1.2 UI, red/green scores": "PASS: V1.3 UI, layered 18-point red/green scores",
}
for old,new in repls.items():
    if s.count(old)!=1: raise RuntimeError(f'desktop replacement expected once: {old!r}, got {s.count(old)}')
    s=s.replace(old,new,1)
if "'1.2' in root.title()" in s or "endswith('/ 19')" in s:
    raise RuntimeError('stale V1.2/V1.2.3 smoke assertion remains')
p.write_text(s)
print('V1.3 UI score display and packaged smoke test migrated')
