from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, Qt

from ..core.grouping import LibrarySnapshot, Section, Summary
from ..core.models import MediaItem

ItemRole = Qt.ItemDataRole.UserRole + 1      # MediaItem (the cover for summary tiles)
SummaryRole = Qt.ItemDataRole.UserRole + 2   # Summary in Years/Months modes, else None


class GridModel(QAbstractListModel):
    """Rows for the current grid mode: every item ("all") or one tile per month/year."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "all"
        self.snapshot: LibrarySnapshot | None = None
        self._rows: list = []
        self.sections: list[Section] = []

    @property
    def items(self) -> list[MediaItem]:
        return self.snapshot.items if self.snapshot else []

    def set_snapshot(self, snapshot: LibrarySnapshot | None, mode: str | None = None) -> None:
        self.beginResetModel()
        self.snapshot = snapshot
        if mode is not None:
            self.mode = mode
        self._rebuild()
        self.endResetModel()

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.beginResetModel()
        self.mode = mode
        self._rebuild()
        self.endResetModel()

    def _rebuild(self) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            self._rows, self.sections = [], []
        elif self.mode == "years":
            self._rows = snapshot.years
            self.sections = [Section(None, "", 0, len(snapshot.years))] if snapshot.years else []
        elif self.mode == "months":
            self._rows, self.sections = snapshot.months, snapshot.months_by_year
        else:
            self._rows, self.sections = snapshot.items, snapshot.month_sections

    # Qt model API ------------------------------------------------------------

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == ItemRole:
            return row.cover if isinstance(row, Summary) else row
        if role == SummaryRole:
            return row if isinstance(row, Summary) else None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return row.title if isinstance(row, Summary) else row.name
        if role == Qt.ItemDataRole.AccessibleTextRole:
            if isinstance(row, Summary):
                return f"{row.title}, {row.subtitle}"
            kind = "Video" if row.is_video else "Photo"
            when = row.captured_at.strftime("%B %d, %Y") if row.captured_at else "undated"
            return f"{kind} {row.name}, {when}"
        return None

    def item_at(self, row: int) -> MediaItem | None:
        if 0 <= row < len(self._rows):
            entry = self._rows[row]
            return entry.cover if isinstance(entry, Summary) else entry
        return None

    def summary_at(self, row: int) -> Summary | None:
        if 0 <= row < len(self._rows):
            entry = self._rows[row]
            return entry if isinstance(entry, Summary) else None
        return None

    def row_for_item_id(self, item_id: int) -> int | None:
        if self.mode != "all":
            return None
        for row, item in enumerate(self._rows):
            if item.id == item_id:
                return row
        return None

    def row_for_first_index(self, first_index: int) -> int:
        """In Years/Months mode, the tile containing the item at `first_index` of all items."""
        best = 0
        for row, summary in enumerate(self._rows):
            if summary.first_index <= first_index:
                best = row
            else:
                break
        return best
