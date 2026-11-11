"""Run a display job in the logged-in Windows session, with an outer mode watchdog."""
import json
import subprocess
import sys
import traceback
from pathlib import Path

from visual_link.backends.windows import desktop_mode, monitors
from visual_link.backends.windows_signal import identity_signal, signals
from visual_link.config import load


def main():
    job_path = Path(sys.argv[1])
    job = json.loads(job_path.read_text(encoding='utf-8-sig'))
    config = load(job['config'])
    mode = job.get('mode', 'display')
    if mode not in ('display', 'display-stream', 'bootstrap', 'connect'):
        raise ValueError('unsupported display job mode')
    report = dict(status='failed')
    try:
        # Preserve desktop and physical timing even when the mode guard refuses startup.
        report['observed_display'] = dict(monitors=monitors(), signals=signals())
        with desktop_mode(config) as display_mode, identity_signal(display_mode['device'], config.get('temporary_1080p', False)) as timing:
            report['mode'] = display_mode
            report['timing'] = timing
            with Path(job['log']).open('w', encoding='utf-8') as output:
                command = [sys.executable, '-m', 'visual_link', mode,
                           '--config', job['config'], '--output', job['output'],
                           '--seconds', str(job['seconds'])]
                if mode not in ('bootstrap', 'connect') or (mode == 'connect' and job.get('input')):
                    command.extend(['--input', job['input']])
                result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                                        timeout=job['seconds']+config['timeout']+10)
            report.update(status='complete' if result.returncode == 0 else 'failed', exitcode=result.returncode)
        report['original_mode_restored'] = True
    except Exception as exc:
        report.update(error=str(exc), traceback=traceback.format_exc())
    Path(job['result']).write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['status'] == 'complete' else 2


if __name__ == '__main__':
    sys.exit(main())
