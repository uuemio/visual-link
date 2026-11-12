"""Immutable gray16 geometry shared by each direction's complete data path."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageParameters:
    width: int = 1920
    height: int = 1080
    top: int = 3
    offset: int = 2

    def __post_init__(self):
        if (any(type(v) is not int for v in (self.width, self.height, self.top, self.offset))
                or (self.width, self.height) not in ((1920, 1080), (1920, 1200))
                or (self.top, self.offset) != (3, 2)
                or self.active_width % 2):
            raise ValueError('unsupported gray16 image geometry')

    @property
    def shape(self):
        return self.height, self.width

    @property
    def active_width(self):
        return self.width - self.offset

    @property
    def active_height(self):
        return self.height - self.top

    @property
    def pixels(self):
        return self.active_width * self.active_height

    @property
    def tx_roi(self):
        return self.top, self.height, self.offset, self.width

    @property
    def rx_roi(self):
        return self.top, self.height, 0, self.active_width

    @property
    def interleave_shape(self):
        return self.active_width // 2, self.active_height

    @property
    def wire_bytes(self):
        return self.pixels // 2

    @property
    def capacity(self):
        return ((self.wire_bytes - 255) // 255) * 247


DEFAULT_IMAGE = ImageParameters()


def image_parameters(config, direction):
    """Parse the boundary config; tx_mode/rx_mode override its common geometry."""
    if direction not in ('tx', 'rx'):
        raise ValueError('image direction must be tx or rx')
    mode = config.get(f'{direction}_mode', {})
    if not isinstance(mode, dict) or set(mode) - {'width', 'height'}:
        raise ValueError('mode must contain width/height only')
    return ImageParameters(width=mode.get('width', config.get('width', 1920)),
                           height=mode.get('height', config.get('height', 1080)),
                           top=config.get('top', 3), offset=config.get('offset', 2))
