"""Line-oriented dmenu input contract for the Qt launcher."""
from .core import Result

MAX_INPUT_BYTES = 1_048_576
MAX_OPTIONS = 10_000


def options_from_bytes(data: bytes) -> list[Result]:
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('dmenu input exceeds 1 MiB')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError('dmenu input must be UTF-8') from error
    lines = text.split('\n') if text else []
    if lines and text.endswith('\n'):
        lines.pop()
    if len(lines) > MAX_OPTIONS:
        raise ValueError('dmenu input exceeds 10000 options')
    if any('\0' in line or len(line) > 2048 for line in lines):
        raise ValueError('dmenu options must be NUL-free and at most 2048 characters')
    return [Result(f'dmenu:{index}', line, 'Choose item', 'system-search', 'dmenu', line)
            for index, line in enumerate(lines)]
