"""Pixel diagnostics only; suggestions never change the wire configuration."""
import cv2
import numpy as np

from .core.mapping import CODEBOOK, DECISION, WIRE_BYTES, wire_to_image


def pattern(seed):
    """Spatially varying 16-level target, reproducible with pinned NumPy."""
    if seed < 0:
        raise ValueError('seed must be nonnegative')
    wire = np.random.default_rng(seed).bytes(WIRE_BYTES)
    return wire_to_image(wire, 'column')


def gray(image):
    if image.dtype != np.uint8 or image.shape not in ((1080, 1920), (1080, 1920, 3)):
        raise ValueError('expected native 1920x1080 uint8 gray or BGR image')
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image


def compare(source, captured, orientation):
    """Measure fixed ROI and estimate integer translation within +/-8 pixels.

    dx/dy mean source[y, x] == oriented_capture[y-dy, x-dx].
    Sparse samples locate a candidate; full ROI statistics test the fixed profile.
    """
    source, captured = gray(source), gray(captured)
    expected = source[3:, 2:]
    if not np.isin(expected, CODEBOOK).all():
        raise ValueError('reference must use the 16-level source codebook')
    target = DECISION[source]
    ys, xs = np.mgrid[16:1064:17, 16:1904:17]
    candidates = []
    for name, image in (('normal', captured), ('flip_vertical', captured[::-1])):
        symbols = DECISION[image]
        for dy in range(-8, 9):
            for dx in range(-8, 9):
                score = float(np.mean(target[ys, xs] == symbols[ys-dy, xs-dx]))
                candidates.append(dict(orientation=name, dx=dx, dy=dy, sample_accuracy=score))
    candidates.sort(key=lambda row: row['sample_accuracy'], reverse=True)
    image = captured[::-1] if orientation == 'flip_vertical' else captured
    observed = image[3:, :1918]
    errors = DECISION[observed] != DECISION[expected]
    levels = []
    for level in CODEBOOK:
        values = observed[expected == level]
        levels.append(dict(source=int(level), count=int(values.size),
                           mean=None if not values.size else float(values.mean()),
                           minimum=None if not values.size else int(values.min()),
                           maximum=None if not values.size else int(values.max())))
    best = candidates[0]
    return dict(kind='calibration_pixels_only', search_radius=8, best_alignment=best,
                runner_up=candidates[1], configured=dict(orientation=orientation, dx=2, dy=0),
                configured_alignment_matches=(best['orientation'] == orientation and best['dx'] == 2 and best['dy'] == 0),
                pixels=int(errors.size), symbol_errors=int(errors.sum()),
                symbol_accuracy=float(1-errors.mean()),
                worst_row_errors=int(errors.sum(axis=1).max()), levels=levels,
                note='Integer search is diagnostic; no rescaling, color correction or automatic profile change.')


def measure(source, captured):
    """Locate a session-specific target and learn this receiver's gray thresholds.

    Only publish the implemented v1 profile when its entire source ROI survives
    and all pixels pass the learned, nonoverlapping 16-level decisions.
    """
    source, captured = gray(source), gray(captured)
    ys, xs = np.mgrid[16:1064:17, 16:1904:17]
    expected = source[ys, xs].astype(float)
    expected -= expected.mean()
    norm = np.linalg.norm(expected)
    candidates = []
    for name, image in (('normal', captured), ('flip_vertical', captured[::-1])):
        for dy in range(-8, 9):
            for dx in range(-8, 9):
                values = image[ys-dy, xs-dx].astype(float)
                values -= values.mean()
                denominator = norm*np.linalg.norm(values)
                score = 0.0 if denominator == 0 else float(np.sum(expected * values) / denominator)
                candidates.append((score, name, dx, dy))
    candidates.sort(reverse=True)
    score, orientation, dx, dy = candidates[0]
    if score < 0.995 or candidates[1][0] > score-0.05:
        raise ValueError('no unambiguous calibration target')
    y0, y1, x0, x1 = 3-dy, 1080-dy, 2-dx, 1920-dx
    if not (0 <= y0 < y1 <= 1080 and 0 <= x0 < x1 <= 1920):
        raise ValueError('v1 source ROI is clipped by capture geometry')
    image = captured[::-1] if orientation == 'flip_vertical' else captured
    observed, expected = image[y0:y1, x0:x1], source[3:, 2:]
    levels = []
    for level in CODEBOOK:
        values = observed[expected == level]
        levels.append(dict(source=int(level), mean=float(values.mean()),
                           minimum=int(values.min()), maximum=int(values.max())))
    if any(a['maximum'] >= b['minimum'] for a, b in zip(levels, levels[1:])):
        raise ValueError('calibration gray levels overlap')
    thresholds = [(a['maximum']+b['minimum'])/2 for a,b in zip(levels, levels[1:])]
    errors = int(np.count_nonzero(np.searchsorted(thresholds, observed) != DECISION[expected]))
    if errors:
        raise ValueError('calibration symbol verification failed')
    return dict(profile='v1-gray16', orientation=orientation, dx=dx, dy=dy,
                roi=[y0,y1,x0,x1], thresholds=thresholds, levels=levels,
                pixels=int(expected.size), symbol_errors=errors, correlation=score)


def receive_wire(image, result):
    """Apply a receiver-owned calibration without changing v1 wire layout."""
    image = gray(image)
    if result['orientation'] == 'flip_vertical':
        image = image[::-1]
    y0,y1,x0,x1 = result['roi']
    # uint8 has only 256 possible values; preserve searchsorted's exact boundary
    # decisions while avoiding a binary search for every captured pixel.
    decisions = np.searchsorted(result['thresholds'], np.arange(256)).astype(np.uint8)
    values = decisions[image[y0:y1,x0:x1]].ravel()
    packed = (values[::2] << 4) | values[1::2]
    return packed.reshape(-1,959).T.copy().tobytes()
