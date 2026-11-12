"""Selected V4L2 driver's declarations and current output (not inferred HDMI timing)."""
import re
import subprocess


def parse_formats(text):
    rows, fmt, mode = [], None, None
    for line in text.splitlines():
        match = re.search(r"\[\d+\]: '(.{4})'", line)
        if match:
            fmt, mode = match[1], None
        match = re.search(r'Size: Discrete (\d+)x(\d+)', line)
        if match and fmt:
            mode = dict(format=fmt, width=int(match[1]), height=int(match[2]), fps=[])
            rows.append(mode)
        match = re.search(r'Interval: Discrete .*\(([\d.]+) fps\)', line)
        if match and mode is not None:
            rate = float(match[1])
            if rate not in mode['fps']:
                mode['fps'].append(rate)
        # Preserve continuous/stepwise declarations without inventing discrete candidates.
        if 'Size: Stepwise' in line or 'Size: Continuous' in line:
            rows.append(dict(format=fmt, size_range=line.strip()))
            mode = None
    return rows


def capabilities(device, timeout=10):
    path = f'/dev/video{device}' if isinstance(device, int) else device

    def run(*arguments):
        try:
            result = subprocess.run(['v4l2-ctl', '-d', path, *arguments], capture_output=True,
                                    text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('V4L2 capability query exceeded timeout') from exc
        return dict(command=list(arguments), returncode=result.returncode,
                    stdout=result.stdout, stderr=result.stderr)

    declared = run('--list-formats-ext')
    if declared['returncode']:
        raise ValueError(f"V4L2 capability query failed: {declared['stderr'].strip()}")
    current = run('--get-fmt-video', '--get-parm')
    timing = run('--query-dv-timings')
    size = re.search(r'Width/Height\s*:\s*(\d+)/(\d+)', current['stdout'])
    fmt = re.search(r"Pixel Format\s*:\s*'(.{4})'", current['stdout'])
    fps = re.search(r'Frames per second:\s*([\d.]+)', current['stdout'])
    return dict(api='V4L2', device=path, declared=parse_formats(declared['stdout']),
                current_output=dict(width=int(size[1]), height=int(size[2]),
                                    format=fmt[1] if fmt else None, fps=float(fps[1]) if fps else None) if size else None,
                input_timing=timing, raw=[declared, current])
