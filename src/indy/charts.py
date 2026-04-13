"""Terminal chart primitives using Rich Text and Unicode block characters.

Adapted from ichrisbirch/stats/cli/charts.py — same block-character approach
for sub-character precision rendering.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

BLOCKS = [' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█']
PARTIAL_BARS = ['  ', '▁▁', '▂▂', '▃▃', '▄▄', '▅▅', '▆▆', '▇▇']


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
    console: Console | None = None,
) -> None:
    """Render horizontal bars with labels and value annotations."""
    con = console or Console()
    max_val = max(values) if values else 0
    max_label_len = max((len(lbl) for lbl in labels), default=0)

    con.print(f'\n  [bold]{title}[/bold]')
    for label, value in zip(labels, values, strict=False):
        line = Text(f'    {label:>{max_label_len}}  ')
        line.append_text(bar_str(value, max_val, bar_width, color))
        line.append(f'  {int(value)}{value_suffix}')
        con.print(line)


def vertical_bars(
    title: str,
    labels: list[str],
    values: list[float],
    *,
    color: str = 'cyan',
    height: int = 12,
    second_values: list[float] | None = None,
    second_color: str = 'magenta',
    console: Console | None = None,
) -> None:
    """Render a vertical bar chart with optional dual series."""
    con = console or Console()
    all_vals = values.copy()
    if second_values:
        all_vals.extend(second_values)
    max_val = max(all_vals) if all_vals else 0
    if max_val <= 0:
        con.print(f'\n  [bold]{title}[/bold]')
        con.print('    No data.')
        return

    con.print(f'\n  [bold]{title}[/bold]')

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

        for i, v in enumerate(values):
            append_bar_cell(line, v, max_val, height, row, color)
            if second_values:
                append_bar_cell(line, second_values[i], max_val, height, row, second_color)
            line.append(' ')

        con.print(line)

    bar_width_per = 3 if not second_values else 5
    axis_width = len(values) * bar_width_per + len(values) - 1
    con.print(f'        └{"─" * (axis_width + 2)}')

    label_line = Text('         ')
    for lbl in labels:
        short = lbl[:4]
        if second_values:
            label_line.append(f'{short:<5}')
        else:
            label_line.append(f'{short:<3}')
    con.print(label_line)


def streamline(
    title: str,
    labels: list[str],
    values: list[float],
    *,
    color: str = 'cyan',
    height: int = 10,
    console: Console | None = None,
) -> None:
    """Render an area chart with box-drawing curves for smooth transitions.

    Each data point gets 2 chars wide, with 1-char transition columns between
    them using ╭╮╯╰│ for smooth curves. Area below the line is filled.
    """
    con = console or Console()
    max_val = max(values) if values else 0
    if max_val <= 0:
        con.print(f'\n  [bold]{title}[/bold]')
        con.print('    No data.')
        return

    heights = [max(1, round((v / max_val) * height)) if v > 0 else 0 for v in values]
    n = len(heights)

    con.print(f'\n  [bold]{title}[/bold]')

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
            if row == h and h > 0:
                line.append('──', style=color)
            elif row < h:
                line.append('██', style=color)
            else:
                line.append('  ')

            if i < n - 1:
                line.append(transition_char(row, h, heights[i + 1], color))

        con.print(line)

    axis_width = n * 2 + (n - 1)
    con.print(f'        └{"─" * (axis_width + 2)}')

    label_line = Text('         ')
    for lbl in labels:
        label_line.append(f'{lbl[:3]:<3}')
    con.print(label_line)


def transition_char(row: int, h_left: int, h_right: int, color: str) -> Text:
    """Return the 1-char transition between two adjacent data points."""
    h_top = max(h_left, h_right)
    h_bot = min(h_left, h_right)

    if h_left == h_right:
        if row == h_left and h_left > 0:
            return Text('─', style=color)
        if row < h_left:
            return Text('█', style=color)
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

    if row < h_bot:
        return Text('█', style=color)

    return Text(' ')


def append_bar_cell(
    line: Text,
    value: float,
    max_val: float,
    height: int,
    row: int,
    color: str,
) -> None:
    """Append one 2-char-wide bar cell to a Text line for a given row."""
    bar_height = (value / max_val) * height
    if bar_height >= row:
        line.append('██', style=color)
    elif bar_height > row - 1:
        frac = bar_height - (row - 1)
        idx = min(int(frac * 8), 7)
        line.append(PARTIAL_BARS[idx], style=color)
    else:
        line.append('  ')
