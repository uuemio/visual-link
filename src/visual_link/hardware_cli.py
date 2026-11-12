"""Hardware CLI dispatch kept separate from offline TX/RX."""
import math

from . import calibration
from .backends.image_sequence import write
from .core.mapping import read_image
from .core.image import image_parameters
from .hardware import capture, receive_one, save_json

MODES = ('pattern', 'compare', 'display', 'capture', 'rx-one', 'display-stream', 'rx-stream', 'bootstrap', 'connect')


def run(args, config):
    if args.captured or args.size is not None:
        raise ValueError('--captured/--size apply only to offline TX/RX')
    if args.mode != 'pattern' and args.seed is not None:
        raise ValueError('--seed is allowed only for TX or pattern generation')
    if args.mode in ('capture', 'rx-one', 'rx-stream', 'pattern', 'bootstrap') and args.input is not None:
        raise ValueError('capture/RX/pattern do not accept --input')
    if args.mode in ('compare', 'display', 'display-stream') and args.input is None:
        raise ValueError('display/compare require --input image')
    if args.mode == 'compare' and args.reference is None:
        raise ValueError('compare requires --reference source image (diagnostics only)')
    if args.mode != 'compare' and args.reference is not None:
        raise ValueError('--reference is for compare only; RX never reads source data')
    if args.mode in ('capture', 'rx-one', 'rx-stream') and config['backend'] not in ('linux', 'windows'):
        raise ValueError('capture requires a platform configuration')
    if args.mode in ('display', 'display-stream') and config['backend'] not in ('linux', 'windows'):
        raise ValueError('display requires a platform configuration')
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        raise ValueError('--seconds must be finite and positive')
    if not 1 <= args.queue_size <= 16:
        raise ValueError('--queue-size must be between 1 and 16')
    # Each run owns a new directory; failures preserve diagnostic artifacts.
    args.output.mkdir(parents=True, exist_ok=False)
    save_json(args.output/'config.json', config)
    try:
        if args.mode in ('bootstrap', 'connect'):
            from .bootstrap import exchange
            return exchange(config, args.output, args.seconds, calibrate=args.mode == 'connect',
                            negotiate=args.mode == 'connect', input_file=args.input)
        elif args.mode == 'pattern':
            seed = 20261109 if args.seed is None else args.seed
            write(args.output/'pattern.png', calibration.pattern(seed, image_parameters(config, 'tx')))
            report = dict(status='generated', seed=seed, generator='NumPy 2.2.6 default_rng.bytes',
                          image='pattern.png')
        elif args.mode == 'compare':
            report = calibration.compare(read_image(args.reference), read_image(args.input), config['orientation'], image_parameters(config, 'rx'))
        elif args.mode == 'rx-stream':
            from .streaming import receive_stream
            report = receive_stream(config, args.output, args.seconds, args.queue_size)
        elif args.mode == 'display-stream':
            from .presentation import Presentation
            if not args.input.is_dir():
                raise ValueError('display-stream requires a PNG directory')
            if config['backend'] == 'windows':
                from .backends.windows import display_one
            else:
                from .backends.linux import display_one
            with (args.output/'presentations.jsonl').open('x') as log:
                sequence = Presentation(sorted(args.input.glob('*.png')), config['fps'], log, args.queue_size)
                try:
                    report = display_one(config, sequence.first(config['timeout']), args.seconds,
                                         ready=lambda value: save_json(args.output/'ready.json', value),
                                         advance=sequence.advance)
                    report.update(status='complete', presentations=sequence.shown,
                                  late_presentations=sequence.late, requested_fps=config['fps'],
                                  queue_capacity=args.queue_size, encoding='preencoded',
                                  cycles_repeat=True, presentation_times='software; not HDMI timestamps')
                finally:
                    sequence.close()
        elif args.mode == 'display':
            if config['backend'] == 'windows':
                from .backends.windows import display_one
                report = display_one(config, read_image(args.input), args.seconds,
                                     ready=lambda value: save_json(args.output/'ready.json', value))
            else:
                from .backends.linux import display_one
                report = display_one(config, read_image(args.input), args.seconds,
                                     ready=lambda value: save_json(args.output/'ready.json', value))
        else:
            image = capture(config, args.output)
            report = (receive_one(config, image, args.output) if args.mode == 'rx-one'
                      else dict(status='captured', image='capture.png', orientation_applied=False))
    except (ValueError, OSError) as exc:
        if args.mode in ('rx-stream', 'bootstrap', 'connect') and (args.output/'result.json').exists():
            raise
        report = dict(status='failed', error=str(exc))
        if args.mode == 'rx-one':
            report.update(expected=None, received=0, missing=None)
            rx_report = args.output/'rx.json'
            if rx_report.exists():
                import json
                report.update(json.loads(rx_report.read_text()))
        save_json(args.output/'result.json', report)
        raise
    save_json(args.output/'result.json', report)
    return report
