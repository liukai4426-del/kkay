from pathlib import Path
p=Path('v135_fix.py')
text=p.read_text()
start=text.index("replace_once('app.py',\n\"基础单笔风险≤")
end=text.index("\n\n# --- desktop.py",start)
replacement="""replace_once('app.py',
"最终仍受日回撤、最大名义仓位和资金5%绝对风险上限约束",
"不再设资金5%硬上限，最终仍受日回撤、最大名义仓位与杠杆约束")"""
p.write_text(text[:start]+replacement+text[end:])
print('V1.3.5 patch anchor corrected')
