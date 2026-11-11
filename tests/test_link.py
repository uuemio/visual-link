import unittest,sys,importlib.util,tempfile,subprocess,os,dataclasses
from pathlib import Path
import numpy as np
from visual_link.core import mapping as m, protocol as p, rs
from visual_link.core.payload import random_data
from visual_link.core.reassembly import Reassembler
from visual_link.config import load
ROOT=Path(__file__).resolve().parents[1]
legacy_path=ROOT.parent/'usb-video-capture/experiments/rs_transport.py'
legacy=None
if legacy_path.exists():
    spec=importlib.util.spec_from_file_location('legacy',legacy_path)
    legacy=importlib.util.module_from_spec(spec); sys.modules['legacy']=legacy; spec.loader.exec_module(legacy)
    legacy.configure_roi(3)

GOLDEN='06d8315a795587667dd147c9c13572d74f6857a98c105e080bc4170fec6b546f'

class LinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=random_data(20261105,1000)
        cls.image,cls.wire=p.encode_frame(cls.data,seed=91)
    def test_capacity_roi(self):
        self.assertEqual(p.capacity(255,247),1000103)
        self.assertTrue(np.all(self.image[:3]==0)); self.assertTrue(np.all(self.image[:,:2]==0))
    def test_mapping(self):
        wire=(bytes(range(256))*((m.WIRE_BYTES+255)//256))[:m.WIRE_BYTES]
        image=m.wire_to_image(wire,'column')
        self.assertEqual(m.image_to_wire(image,False,'column'),wire)
        captured=np.zeros_like(image); captured[:,:1918]=image[:,2:]
        self.assertEqual(m.image_to_wire(captured,True,'column'),wire)
        for x in range(256): self.assertEqual(int(m.DECISION[x]),min(15,(x+8)//17))
        with self.assertRaises(ValueError):m.image_to_wire(image[:10])
    def test_legacy_golden(self):
        if legacy is None:
            self.assertEqual(p.digest(self.wire).hex(),GOLDEN)
            return
        image,wire=legacy.encode_frame(self.data,n=255,k=247,seed=91,layout='column')
        self.assertEqual(wire,self.wire); np.testing.assert_array_equal(image,self.image)
        self.assertEqual(legacy.decode_wire(self.wire).data,self.data)
        self.assertEqual(p.decode(wire).data,self.data)
    def test_parity(self):
        messages=np.frombuffer(bytes(range(247))*3,np.uint8).reshape(-1,247)
        for message,parity in zip(messages,rs.parity(messages,255,247)):
            self.assertEqual(parity.tobytes(),bytes(rs.codec(255,247).encode(message.tobytes()))[247:])
    def test_correction(self):
        for count in range(1,5):
            wire=bytearray(self.wire)
            for i in range(count):wire[255+i]^=123
            self.assertEqual(p.decode(bytes(wire)).data,self.data)
        wire=bytearray(self.wire)
        for i in range(64):wire[i]^=123
        self.assertEqual(p.decode(bytes(wire)).data,self.data)
    def test_every_block_correction(self):
        wire=bytearray(self.wire)
        for block in range(4049):
            for i in range(4): wire[255+block*255+i]^=17+i
        self.assertEqual(p.decode(bytes(wire)).data,self.data)
    def test_protected_invalid_header(self):
        base=list(p.HEADER.unpack(bytes(rs.RSCodec(128).decode(self.wire[:255])[0])[:p.HEADER.size]))
        for field,value in ((1,2),(4,5),(5,0),(6,1000104),(7,500)):
            fields=base.copy();fields[field]=value
            head=bytes(rs.RSCodec(128).encode(p.HEADER.pack(*fields).ljust(127,b'\0')))
            with self.assertRaises(ValueError):p.decode(head+self.wire[255:])
    def test_over_limit_and_padding(self):
        for start in (255,255+4048*255):
            wire=bytearray(self.wire)
            for i in range(20):wire[start+i]^=(i+1)
            with self.assertRaises(ValueError):p.decode(bytes(wire))
        with self.assertRaises(Exception):p.decode(bytes(m.WIRE_BYTES))
    def test_file_boundaries(self):
        for size in (0,1,246,247,248,1000102,1000103,1000104,2000207):
            data=random_data(2,size); cap=p.capacity(255,247); count=max(1,(size+cap-1)//cap)
            frames=[]
            for i in range(count):
                _,wire=p.encode_frame(data[i*cap:(i+1)*cap],index=i,count=count,total=size,file_hash=p.digest(data))
                frames.append(p.decode(wire))
            r=Reassembler()
            for frame in reversed(frames):r.add(frame)
            r.add(frames[0]); self.assertEqual(r.duplicates,1); self.assertEqual(r.finish(),data)
    def test_reassembly_failures(self):
        f=p.decode(self.wire); r=Reassembler();r.add(f)
        with self.assertRaises(ValueError):r.add(dataclasses.replace(f,data=b'other'))
        with self.assertRaises(ValueError):r.add(dataclasses.replace(f,metadata={**f.metadata,'total':99999}))
        with self.assertRaises(ValueError):Reassembler().finish()
        r=Reassembler();r.add(dataclasses.replace(f,metadata={**f.metadata,'count':2}))
        with self.assertRaises(ValueError):r.finish()
    def test_payload(self):
        self.assertEqual(random_data(8,500),random_data(8,500));self.assertNotEqual(random_data(9,500),random_data(8,500))
        with self.assertRaises(ValueError):random_data(-1,5)
    def test_config(self):
        load(ROOT/'configs/offline.json')
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'bad.json';path.write_text('{}')
            with self.assertRaises(ValueError):load(path)
    def test_independent_cli(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); env={**os.environ,'PYTHONPATH':str(ROOT/'src')}
            base=[sys.executable,'-m','visual_link'];cfg=['--config',str(ROOT/'configs/offline.json')]
            def run(args):return subprocess.run(base+args+cfg,cwd=td,env=env,capture_output=True,text=True)
            tx=run(['tx','--seed','7','--size','301','--output',str(td/'frames')]);self.assertEqual(tx.returncode,0,tx.stderr)
            rx=run(['rx','--input',str(td/'frames'),'--output',str(td/'restored')]);self.assertEqual(rx.returncode,0,rx.stderr)
            self.assertEqual((td/'restored').read_bytes(),random_data(7,301))
            self.assertEqual(run(['rx','--input',str(td/'frames'),'--output',str(td/'restored')]).returncode,2)

if __name__=='__main__':unittest.main()
