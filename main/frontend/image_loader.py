"""Bounded background raster decoding; workers never create widgets or QPixmaps."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import stat
from threading import Lock
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QSize, Signal, Slot
from PySide6.QtGui import QImageReader
from .image_preview import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, RASTER_FORMATS


class ImageLoader(QObject):
    finished = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='kite-images')
        self.pending, self.running = OrderedDict(), {}
        self.sequence, self.closed = 0, False
        self.cache, self.cache_bytes, self.lock = OrderedDict(), 0, Lock()
        self.finished.connect(self.completed)

    def cancel(self, group):
        self.pending = OrderedDict((key, job) for key, job in self.pending.items() if job[0] != group)
        for key, job in list(self.running.items()):
            if job[0] == group:
                self.running[key] = (group, None)

    def request(self, source, size, callback, group='preview'):
        if self.closed:
            return
        if group == 'preview':
            self.cancel(group)
        self.sequence += 1
        self.pending[self.sequence] = (group, source, size, callback)
        if group == 'preview':
            self.pending.move_to_end(self.sequence, last=False)
        while len(self.pending) > 64:
            self.pending.popitem()
        self.pump()

    def pump(self):
        while self.pending and len(self.running) < 2 and not self.closed:
            identifier, (group, source, size, callback) = self.pending.popitem(last=False)
            self.running[identifier] = (group, callback)
            def work(key=identifier, data=source, target=size):
                try:
                    result = self.decode(data, target)
                    error = ''
                except Exception as exception:
                    result, error = None, str(exception)[:256]
                if not self.closed:
                    self.finished.emit(key, result, error)
            self.pool.submit(work)

    def decode(self, source, target):
        if isinstance(source, bytes):
            data, identity = source, None
        else:
            path = Path(source)
            if not path.is_absolute() or len(str(path)) > 4096:
                raise ValueError('Invalid local image path')
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_IMAGE_BYTES:
                    raise ValueError('Preview unavailable: file exceeds safety limits or is not regular')
                identity = (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, target)
                with self.lock:
                    if identity in self.cache:
                        self.cache.move_to_end(identity)
                        return self.cache[identity]
                data = stream.read(MAX_IMAGE_BYTES + 1)
                after = os.fstat(stream.fileno())
                if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                    raise ValueError('Image changed during preview loading')
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError('Image exceeds preview byte limit')
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        reader.setAutoTransform(True)
        if reader.format().data() not in RASTER_FORMATS:
            raise ValueError('Preview format unavailable')
        size = reader.size()
        if (not size.isValid() or size.width() * size.height() > MAX_IMAGE_PIXELS or max(size.width(), size.height()) > 32768):
            raise ValueError('Preview format/dimensions unavailable')
        bound = QSize(max(1, min(2048, target[0])), max(1, min(2048, target[1])))
        from PySide6.QtCore import Qt
        scaled = size.scaled(bound, Qt.AspectRatioMode.KeepAspectRatio)
        if scaled.width() < size.width() or scaled.height() < size.height():
            reader.setScaledSize(scaled)
        image = reader.read()
        if image.isNull():
            raise ValueError('Image decoder unavailable or invalid raster')
        result = image, (size.width(), size.height())
        if identity is not None:
            cost = image.sizeInBytes()
            with self.lock:
                if self.closed:
                    return result
                previous = self.cache.pop(identity, None)
                if previous:
                    self.cache_bytes -= previous[0].sizeInBytes()
                self.cache[identity] = result
                self.cache_bytes += cost
                while self.cache and self.cache_bytes > 32 * 1024 * 1024:
                    _, old = self.cache.popitem(last=False)
                    self.cache_bytes -= old[0].sizeInBytes()
        return result

    @Slot(object, object, object)
    def completed(self, identifier, result, error):
        job = self.running.pop(identifier, None)
        if job and job[1] is not None and not self.closed:
            job[1](result, error)
        self.pump()

    def close(self):
        self.closed = True
        self.pending.clear()
        self.running.clear()
        self.pool.shutdown(wait=False, cancel_futures=True)
        with self.lock:
            self.cache.clear()
            self.cache_bytes = 0
