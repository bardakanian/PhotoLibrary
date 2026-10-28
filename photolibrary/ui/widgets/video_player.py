from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from ...core.formatting import format_duration

PLAY, PAUSE = "▶", "❚❚"


class VideoPlayer(QWidget):
    """In-app playback: play/pause, a scrubber, time labels, volume and mute."""

    open_externally_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self._scrubbing = False
        self._resume_after_scrub = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.screen_stack = QStackedWidget()
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: #0B0C0E;")
        self.player.setVideoOutput(self.video)
        self.screen_stack.addWidget(self.video)
        failure = QWidget()
        failure_layout = QVBoxLayout(failure)
        failure_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.failure_label = QLabel("This video can't be played here.")
        self.failure_label.setStyleSheet("color: #A6ADBA; font-size: 15px;")
        self.failure_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.failure_label.setWordWrap(True)
        open_button = QPushButton("Open in Default App")
        open_button.clicked.connect(self.open_externally_requested)
        failure_layout.addWidget(self.failure_label)
        failure_layout.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignCenter)
        self.screen_stack.addWidget(failure)
        layout.addWidget(self.screen_stack, 1)

        controls = QFrame()
        controls.setProperty("role", "viewerBar")
        bar = QHBoxLayout(controls)
        bar.setContentsMargins(16, 8, 16, 10)
        bar.setSpacing(10)
        self.play_button = self._button(PLAY, "Play or pause (Space)")
        self.play_button.clicked.connect(self.toggle_play)
        bar.addWidget(self.play_button)
        self.position_label = QLabel("0:00")
        self.position_label.setMinimumWidth(44)
        bar.addWidget(self.position_label)
        self.scrubber = QSlider(Qt.Orientation.Horizontal)
        self.scrubber.setAccessibleName("Playback position")
        self.scrubber.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scrubber.setRange(0, 0)
        self.scrubber.sliderPressed.connect(self._scrub_started)
        self.scrubber.sliderMoved.connect(self._scrub_moved)
        self.scrubber.sliderReleased.connect(self._scrub_finished)
        self.scrubber.actionTriggered.connect(self._scrubber_clicked)
        bar.addWidget(self.scrubber, 1)
        self.duration_label = QLabel("0:00")
        self.duration_label.setProperty("muted", True)
        self.duration_label.setMinimumWidth(44)
        bar.addWidget(self.duration_label)
        self.mute_button = self._button("🔊", "Mute (M)")
        self.mute_button.setCheckable(True)
        self.mute_button.toggled.connect(self._set_muted)
        bar.addWidget(self.mute_button)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setAccessibleName("Volume")
        self.volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(96)
        self.volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        bar.addWidget(self.volume)
        layout.addWidget(controls)

        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.errorOccurred.connect(self._failed)

    @staticmethod
    def _button(text: str, tooltip: str) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setProperty("compact", True)
        # Keyboard shortcuts belong to the viewer, not to whichever button was last clicked.
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    # Playback --------------------------------------------------------------

    def load(self, path: Path, autoplay: bool = True) -> None:
        self.screen_stack.setCurrentIndex(0)
        self.scrubber.setRange(0, 0)
        self.position_label.setText("0:00")
        self.duration_label.setText("0:00")
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        if autoplay:
            self.player.play()

    def stop(self) -> None:
        self.player.stop()
        # Release the file so it can be moved or deleted.
        self.player.setSource(QUrl())

    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
                self.player.setPosition(0)
            self.player.play()

    def seek_by(self, milliseconds: int) -> None:
        duration = self.player.duration()
        self.player.setPosition(max(0, min(duration or 0, self.player.position() + milliseconds)))

    def toggle_mute(self) -> None:
        self.mute_button.toggle()

    def _set_muted(self, muted: bool) -> None:
        self.audio.setMuted(muted)
        self.mute_button.setText("🔇" if muted else "🔊")

    # Signals from the player and scrubber ---------------------------------

    def _position_changed(self, position: int) -> None:
        if not self._scrubbing:
            self.scrubber.setValue(position)
        self.position_label.setText(format_duration(position / 1000))

    def _duration_changed(self, duration: int) -> None:
        self.scrubber.setRange(0, max(0, duration))
        self.scrubber.setPageStep(max(1000, duration // 20))
        self.duration_label.setText(format_duration(duration / 1000))

    def _state_changed(self, state) -> None:
        self.play_button.setText(PAUSE if state == QMediaPlayer.PlaybackState.PlayingState else PLAY)

    def _failed(self, _error, message: str) -> None:
        self.failure_label.setText("This video can't be played here." + (f"\n{message}" if message else ""))
        self.screen_stack.setCurrentIndex(1)

    def _scrub_started(self) -> None:
        self._scrubbing = True
        self._resume_after_scrub = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.player.pause()

    def _scrub_moved(self, value: int) -> None:
        self.player.setPosition(value)

    def _scrub_finished(self) -> None:
        self._scrubbing = False
        self.player.setPosition(self.scrubber.value())
        if self._resume_after_scrub:
            self.player.play()

    def _scrubber_clicked(self, action: int) -> None:
        # Clicking the groove pages the slider; jump the player to match.
        if not self._scrubbing and action in (QSlider.SliderAction.SliderPageStepAdd, QSlider.SliderAction.SliderPageStepSub):
            self.player.setPosition(self.scrubber.sliderPosition())
