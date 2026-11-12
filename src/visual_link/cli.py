import argparse, json, os, tempfile, time
from pathlib import Path
from .config import load
from .core.payload import random_data
from .core.protocol import capacity, digest, encode_frame, decode
from .core.mapping import image_to_wire
from .core.image import image_parameters
from .core.reassembly import Reassembler
from .backends.image_sequence import frames, write
from .hardware_cli import MODES, run as run_hardware

def main():
    p=argparse.ArgumentParser(description='Visual TX/RX and single-frame hardware diagnostics')
    p.add_argument('mode',choices=['tx','rx',*MODES])
    p.add_argument('--config',required=True)
    p.add_argument('--input',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seed',type=int)
    p.add_argument('--size',type=int)
    p.add_argument('--captured',action='store_true',help='input uses capture ROI, rather than source ROI')
    p.add_argument('--reference',type=Path,help='source image for compare only')
    p.add_argument('--seconds',type=float,default=60,help='single-image display hold time')
    p.add_argument('--refresh-hz',type=float,help='display refresh rate for apply-mode (Hz)')
    p.add_argument('--queue-size',type=int,default=2,help='bounded stream queue capacity (1-16)')
    a=p.parse_args()
    try:
        c=load(a.config)
        tx,rx=image_parameters(c, 'tx'),image_parameters(c, 'rx')
        if a.mode in MODES:
            print(json.dumps(run_hardware(a,c)))
            return
        if a.reference is not None:
            raise ValueError('--reference is for compare only')
        if c['backend']!='image_sequence':
            raise ValueError('tx/rx operate on offline images; use display/capture/rx-one for hardware')
        if a.output.exists():
            raise ValueError('output already exists')
        if a.mode=='tx':
            if a.captured or (a.input is not None and (a.seed is not None or a.size is not None)):
                raise ValueError('choose file input or seed and size')
            if a.input is None and (a.seed is None or a.size is None):
                raise ValueError('TX requires input or seed and size')
            data=a.input.read_bytes() if a.input else random_data(a.seed,a.size)
            cap=capacity(255,247,tx); count=max(1,(len(data)+cap-1)//cap)
            a.output.mkdir(parents=True,exist_ok=False)
            for i in range(count):
                image,_=encode_frame(data[i*cap:(i+1)*cap],index=i,count=count,total=len(data),file_hash=digest(data),seed=i,parameters=tx)
                write(a.output/f'frame_{i:05d}.png',image)
            print(json.dumps(dict(frames=count,bytes=len(data),sha256=digest(data).hex())))
        else:
            if a.input is None or a.seed is not None or a.size is not None:
                raise ValueError('RX requires image directory only')
            r=Reassembler(); start=time.monotonic()
            for image in frames(a.input):
                if time.monotonic()-start > c['timeout']:
                    raise ValueError('offline processing timeout')
                if c['orientation']=='flip_vertical': image=image[::-1].copy()
                r.add(decode(image_to_wire(image,captured=a.captured,layout='column',parameters=rx),parameters=rx))
            data=r.finish()
            a.output.parent.mkdir(parents=True,exist_ok=True)
            fd,tmp=tempfile.mkstemp(dir=a.output.parent)
            try:
                with os.fdopen(fd,'wb') as f: f.write(data)
                # Exclusive destination reservation prevents accidental overwrite.
                with a.output.open('xb') as f: pass
                os.replace(tmp,a.output)
            finally:
                if Path(tmp).exists(): Path(tmp).unlink()
            print(json.dumps(dict(**r.stats(),bytes=len(data),sha256=digest(data).hex())))
    except (ValueError,OSError) as e:
        p.exit(2,str(e)+'\n')
