import sys, json, time, base64, stat, hashlib
from pathlib import Path
sys.path.insert(0,'/tmp')
from vl_remote import client
root=Path('/mnt/hgfs/VMwareShare/visual-link')
base='runs/mixed-fix-20261112-06'
tag=sys.argv[1];height=int(sys.argv[2]);seconds=int(sys.argv[3]) if len(sys.argv)>3 else 180
pi=client('pi');win=client('win');s=pi.open_sftp();home=s.normalize('.')
for side in ['windows','pi']:
 config=json.loads((root/f'configs/{side}-recommended.json').read_text())
 if height==1080: config['candidate_modes']=[[1920,1080]]
 config['signal_lock_seconds']=40
 config['timeout']=30
 name=f'{base}/{tag}-{side}-config.json'
 (root/name).write_text(json.dumps(config,indent=2))
 payload=(f'{tag} {side} visual HDMI independent payload\n'.encode()*100000)[:3100000 if side=='windows' else 3000000]
 (root/f'{base}/{tag}-{side}-input.bin').write_bytes(payload)
 if side=='pi':
  pi.exec_command(f'mkdir -p ~/visual-link/{base}')[1].read()
  for item in [name,f'{base}/{tag}-{side}-input.bin']:
   s.put(str(root/item),home+'/visual-link/'+item)
ps=f'''Set-Location 'E:\\VMwareShare\\visual-link'
.\\scripts\\windows_interactive.ps1 -Config {base}/{tag}-windows-config.json -InputImage {base}/{tag}-windows-input.bin -OutputDirectory {base}/{tag}-windows -Mode connect -Seconds {seconds}
'''
_,wo,we=win.exec_command('powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand '+base64.b64encode(ps.encode('utf-16le')).decode())
command=f'cd ~/visual-link && XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 DISPLAY=:0 PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 .venv-hardware/bin/python -m visual_link connect --config {base}/{tag}-pi-config.json --input {base}/{tag}-pi-input.bin --output {base}/{tag}-pi --seconds {seconds} > {base}/{tag}-pi.log 2>&1'
_,po,pe=pi.exec_command(command)
print('started',tag,flush=True)
for tick in range((seconds+80)//10):
 time.sleep(10)
 for side in ['windows','pi']:
  name=f'{base}/{tag}-{side}'
  try:
   if side=='windows':
    continue  # Do not open HGFS progress files while Windows atomically replaces them.
   else:
    try:f=s.open(home+'/visual-link/'+name+'/progress.json')
    except OSError:f=s.open(home+'/visual-link/'+name+'/startup/progress.json')
    with f:progress=json.load(f)
   print(side,{k:progress.get(k) for k in ['state','phase','round','indices','local_status','peer_status','selected_modes','connection_ready','receive_complete']},flush=True)
  except (OSError,ValueError): pass
 if wo.channel.exit_status_ready() and po.channel.exit_status_ready():break
print('windows',wo.read().decode(errors='replace'),we.read().decode(errors='replace'),flush=True)
print('pi',po.read().decode(errors='replace'),pe.read().decode(errors='replace'),flush=True)
def collect(remote, local):
 local.mkdir(exist_ok=True,parents=True)
 for item in s.listdir_attr(remote):
  if stat.S_ISDIR(item.st_mode):collect(remote+'/'+item.filename,local/item.filename)
  else:s.get(remote+'/'+item.filename,str(local/item.filename))
collect(home+'/visual-link/'+base+'/'+tag+'-pi',root/base/(tag+'-pi'))
s.get(home+'/visual-link/'+base+'/'+tag+'-pi.log',str(root/base/(tag+'-pi.log')))
for side,other in [('windows','pi'),('pi','windows')]:
 p=root/base/(tag+'-'+side)
 try:
  result=json.loads((p/'result.json').read_text())
  actual=hashlib.sha256((p/'received.bin').read_bytes()).hexdigest()
  expected=hashlib.sha256((root/base/(tag+'-'+other+'-input.bin')).read_bytes()).hexdigest()
  print(side,result['status'],'sha_match',actual==expected,'modes',result.get('modes'),flush=True)
 except Exception as exc:print(side,type(exc).__name__,str(exc),flush=True)
s.close();pi.close();win.close()
