"""Geometry for a sectioned grid: headers followed by rows of equal cells.

Pure arithmetic over section counts, so laying out 50,000 items costs the same as
laying out the few hundred sections they fall into. All lookups use binary search.
"""

from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(frozen=True, slots=True)
class SectionGeometry:
    start: int       # first item index
    count: int
    top: int         # y of the header
    cells_top: int   # y of the first row of cells
    rows: int
    bottom: int      # y just past the last row


class GridLayout:
    def __init__(self, section_counts: list[int], width: int, target_cell: int, *,
                 spacing: int = 2, margin: int = 16, header_height: int = 44, section_gap: int = 18,
                 aspect: float = 1.0, max_columns: int | None = None):
        self.width = max(1, width)
        self.spacing = spacing
        self.margin = margin
        self.header_height = header_height
        self.section_gap = section_gap
        available = max(1, self.width - 2 * margin)
        columns = max(1, (available + spacing) // (max(1, target_cell) + spacing))
        if max_columns:
            columns = min(columns, max_columns)
        self.columns = int(columns)
        # Stretch cells so each row fills the width exactly.
        self.cell_width = max(1.0, (available - (self.columns - 1) * spacing) / self.columns)
        self.cell_height = max(1.0, self.cell_width * aspect)
        self.row_height = self.cell_height + spacing

        self.sections: list[SectionGeometry] = []
        self._starts: list[int] = []
        self._tops: list[int] = []
        y = margin // 2
        start = 0
        for count in section_counts:
            rows = -(-count // self.columns) if count else 0
            cells_top = y + header_height
            bottom = cells_top + round(rows * self.row_height - (spacing if rows else 0))
            self.sections.append(SectionGeometry(start, count, y, cells_top, rows, bottom))
            self._starts.append(start)
            self._tops.append(y)
            start += count
            y = bottom + section_gap
        self.item_count = start
        self.total_height = (y - section_gap + margin) if self.sections else 0

    def section_index_for_item(self, index: int) -> int:
        return max(0, bisect_right(self._starts, index) - 1)

    def section_index_at_y(self, y: float) -> int:
        return max(0, bisect_right(self._tops, y) - 1)

    def rect_for(self, index: int) -> Rect:
        section = self.sections[self.section_index_for_item(index)]
        offset = index - section.start
        row, column = divmod(offset, self.columns)
        x = self.margin + column * (self.cell_width + self.spacing)
        y = section.cells_top + row * self.row_height
        # Round edges, not sizes, so neighbouring cells never overlap or leave gaps.
        left, top = round(x), round(y)
        return Rect(left, top, round(x + self.cell_width) - left, round(y + self.cell_height) - top)

    def header_rect(self, section_index: int) -> Rect:
        section = self.sections[section_index]
        return Rect(0, section.top, self.width, self.header_height)

    def index_at(self, x: float, y: float) -> int | None:
        if not self.sections:
            return None
        section = self.sections[self.section_index_at_y(y)]
        if y < section.cells_top or y >= section.bottom:
            return None
        row = int((y - section.cells_top) // self.row_height)
        column = int((x - self.margin) // (self.cell_width + self.spacing))
        if column < 0 or column >= self.columns:
            return None
        offset = row * self.columns + column
        if offset >= section.count:
            return None
        index = section.start + offset
        return index if self.rect_for(index).contains(x, y) else None

    def indexes_in(self, y0: float, y1: float) -> range:
        """Every item whose row overlaps the band y0..y1 (whole rows, in order)."""
        if not self.sections or y1 < y0:
            return range(0)
        first = last = None
        for section_index in range(self.section_index_at_y(y0), len(self.sections)):
            section = self.sections[section_index]
            if section.top > y1:
                break
            if not section.count or section.bottom < y0 or section.cells_top > y1:
                continue
            first_row = max(0, int((y0 - section.cells_top) // self.row_height))
            last_row = min(section.rows - 1, int((y1 - section.cells_top) // self.row_height))
            if last_row < first_row:
                continue
            if first is None:
                first = section.start + first_row * self.columns
            last = min(section.start + section.count - 1, section.start + last_row * self.columns + self.columns - 1)
        return range(0) if first is None else range(first, last + 1)

    def sections_in(self, y0: float, y1: float) -> range:
        if not self.sections:
            return range(0)
        first = self.section_index_at_y(y0)
        last = self.section_index_at_y(y1)
        return range(first, last + 1)

    def indexes_in_rect(self, x0: float, y0: float, x1: float, y1: float) -> list[int]:
        """Items whose cells intersect a rectangle (rubber-band selection)."""
        result = []
        for index in self.indexes_in(y0, y1):
            rect = self.rect_for(index)
            if rect.x < x1 and rect.x + rect.width > x0 and rect.y < y1 and rect.y + rect.height > y0:
                result.append(index)
        return result

    def nearest_index(self, x: float, y: float) -> int | None:
        """The item at a point, or the closest one in reading order (for range selection)."""
        if not self.item_count:
            return None
        section_index = self.section_index_at_y(y)
        section = self.sections[section_index]
        if y < section.cells_top:
            return section.start
        if y >= section.bottom:
            return section.start + section.count - 1
        row = min(section.rows - 1, int((y - section.cells_top) // self.row_height))
        column = min(self.columns - 1, max(0, int((x - self.margin) // (self.cell_width + self.spacing))))
        return section.start + min(section.count - 1, row * self.columns + column)

    # Keyboard navigation -------------------------------------------------

    def _position(self, index: int) -> tuple[int, int, int]:
        section_index = self.section_index_for_item(index)
        row, column = divmod(index - self.sections[section_index].start, self.columns)
        return section_index, row, column

    def index_below(self, index: int) -> int:
        section_index, row, column = self._position(index)
        section = self.sections[section_index]
        if row + 1 < section.rows:
            return min(section.start + section.count - 1, section.start + (row + 1) * self.columns + column)
        for following in self.sections[section_index + 1:]:
            if following.count:
                return following.start + min(column, following.count - 1)
        return index

    def index_above(self, index: int) -> int:
        section_index, row, column = self._position(index)
        section = self.sections[section_index]
        if row > 0:
            return section.start + (row - 1) * self.columns + column
        for previous in reversed(self.sections[:section_index]):
            if previous.count:
                last_row_start = previous.start + (previous.rows - 1) * self.columns
                return min(previous.start + previous.count - 1, last_row_start + column)
        return index
