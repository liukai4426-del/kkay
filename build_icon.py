"""Build the macOS app icon from the selected V1.4.2 circular gradient logo."""
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw

root=Path(__file__).resolve().parent
source=Image.open(root/'assets'/'kaytrade-v142-logo.png').convert('RGBA')
out=root/'OKXLocal.iconset'; out.mkdir(exist_ok=True)

base=Image.new('RGBA',(1024,1024),(0,0,0,0))
draw=ImageDraw.Draw(base)
draw.rounded_rectangle((38,38,986,986),radius=220,fill='#080d10')
logo=source.resize((760,760),Image.Resampling.LANCZOS)
base.alpha_composite(logo,((1024-760)//2,(1024-760)//2))

for size in (16,32,128,256,512):
    for scale in (1,2):
        n=size*scale
        base.resize((n,n),Image.Resampling.LANCZOS).save(out/f'icon_{size}x{size}{"@2x" if scale==2 else ""}.png')
subprocess.run(['iconutil','-c','icns',str(out),'-o','OKXLocal.icns'],check=True)
