"""Verify six received files and directional native modes from saved evidence."""
import hashlib
import json
from pathlib import Path
root = Path(__file__).resolve().parent
rows = []
for tag, heights in [('A', {'windows':1200,'pi':1080}),
                     ('B-accepted', {'windows':1080,'pi':1200}),
                     ('C-final', {'windows':1080,'pi':1080})]:
    for side, peer in [('windows','pi'),('pi','windows')]:
        run = root/f'{tag}-{side}'
        result = json.loads((run/'result.json').read_text())
        capabilities = json.loads((run/'startup/capabilities.json').read_text())
        initial = json.loads(next((run/'startup/stage-000').glob('initial-*.json')).read_text())
        calibration = json.loads((run/'startup/stage-001/calibration.json').read_text())
        source = root/f'{tag}-{peer}-input.bin'
        received = run/'received.bin'
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        modes = result['modes']
        row = dict(run=run.name,status=result['status'], bytes=received.stat().st_size,
                   source_sha256=digest(source), received_sha256=digest(received),
                   initial_display=capabilities['display']['current_output'],
                   initial_capture=initial['actual'], tx=modes['tx_mode'],rx=modes['rx_mode'],
                   actual_capture=modes['actual_capture']['actual'],
                   symbol_errors=calibration['symbol_errors'], transport=result['transport'])
        assert row['status']=='complete' and row['source_sha256']==row['received_sha256']
        assert row['initial_display']['height']==row['tx']['height']==heights[side]
        assert row['rx']['height']==row['actual_capture']['height']==heights[peer]
        assert row['symbol_errors']==0
        assert row['transport']['sent_confirmed'] and row['transport']['receive_finish_confirmed']
        assert row['transport']['acknowledged']>=3
        rows.append(row)
(root/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
print(json.dumps([dict(run=r['run'],status=r['status'],sha_match=True) for r in rows],indent=2))
