"""Terminal chart primitives using Rich Text and Unicode block characters.

Eighth-block characters give sub-character precision, so a bar's length carries
roughly three extra bits over one drawn in whole cells.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

BLOCKS = [' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█']


def bar_str(value: float, max_value: float, width: int, color: str = 'cyan') -> Text:
    """Render a single horizontal bar with fractional-block precision."""
    if max_value <= 0 or value <= 0:
        return Text('')
    ratio = min(value / max_value, 1.0)
    full_blocks = int(ratio * width)
    remainder = (ratio * width) - full_blocks
    partial = BLOCKS[int(remainder * 8)]
    bar = '█' * full_blocks + partial
    result = Text()
    result.append(bar, style=color)
    return result


def format_axis_label(value: float) -> str:
    """Format a Y-axis value with K/M suffixes for compact display."""
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f}M'
    if value >= 1_000:
        return f'{value / 1_000:.0f}k'
    if value == int(value):
        return str(int(value))
    return f'{value:.1f}'


def horizontal_bars(
    title: str,
    labels: list[str],
    values: list[float],
    *,
    color: str = 'yellow',
    bar_width: int = 35,
    value_suffix: str = '',
    console: Console,
) -> None:
    """Render horizontal bars with labels and value annotations."""
    max_val = max(values) if values else 0
    max_label_len = max((len(lbl) for lbl in labels), default=0)

    console.print(f'\n  [bold]{title}[/bold]')
    for label, value in zip(labels, values, strict=False):
        line = Text(f'    {label:>{max_label_len}}  ')
        line.append_text(bar_str(value, max_val, bar_width, color))
        line.append(f'  {int(value)}{value_suffix}')
        console.print(line)


def streamline(
    title: str,
    labels: list[str],
    values: list[float],
    *,
    color: str = 'cyan',
    height: int = 10,
    console: Console,
) -> None:
    """Render a line chart with box-drawing curves for smooth transitions.

    Column width adapts to label length. Each data point gets `col_width` chars
    wide, with 1-char transition columns using ╭╮╯╰│ for smooth curves.
    """
    max_val = max(values) if values else 0
    if max_val <= 0:
        console.print(f'\n  [bold]{title}[/bold]')
        console.print('    No data.')
        return

    col_width = max((len(lbl) for lbl in labels), default=2)
    col_width = max(col_width, 2)
    slot_width = col_width + 1

    heights = [max(1, round((v / max_val) * height)) for v in values]
    n = len(heights)

    console.print(f'\n  [bold]{title}[/bold]')

    for row in range(height, 0, -1):
        if row == height:
            label = format_axis_label(max_val)
        elif row == height // 2:
            label = format_axis_label(max_val / 2)
        elif row == 1:
            label = '0'
        else:
            label = ''

        line = Text(f'  {label:>5} │ ')

        for i, h in enumerate(heights):
            if row == h:
                line.append('─' * col_width, style=color)
            else:
                line.append(' ' * col_width)

            if i < n - 1:
                line.append(transition_char(row, h, heights[i + 1], color))

        console.print(line)

    axis_width = n * col_width + (n - 1)
    console.print(f'        └{"─" * (axis_width + 2)}')

    label_line = Text('         ')
    for lbl in labels:
        label_line.append(f'{lbl:<{slot_width}}')
    console.print(label_line)


def transition_char(row: int, h_left: int, h_right: int, color: str) -> Text:
    """Return the 1-char transition between two adjacent data points."""
    h_top = max(h_left, h_right)
    h_bot = min(h_left, h_right)

    if h_left == h_right:
        if row == h_left and h_left > 0:
            return Text('─', style=color)
        return Text(' ')

    if row > h_top or (row == h_top == 0):
        return Text(' ')

    if row == h_top:
        char = '╮' if h_left > h_right else '╭'
        return Text(char, style=color)

    if h_bot < row < h_top:
        return Text('│', style=color)

    if row == h_bot and h_bot > 0:
        char = '╰' if h_left > h_right else '╯'
        return Text(char, style=color)

    return Text(' ')
