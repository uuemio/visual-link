"""Read selected DirectShow output pin capabilities without opening an OpenCV graph."""
import ctypes as c
import uuid


class Guid(c.Structure):
    _fields_ = [('data', c.c_ubyte * 16)]

    def __init__(self, value):
        super().__init__((c.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))


class MediaType(c.Structure):
    _fields_ = [('major', Guid), ('subtype', Guid), ('fixed', c.c_int32),
                ('compressed', c.c_int32), ('sample_size', c.c_uint32),
                ('format_type', Guid), ('unknown', c.c_void_p),
                ('format_size', c.c_uint32), ('format', c.c_void_p)]


def _method(pointer, index, result, *arguments):
    table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(result, c.c_void_p, *arguments)(table[index])


def _call(pointer, index, *arguments):
    types, values = zip(*arguments) if arguments else ((), ())
    return _method(pointer, index, c.c_int32, *types)(pointer, *values)


def _check(result):
    if result < 0:
        raise OSError(f'DirectShow HRESULT {result & 0xffffffff:#x}')


def _release(pointer):
    if pointer:
        _method(pointer, 2, c.c_uint32)(pointer)


def _query(pointer, iid):
    result = c.c_void_p()
    _check(_call(pointer, 0, (c.c_void_p, c.byref(Guid(iid))),
                 (c.c_void_p, c.byref(result))))
    return result


def _next(enumerator):
    result, count = c.c_void_p(), c.c_uint32()
    rc = _call(enumerator, 3, (c.c_uint32, 1), (c.c_void_p, c.byref(result)),
               (c.c_void_p, c.byref(count)))
    _check(rc)
    return result if count.value else None


def _describe(media):
    value = media.contents
    subtype = str(uuid.UUID(bytes_le=bytes(value.subtype.data)))
    fmt = str(uuid.UUID(bytes_le=bytes(value.format_type.data)))
    # FORMAT_VideoInfo / FORMAT_VideoInfo2: AvgTimePerFrame at byte 40.
    offset = {'05589f80-c356-11ce-bf01-00aa0055595a': 48,
              'f72a76a0-eb0a-11d0-ace4-0000c0cc16ba': 72}.get(fmt)
    if offset is None or value.format_size < offset + 40:
        return None
    width = c.c_int32.from_address(value.format + offset + 4).value
    height = c.c_int32.from_address(value.format + offset + 8).value
    interval = c.c_int64.from_address(value.format + 40).value
    code = bytes(value.subtype.data)[:4]
    name = 'MEDIASUBTYPE_RGB24' if subtype == 'e436eb7d-524f-11ce-9f53-0020af0ba770' else (
        code.decode('ascii') if all(32 <= b < 127 for b in code) else subtype)
    return dict(width=abs(width), height=abs(height), format=name, subtype=subtype,
                fps=10000000 / interval if interval > 0 else None,
                frame_interval_100ns=interval, bottom_up=height > 0)


def capabilities(device):
    """Enumerate IAMStreamConfig media types; declared rates are driver ranges."""
    ole = c.WinDLL('ole32')
    ole.CoTaskMemFree.argtypes = [c.c_void_p]
    initialized = ole.CoInitializeEx(None, 0) >= 0
    owned = []

    def own(pointer):
        owned.append(pointer)
        return pointer

    def free_media(media):
        if media:
            if media.contents.unknown:
                _release(media.contents.unknown)
            ole.CoTaskMemFree(media.contents.format)
            ole.CoTaskMemFree(media)

    try:
        enum = c.c_void_p()
        _check(ole.CoCreateInstance(c.byref(Guid('62be5d10-60eb-11d0-bd3b-00a0c911ce86')),
                                   None, 1, c.byref(Guid('29840822-5b84-11d0-bd3b-00a0c911ce86')),
                                   c.byref(enum)))
        own(enum)
        devices = c.c_void_p()
        rc = _call(enum, 3, (c.c_void_p, c.byref(Guid('860bb310-5d01-11d0-bd3b-00a0c911ce86'))),
                   (c.c_void_p, c.byref(devices)), (c.c_uint32, 0))
        _check(rc)
        if not devices:
            raise ValueError('no DirectShow video capture devices')
        own(devices)
        for index in range(device + 1):
            moniker = _next(devices)
            if not moniker:
                raise ValueError('DirectShow device index out of range')
            own(moniker)
        camera = c.c_void_p()
        _check(_call(moniker, 8, (c.c_void_p, None), (c.c_void_p, None),
                     (c.c_void_p, c.byref(Guid('56a86895-0ad4-11ce-b03a-0020af0ba770'))),
                     (c.c_void_p, c.byref(camera))))
        own(camera)
        pins = c.c_void_p()
        _check(_call(camera, 10, (c.c_void_p, c.byref(pins))))
        own(pins)
        rows, current = [], None
        while True:
            pin = _next(pins)
            if not pin:
                break
            own(pin)
            direction = c.c_int32()
            _check(_call(pin, 9, (c.c_void_p, c.byref(direction))))
            if direction.value != 1:
                continue
            try:
                stream = own(_query(pin, 'c6e13340-30ac-11d0-a18c-00a0c9118956'))
            except OSError:
                continue
            count, size = c.c_int32(), c.c_int32()
            _check(_call(stream, 5, (c.c_void_p, c.byref(count)), (c.c_void_p, c.byref(size))))
            if not 0 < size.value <= 65536 or not 0 <= count.value <= 65536:
                raise ValueError('invalid DirectShow capability buffer')
            buffer = c.create_string_buffer(size.value)
            media = c.POINTER(MediaType)()
            try:
                _check(_call(stream, 4, (c.c_void_p, c.byref(media))))
                current = _describe(media)
            finally:
                free_media(media)
            for index in range(count.value):
                media = c.POINTER(MediaType)()
                try:
                    _check(_call(stream, 6, (c.c_int32, index), (c.c_void_p, c.byref(media)),
                                 (c.c_void_p, buffer)))
                    row = _describe(media)
                    if row:
                        # VIDEO_STREAM_CONFIG_CAPS contains frame interval limits at 104,112.
                        if size.value >= 120:
                            low = c.c_int64.from_buffer(buffer, 104).value
                            high = c.c_int64.from_buffer(buffer, 112).value
                            if low > 0 and high >= low:
                                row['fps_min'], row['fps_max'] = 1e7 / high, 1e7 / low
                        if row not in rows:
                            rows.append(row)
                finally:
                    free_media(media)
            # First capture output is the one used by OpenCV's DirectShow graph.
            break
        if not rows:
            raise ValueError('selected DirectShow device exposes no IAMStreamConfig video modes')
        return dict(device=device, api='DirectShow/IAMStreamConfig', declared=rows,
                    current_output=current, current_output_scope='unconnected capture pin; not HDMI input timing')
    finally:
        for pointer in reversed(owned):
            _release(pointer)
        if initialized:
            ole.CoUninitialize()
