"""Library Insights: totals, storage, date span, habits and a per-year chart, from the index only."""

import calendar

from PySide6.QtCharts import QBarCategoryAxis, QBarSet, QChart, QChartView, QStackedBarSeries, QValueAxis
from PySide6.QtCore import QMargins, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QStackedWidget,
    QToolTip, QVBoxLayout, QWidget,
)

from ...core.formatting import format_size
from ...core.models import AnalyticsResult
from ..theme.colors import Palette
from ..widgets.common import EmptyState, PageHeader


class InsightCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setProperty("role", "surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(5)
        caption = QLabel(title)
        caption.setProperty("muted", True)
        self.value = QLabel("—")
        self.value.setProperty("role", "insightValue")
        self.value.setWordWrap(True)
        self.detail = QLabel()
        self.detail.setProperty("subtle", True)
        self.detail.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        layout.addStretch(1)

    def set_value(self, value: str, detail: str) -> None:
        self.value.setText(value)
        self.detail.setText(detail)
        self.setAccessibleName(f"{value}, {detail}")


class MediaByYearChart(QChartView):
    def __init__(self, colors: Palette, parent=None):
        super().__init__(parent)
        self._colors = colors
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(265)
        self.setAccessibleName("Photo and video counts by capture year")

    def set_data(self, result: AnalyticsResult) -> None:
        chart = QChart()
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        chart.setBackgroundVisible(False)
        chart.setMargins(QMargins(0, 0, 0, 0))
        chart.legend().setVisible(False)

        if not result.media_by_year:
            self.setChart(chart)
            self.setMinimumWidth(0)
            self.set_theme_colors(self._colors)
            return

        photos = QBarSet("Photos")
        videos = QBarSet("Videos")
        for entry in result.media_by_year:
            photos.append(entry.photos)
            videos.append(entry.videos)
        series = QStackedBarSeries()
        series.append(photos)
        series.append(videos)
        chart.addSeries(series)

        years = [str(entry.year) for entry in result.media_by_year]
        categories = QBarCategoryAxis()
        categories.append(years)
        if len(years) > 12:
            categories.setLabelsAngle(-45)
        values = QValueAxis()
        maximum = max((entry.total for entry in result.media_by_year), default=1)
        values.setRange(0, max(1, maximum * 1.12))
        values.setLabelFormat("%d")
        values.applyNiceNumbers()
        chart.addAxis(categories, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(values, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(categories)
        series.attachAxis(values)

        photos.hovered.connect(lambda active, index: self._show_tooltip(active, index, "Photos", result))
        videos.hovered.connect(lambda active, index: self._show_tooltip(active, index, "Videos", result))
        self.setChart(chart)
        self.setMinimumWidth(len(years) * 48 + 90)
        self.set_theme_colors(self._colors)

    def set_theme_colors(self, colors: Palette) -> None:
        self._colors = colors
        self.setBackgroundBrush(QBrush(QColor(colors.surface)))
        chart = self.chart()
        if not chart:
            return
        muted = QBrush(QColor(colors.text_muted))
        grid = QPen(QColor(colors.border))
        chart.setTitleBrush(QBrush(QColor(colors.text)))
        chart.legend().setLabelBrush(muted)
        if not chart.series():
            return
        sets = chart.series()[0].barSets()
        if len(sets) == 2:
            sets[0].setColor(QColor(colors.accent))
            sets[1].setColor(QColor(colors.success))
            for bar_set in sets:
                bar_set.setBorderColor(QColor(colors.surface))
        for axis in chart.axes():
            axis.setLabelsBrush(muted)
            axis.setTitleBrush(muted)
            axis.setGridLinePen(grid)
            axis.setLinePen(grid)

    @staticmethod
    def _show_tooltip(active: bool, index: int, kind: str, result: AnalyticsResult) -> None:
        if not active or not 0 <= index < len(result.media_by_year):
            QToolTip.hideText()
            return
        entry = result.media_by_year[index]
        count = entry.photos if kind == "Photos" else entry.videos
        QToolTip.showText(QCursor.pos(), f"{entry.year} · {kind}: {count:,} · Total: {entry.total:,}")


class LoadingState(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "surface")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)
        title = QLabel("Loading Library Insights…")
        title.setProperty("role", "section")
        detail = QLabel("Reading the library index")
        detail.setProperty("muted", True)
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        progress.setFixedWidth(240)
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(detail, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(progress, alignment=Qt.AlignmentFlag.AlignCenter)


def _share(part: int, whole: int) -> str:
    if not whole:
        return ""
    percent = part * 100 / whole
    if 0 < percent < 1:
        return "Less than 1% of the library"
    if 99 < percent < 100:
        return "Over 99% of the library"
    return f"{percent:.0f}% of the library"


def _short_date(value) -> str:
    return f"{value:%B} {value.day}, {value.year}"


def _plural(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


class LibraryInsightsPage(QWidget):
    back_requested = Signal()
    choose_folder_requested = Signal()
    rescan_requested = Signal()
    retry_requested = Signal()

    def __init__(self, colors: Palette, parent=None):
        super().__init__(parent)
        self._colors = colors
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 24)
        outer.setSpacing(18)
        self.header = PageHeader("Library Insights", "A snapshot of everything indexed in your library")
        back = QPushButton("‹  Library")
        back.setToolTip("Back to the library (Esc)")
        back.clicked.connect(self.back_requested)
        self.header.actions.addWidget(back)
        outer.addWidget(self.header)

        self.states = QStackedWidget()
        self.no_folder = EmptyState("No library folder selected",
                                    "Choose a photo and video folder to see Library Insights.", "Choose Folder…")
        self.no_folder.action_requested.connect(self.choose_folder_requested)
        self.empty = EmptyState("Your library index is empty",
                                "Rescan the folder to find photos and videos.", "Rescan Library")
        self.empty.action_requested.connect(self.rescan_requested)
        self.loading = LoadingState()
        self.error = EmptyState("Library Insights are unavailable",
                                "The library index could not be read. Try again or rescan the library.", "Try Again")
        self.error.action_requested.connect(self.retry_requested)
        self.content = self._build_content()
        for widget in (self.no_folder, self.empty, self.loading, self.error, self.content):
            self.states.addWidget(widget)
        outer.addWidget(self.states, 1)
        self.show_no_folder()

        back_shortcut = QShortcut(QKeySequence("Esc"), self)
        back_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        back_shortcut.activated.connect(self.back_requested)

    def _build_content(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 8, 20)
        layout.setSpacing(16)
        cards = QGridLayout()
        cards.setHorizontalSpacing(14)
        cards.setVerticalSpacing(14)
        self.cards = {
            "total": InsightCard("All media"),
            "photos": InsightCard("Photos"),
            "videos": InsightCard("Videos"),
            "storage": InsightCard("Storage used"),
            "span": InsightCard("Date span"),
            "camera": InsightCard("Most used camera"),
            "year": InsightCard("Largest year"),
            "month": InsightCard("Most active month"),
            "day": InsightCard("Busiest calendar day"),
        }
        for index, card in enumerate(self.cards.values()):
            cards.addWidget(card, index // 3, index % 3)
            cards.setColumnStretch(index % 3, 1)
        layout.addLayout(cards)

        chart_surface = QFrame()
        chart_surface.setProperty("role", "surface")
        chart_layout = QVBoxLayout(chart_surface)
        chart_layout.setContentsMargins(12, 12, 12, 8)
        self.chart_title = QLabel("Media by year")
        self.chart_title.setProperty("role", "section")
        self.chart_note = QLabel()
        self.chart_note.setProperty("muted", True)
        chart_layout.addWidget(self.chart_title)
        chart_layout.addWidget(self.chart_note)
        self.chart = MediaByYearChart(self._colors)
        chart_scroll = QScrollArea()
        chart_scroll.setWidget(self.chart)
        # Fill the available width; the chart's minimum width adds a horizontal scrollbar
        # only when there are too many years to fit.
        chart_scroll.setWidgetResizable(True)
        chart_scroll.setFrameShape(QFrame.Shape.NoFrame)
        chart_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        chart_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chart_scroll.setMinimumHeight(286)
        chart_layout.addWidget(chart_scroll)
        layout.addWidget(chart_surface)
        layout.addStretch()
        scroll.setWidget(body)
        return scroll

    def set_folder_name(self, name: str) -> None:
        self.header.subtitle.setText(f"A snapshot of everything indexed in “{name}”" if name else
                                     "A snapshot of everything indexed in your library")

    def show_no_folder(self) -> None:
        self.states.setCurrentWidget(self.no_folder)

    def show_loading(self) -> None:
        self.states.setCurrentWidget(self.loading)

    def show_error(self) -> None:
        self.states.setCurrentWidget(self.error)

    def set_result(self, result: AnalyticsResult) -> None:
        if result.combined_total == 0:
            self.states.setCurrentWidget(self.empty)
            return
        total = result.combined_total
        self.cards["total"].set_value(f"{total:,}", "Photos and videos combined")
        photos_detail = _share(result.total_photos, total)
        if result.live_photos:
            photos_detail += f" · {_plural(result.live_photos, 'Live Photo')}"
        self.cards["photos"].set_value(f"{result.total_photos:,}", photos_detail)
        self.cards["videos"].set_value(f"{result.total_videos:,}", _share(result.total_videos, total))
        self.cards["storage"].set_value(format_size(result.total_bytes),
                                        f"About {format_size(result.total_bytes // total)} per item")

        if result.oldest and result.newest:
            years = result.newest.year - result.oldest.year + 1
            span = _plural(years, "year")
            detail = f"{_short_date(result.oldest)} – {_short_date(result.newest)}"
            if result.undated_count:
                detail += f" · {_plural(result.undated_count, 'item')} without a date"
            self.cards["span"].set_value(span, detail)
        else:
            self.cards["span"].set_value("—", "No capture dates were found")

        if result.top_camera:
            others = result.camera_count - 1
            detail = _plural(result.top_camera_count, "item")
            detail += f" · {_plural(others, 'other camera')}" if others else " · the only camera found"
            self.cards["camera"].set_value(result.top_camera, detail)
        else:
            self.cards["camera"].set_value("—", "No camera information in the metadata")

        year = str(result.largest_year) if result.largest_year is not None else "—"
        self.cards["year"].set_value(year, _plural(result.largest_year_count, "item"))
        month = calendar.month_name[result.most_active_month] if result.most_active_month else "—"
        self.cards["month"].set_value(month, f"{_plural(result.most_active_month_count, 'item')} across all years")
        day = f"{calendar.month_name[result.busiest_day[0]]} {result.busiest_day[1]}" if result.busiest_day else "—"
        self.cards["day"].set_value(day, f"{_plural(result.busiest_day_count, 'item')} across all years")
        self._update_chart_note(bool(result.media_by_year))
        self.chart.set_data(result)
        self.states.setCurrentWidget(self.content)

    def set_theme_colors(self, colors: Palette) -> None:
        self._colors = colors
        chart = self.chart.chart()
        self._update_chart_note(bool(chart and chart.series()))
        self.chart.set_theme_colors(colors)

    def _update_chart_note(self, has_years: bool) -> None:
        if not has_years:
            self.chart_note.setText("No capture years are available for the indexed media.")
            return
        self.chart_note.setText(
            f'<span style="color:{self._colors.accent}">■</span> Photos&nbsp;&nbsp;&nbsp;'
            f'<span style="color:{self._colors.success}">■</span> Videos&nbsp;&nbsp;&nbsp;'
            f'<span style="color:{self._colors.text_subtle}">Hover a bar for exact counts</span>'
        )
