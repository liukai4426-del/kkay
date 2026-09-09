"""Draw the app's vector-like mark at every macOS icon size."""
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw

out=Path('OKXLocal.iconset'); out.mkdir(exist_ok=True)
for size in (16,32,128,256,512):
    for scale in (1,2):
        n=size*scale
        image=Image.new('RGBA',(1024,1024),(0,0,0,0))
        draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((40,40,984,984),radius=220,fill='#10181e',outline='#284a40',width=8)
        draw.rounded_rectangle((180,180,844,844),radius=180,fill='#12362b')
        draw.line([(260,682),(434,425),(576,558),(760,302)],fill='#0ddb9f',width=58,joint='curve')
        for x,y in [(260,682),(434,425),(576,558),(760,302)]:
            draw.ellipse((x-28,y-28,x+28,y+28),fill='#0ddb9f')
        image.resize((n,n),Image.Resampling.LANCZOS).save(out/f'icon_{size}x{size}{"@2x" if scale==2 else ""}.png')
subprocess.run(['iconutil','-c','icns',str(out),'-o','OKXLocal.icns'],check=True)
