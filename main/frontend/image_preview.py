"""Bounded local raster preview with resize-safe, aspect-preserving rendering."""
import os
from pathlib import Path
import stat

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 32_000_000


class ImagePreview(QLabel):
    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self._source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setStyleSheet(f"background: transparent; color: {colors['muted']}; border: none;")

    def set_source(self, source, alt=''):
        self.setAccessibleName(alt or 'Image preview')
        try:
            if not isinstance(source, str) or not source or len(source) > 4096 or '\0' in source:
                raise ValueError('Invalid image path')
            path = Path(source).expanduser()
            if not path.is_absolute():
                raise ValueError('Image path must be absolute')
            # Nonblocking open also rejects FIFOs/devices without hanging the UI.
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
                    raise ValueError('Image file exceeds limits or is not a regular file')
                data = stream.read(MAX_IMAGE_BYTES + 1)
            return self.set_data(data)
        except (OSError, ValueError):
            self.setText('Image preview unavailable')
            return False

    def set_data(self, data):
        if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
            self.setText('Image preview unavailable')
            return False
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        reader.setAutoTransform(True)
        size = reader.size()
        # SVG is excluded: a preview must not load referenced files or network resources.
        if (reader.format().data() not in {b'png', b'jpeg', b'webp', b'gif', b'bmp', b'ico', b'tiff', b'avif', b'heif'}
                or not size.isValid() or size.width() * size.height() > MAX_IMAGE_PIXELS
                or max(size.width(), size.height()) > 32768):
            self.setText('Image preview unavailable')
            return False
        image = reader.read()
        if image.isNull():
            self.setText('Image preview unavailable')
            return False
        self._source = QPixmap.fromImage(image)
        self._fit()
        return True

    def setPixmap(self, pixmap):
        # Host-generated color swatches keep QLabel's unscaled behavior.
        self._source = QPixmap()
        super().setPixmap(pixmap)

    def setText(self, text):
        self._source = QPixmap()
        super().setText(text)

    def _fit(self):
        if self._source.isNull():
            super().setPixmap(QPixmap())
            return
        bounds = QSize(max(1, self.width() * 8 // 10), max(1, self.height() * 8 // 10))
        super().setPixmap(self._source.scaled(bounds, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._source.isNull():
            self._fit()
