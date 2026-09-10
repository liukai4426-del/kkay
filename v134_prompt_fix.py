from pathlib import Path
p=Path('v134_upgrade.py')
s=p.read_text()
old="s=replace_once(s,old_prompt,new_prompt,'authorization score prompt')"
new="""if old_prompt in s:\n    s=s.replace(old_prompt,new_prompt,1)\nelse:\n    lines=s.splitlines(True)\n    replacement_line=\"        typed=simpledialog.askstring('启动全自动授权',summary+f'\\\\n最高10分；最终评分 ≥ {s.score_threshold:g}/10 才进入开仓风控。3.5–4.5=1×仓位 / 5.0–6.5=1.5× / 7.0–10=2×。1D EMA5/10/20支撑或压力最高分别+1/+2/+3且只取最高；1H与4H明显反向趋势各-1。15m Setup≥0.5、5m Trigger≥0.5；前方结构<1R禁止开仓。\\\\n\\\\n同意上述参数请输入 '+token,parent=self.root)\\n\"\n    for i,line in enumerate(lines):\n        if \"typed=simpledialog.askstring('启动全自动授权'\" in line:\n            lines[i]=replacement_line\n            break\n    else:\n        raise SystemExit('missing auth prompt line')\n    s=''.join(lines)"""
if old not in s:
    raise SystemExit('prompt fixer could not find helper call')
s=s.replace(old,new,1)
p.write_text(s)
print('V1.3.4 prompt anchor made robust')
