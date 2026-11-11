"""Local desktop control page for the existing connect command."""
import argparse
import json
import locale
import math
import os
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def outcome(result, sending):
    """Return the missing delivery condition, or None for a complete transfer."""
    if result.get('status') != 'complete':
        return result.get('error') or '程序未生成完整结果，请查看日志。'
    transport = result.get('transport', {})
    if sending and not transport.get('sent_confirmed'):
        return '发送文件尚未获得交付确认。'
    receiving = not sending or result.get('receive_count') is not None or result.get('receive_complete')
    if receiving and not result.get('receive_complete'):
        return '未收到完整文件。'
    if receiving and not transport.get('receive_finish_confirmed'):
        return '接收文件尚未完成交付确认。'
    return None


class Transfer:
    def __init__(self, root, platform=None):
        self.root = Path(root).resolve()
        self.platform = platform or ('windows' if os.name == 'nt' else 'pi')
        self.config = f'configs/{self.platform}-recommended.json'
        self.process = None
        self.directory = None
        self.log = None
        self.sending = False
        self.error = None
        self.lock = threading.Lock()

    def start(self, values):
        if self.process is not None and self.process.poll() is None:
            raise ValueError('本次传输仍在运行。')
        seconds = float(values.get('seconds', 180))
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('运行时间必须为大于0的秒数。')
        name = values.get('input', '').strip()
        source = (self.root / name).resolve() if name else None
        if source is not None and not source.is_file():
            raise ValueError('找不到待发送文件，请填写本机文件的完整路径。')
        output = values.get('output', '').strip() or f'runs/web-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}'
        directory = (self.root / output).resolve()
        if directory.exists():
            raise ValueError('输出目录已存在，请换一个新目录。')
        directory.parent.mkdir(parents=True, exist_ok=True)
        log = directory.with_name(directory.name+'-web.log')
        if log.exists():
            raise ValueError('日志已存在，请换一个新目录。')
        if self.platform == 'windows':
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                       str(self.root/'scripts/windows_interactive.ps1'), '-Mode', 'connect',
                       '-Config', self.config, '-OutputDirectory', str(directory), '-Seconds', str(seconds)]
            if source:
                command += ['-InputImage', str(source)]
        else:
            command = [sys.executable, '-m', 'visual_link', 'connect', '--config', self.config,
                       '--output', str(directory), '--seconds', str(seconds)]
            if source:
                command += ['--input', str(source)]
        self.directory, self.log = directory, log
        self.sending, self.error, self.process = source is not None, None, None
        (directory.with_name(directory.name+'-web-request.json')).write_text(
            json.dumps(dict(input=str(source) if source else None, seconds=seconds,
                            output=str(directory), command=command), ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            with log.open('x', encoding='utf-8') as stream:
                self.process = subprocess.Popen(command, cwd=self.root, stdout=stream, stderr=subprocess.STDOUT)
        except OSError as exc:
            self.error = f'启动失败：{exc}'
        return self.status()

    def status(self):
        value = dict(status='idle', platform=self.platform, config=self.config,
                     root=str(self.root), output=None, log=None, received=None)
        if self.directory is None:
            return value
        directory = self.directory
        running = self.process is not None and self.process.poll() is None
        result = read_json(directory/'result.json') if not running else {}
        progress = read_json(directory/'progress.json')
        value.update(progress)
        value.update(output=str(directory), log=str(self.log),
                     program_log=str(directory.with_name(directory.name+'-job.log')) if self.platform == 'windows' else str(self.log),
                     received=str(directory/'received.bin') if (directory/'received.bin').exists() else None,
                     result=str(directory/'result.json'), sending=self.sending)
        if running:
            value.update(status='running', error=None)
        else:
            error = self.error
            if not error and self.process.returncode != 0:
                job = read_json(directory.with_name(directory.name+'-job-result.json')) if self.platform == 'windows' else {}
                encoding = locale.getpreferredencoding(False) if self.platform == 'windows' else 'utf-8'
                details = self.log.read_text(encoding=encoding, errors='replace').strip()[-4000:]
                error = result.get('error') or job.get('error') or f'程序退出码：{self.process.returncode}。\n{details}'
            error = error or outcome(result, self.sending)
            value.update(result)
            value.update(status='failed' if error else 'success', error=error,
                         exitcode=None if self.process is None else self.process.returncode)
        return value


def make_server(transfer, port):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, value, content_type='application/json; charset=utf-8'):
            body = value.encode('utf-8') if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/':
                self.reply(200, Path(__file__).with_name('web.html').read_text(encoding='utf-8'), 'text/html; charset=utf-8')
            elif self.path == '/api/status':
                with transfer.lock:
                    self.reply(200, transfer.status())
            else:
                self.reply(404, dict(error='页面不存在。'))

        def do_POST(self):
            if self.path != '/api/start':
                self.reply(404, dict(error='接口不存在。'))
                return
            origin = self.headers.get('Origin')
            if (self.headers.get_content_type() != 'application/json'
                    or (origin and origin != 'http://'+self.headers['Host'])):
                self.reply(403, dict(error='请从本机页面启动。'))
                return
            try:
                values = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                with transfer.lock:
                    self.reply(200, transfer.start(values))
            except (ValueError, OSError) as exc:
                self.reply(400, dict(error=str(exc)))

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='Visual Link 本机操作页面')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    transfer = Transfer(Path.cwd())
    server = make_server(transfer, args.port)
    print(f'Visual Link：http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
