"""Build the macOS app icon from the selected circular gradient logo."""
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw

root=Path(__file__).resolve().parent
source=Image.open(root/'assets'/'kaytrade-v142-logo.png').convert('RGBA')
out=root/'OKXLocal.iconset'; out.mkdir(exist_ok=True)

# Only a subtle enlargement: trim 2% from the source edge and grow the artwork
# from 760 to 780 px inside the same 1024 px app-icon tile.
LOGO_CROP_RATIO=0.02
LOGO_SIZE=780
w,h=source.size
dx=max(0,int(round(w*LOGO_CROP_RATIO)))
dy=max(0,int(round(h*LOGO_CROP_RATIO)))
if dx*2<w and dy*2<h:
    source=source.crop((dx,dy,w-dx,h-dy))

base=Image.new('RGBA',(1024,1024),(0,0,0,0))
draw=ImageDraw.Draw(base)
draw.rounded_rectangle((38,38,986,986),radius=220,fill='#080d10')
logo=source.resize((LOGO_SIZE,LOGO_SIZE),Image.Resampling.LANCZOS)
base.alpha_composite(logo,((1024-LOGO_SIZE)//2,(1024-LOGO_SIZE)//2))

for size in (16,32,128,256,512):
    for scale in (1,2):
        n=size*scale
        base.resize((n,n),Image.Resampling.LANCZOS).save(out/f'icon_{size}x{size}{"@2x" if scale==2 else ""}.png')
subprocess.run(['iconutil','-c','icns',str(out),'-o','OKXLocal.icns'],check=True)
