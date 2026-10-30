"""Reusable frontend/API checks; runnable with uv run python -m tests.check_frontend."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt, QUrl
from PySide6.QtGui import QImage, QTextCursor, QTextDocument
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from main.core import APP_ROOT, DATA, load_config
from main.frontend import ImagePreview, MarkdownView, input_values, render_nodes
from main.frontend.image_preview import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS
from main.plugin_api import Node, View, validate_view


def check_frontend(app):
    config = load_config(DATA / 'defaults.yaml')
    validate_view(View(nodes=(Node('markdown', 'md', {'text': '\n' * 131072}),)))
    for node in (
        Node('markdown', 'bad', {'text': 42}),
        Node('markdown', 'bad', {'text': 'é' * 65537}),
        Node('markdown', 'bad', {'text': '\ud800'}),
        Node('markdown', 'bad', {'html': '<b>no</b>'}),
        Node('markdown', 'bad', events={'change': {}}),
        Node('image_preview', 'bad'),
        Node('image_preview', 'bad', {'source': 'relative.png'}),
        Node('image_preview', 'bad', {'source': 'https://example.org/image.png'}),
        Node('image_preview', 'bad', {'source': 'file:///tmp/image.png'}),
        Node('image_preview', 'bad', {'source': '/tmp/image\0.png'}),
        Node('image_preview', 'bad', {'source': '/tmp/image.png', 'alt': 42}),
        Node('image_preview', 'bad', {'source': '/tmp/image.png'}, children=(Node('label', 'child'),)),
    ):
        try:
            validate_view(View(nodes=(node,)))
        except ValueError:
            pass
        else:
            raise AssertionError(node)
    subprocess.run([sys.executable, '-c',
                    "import sys, plugin_api; assert not any(k.startswith('PySide6') for k in sys.modules)"],
                   cwd=APP_ROOT, check=True)
    # Existing import paths stay valid while implementation lives in frontend/.
    from main.plugin_ui import render_nodes as legacy_renderer
    from main.widgets import pixel_font as legacy_font
    from main.frontend.widgets import pixel_font
    assert legacy_renderer is render_nodes and legacy_font is pixel_font

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / 'image.png'
        image = QImage(80, 40, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.red)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(buffer, 'PNG')  # pyright: ignore[reportCallIssue, reportArgumentType]
        data = bytes(buffer.data().data())
        source.write_bytes(data)
        preview = ImagePreview(config['colors'])
        preview.resize(240, 160)
        preview.show()
        app.processEvents()
        assert preview.set_source(str(source), 'Red rectangle')
        assert preview.accessibleName() == 'Red rectangle'
        assert preview.pixmap().width() == 192 and preview.pixmap().height() == 96
        preview.resize(120, 200)
        app.processEvents()
        assert preview.pixmap().width() == 96 and preview.pixmap().height() == 48
        assert not preview.set_source(str(root / 'missing.png'))
        assert preview.pixmap().isNull() and preview.text() == 'Image preview unavailable'
        assert not preview.set_data(b'not an image')
        assert not preview.set_source('https://example.org/image.png')
        assert not preview.set_source('/dev/null')
        fifo = root / 'pipe'
        os.mkfifo(fifo)
        assert not preview.set_source(str(fifo))
        large = root / 'large.png'
        with large.open('wb') as stream:
            stream.truncate(MAX_IMAGE_BYTES + 1)
        assert not preview.set_source(str(large))
        assert not preview.set_data(b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"/>')
        with patch('main.frontend.image_preview.QImageReader') as reader:
            reader.return_value.size.return_value = QSize(MAX_IMAGE_PIXELS, 2)
            reader.return_value.format.return_value = QByteArray(b'png')
            assert not preview.set_data(data)
            reader.return_value.read.assert_not_called()

        markdown = MarkdownView(config['colors'])
        markdown.resize(500, 400)
        markdown.set_markdown('''# Heading

## Section

### Subsection

Body **bold** and `code`.

> [!NOTE]
> First paragraph.
>
> Second paragraph.

```sh
printf 'hello'
[!TIP] stays literal inside code
```
''')
        headings = {}
        block = markdown.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel():
                headings[block.blockFormat().headingLevel()] = block.begin().fragment().charFormat().fontPointSize()
            block = block.next()
        assert headings[1] > headings[2] > headings[3]
        frames = markdown.document().rootFrame().childFrames()
        assert len(frames) == 2 and sorted(frame.frameFormat().padding() for frame in frames) == [0, 16]
        assert 'Note\nFirst paragraph.\nSecond paragraph.' in markdown.toPlainText()
        assert '[!TIP] stays literal inside code' in markdown.toPlainText()
        for label in ('NOTE', 'TIP', 'IMPORTANT', 'WARNING', 'CAUTION'):
            markdown.set_markdown(f'> [!{label}]\n> Body for {label}.')
            assert label.title() in markdown.toPlainText()
            assert len(markdown.document().rootFrame().childFrames()) == 1
        try:
            markdown.set_markdown('é' * 65537)
        except ValueError:
            pass
        else:
            raise AssertionError('Oversized Markdown accepted')
        for url in (QUrl.fromLocalFile(str(source)), QUrl('https://example.org/image.png'),
                    QUrl('data:image/png;base64,AAAA')):
            markdown.set_markdown(f'![blocked]({url.toString()})\n\n<img src="{url.toString()}">')
            markdown.show()
            app.processEvents()
            resource = markdown.document().resource(QTextDocument.ResourceType.ImageResource, url)
            assert isinstance(resource, QImage) and resource.size() == QSize(1, 1)
            assert resource.pixelColor(0, 0).alpha() == 0
        with patch('main.frontend.markdown.QDesktopServices.openUrl') as opened:
            for url in ('file:///tmp/private', 'javascript:alert(1)', 'data:text/plain,secret', 'https:relative', '#local'):
                markdown.anchorClicked.emit(QUrl(url))
            opened.assert_not_called()
            markdown.anchorClicked.emit(QUrl('https://example.org/guide'))
            opened.assert_called_once()

        host = QWidget()
        host.resize(500, 600)
        layout = QVBoxLayout(host)
        nodes = (Node('markdown', 'guide', {'text': '# Plugin guide'}),
                 Node('image_preview', 'picture', {'source': str(source), 'alt': 'Fixture image'}))
        validate_view(View(nodes=nodes))
        render_nodes(layout, nodes, lambda event: None, config)
        host.show()
        app.processEvents()
        assert isinstance(host.findChild(MarkdownView, 'guide'), MarkdownView)
        assert not host.findChild(ImagePreview, 'picture').pixmap().isNull()
        assert input_values(host) == {}
        host.close()
        markdown.close()
        preview.close()
    print('Frontend OK: reusable Markdown/images, bounds, safe resources, renderer, import compatibility')


if __name__ == '__main__':
    app = QApplication([])
    check_frontend(app)
