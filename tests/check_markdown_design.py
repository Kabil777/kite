"""Native document design checks; screenshots contain only public fixture text."""
import os

SAMPLE = '''# A quieter workspace

Write **clear instructions** with `inline code` and [useful links](https://example.org).

## Start here

- Open KITE.
- Choose your next action.

> Keep your work in context.

> [!TIP]
> Copy only when ready to paste.
>
> Nothing is stored in history.

```sh
printf 'Hello, KITE'
kite clipboard
```

### Keyboard shortcuts

| Action | Shortcut |
| --- | --- |
| Copy | Enter |
| Go back | Escape |
'''


def check_markdown_design():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QColor, QFont, QFontInfo, QTextCursor, QTextTable
    from main.frontend.markdown import MarkdownView
    app = QApplication.instance() or QApplication([])
    view = MarkdownView({})
    previews = {}
    try:
        view.resize(750, 750)
        view.set_markdown(SAMPLE)
        view.show(); app.processEvents()
        plain = view.toPlainText()
        assert plain.count('Keep your work in context.') == 1, 'Quote duplicated during framing'
        assert plain.count('Copy only when ready to paste.') == 1, 'Callout duplicated during framing'
        assert '[!TIP]' not in plain and 'Tip' in plain
        assert view.document().documentMargin() == 32
        found = set()
        block = view.document().begin()
        while block.isValid():
            level = block.blockFormat().headingLevel()
            fragment = block.begin()
            while not fragment.atEnd():
                run = fragment.fragment()
                style = run.charFormat()
                px = style.fontPointSize() * view.logicalDpiY() / 72
                if level:
                    assert abs(px - {1: 30, 2: 24, 3: 20}[level]) < .01
                    assert style.foreground().color() == QColor(view.TEXT)
                    assert style.fontWeight() == QFont.Weight.DemiBold
                    assert not QFontInfo(style.font()).italic()
                    found.add('heading' + str(level))
                elif run.text() == 'inline code':
                    assert style.fontFamilies() == [view.CODE_FONT]
                    assert style.foreground().color().name() == '#eb5757'
                    assert abs(px - 14) < .01
                    found.add('inline')
                elif run.text() == 'useful links':
                    assert style.isAnchor() and style.fontUnderline()
                    assert style.foreground().color().name() == view.MUTED
                    found.add('link')
                elif run.text() == 'Write ':
                    assert style.fontFamilies() == [view.BODY_FONT] and abs(px - 16) < .01
                    assert block.blockFormat().lineHeight() == 150
                    found.add('body')
                elif run.text().startswith("printf 'Hello"):
                    assert style.fontFamilies() == [view.CODE_FONT] and abs(px - 14) < .01
                    found.add('code')
                fragment += 1
            block = block.next()
        assert found == {'heading1', 'heading2', 'heading3', 'body', 'inline', 'link', 'code'}
        frames = view.document().rootFrame().childFrames()
        assert len(frames) == 4
        quote = next(frame for frame in frames if isinstance(frame, QTextTable) and frame.columns() == 1)
        assert quote.cellAt(0, 0).format().toTableCellFormat().leftBorder() == 3
        callout = next(frame for frame in frames if isinstance(frame, QTextTable) and frame.rows() == 1 and frame.columns() == 2)
        assert callout.cellAt(0, 1).format().background().color().name() == view.SURFACE
        assert callout.cellAt(0, 1).format().toTableCellFormat().topPadding() == 16
        for width in (750, 420, 1100):
            view.resize(width, 750); app.processEvents()
            assert view.viewport().width() <= 784
            assert view.horizontalScrollBar().maximum() == 0
            for frame in view.document().rootFrame().childFrames():
                rect = view.document().documentLayout().frameBoundingRect(frame)
                assert rect.left() >= 0 and rect.right() <= view.viewport().width(), (width, rect, view.viewport().width())
            assert view.verticalScrollBar().maximum() > 0
            view.verticalScrollBar().setValue(0)
            previews[str(width)] = view.grab().toImage()
        view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
        view.set_markdown('# Fresh page\n\nNew content.')
        assert view.verticalScrollBar().value() == 0
        assert 'quieter' not in view.toPlainText()
    finally:
        view.close(); app.processEvents()
    print('Markdown design OK: neutral sans type scale, code/link distinction, nonduplicated callouts/quote rules, tables, narrow/centered reading columns and reset')
    return previews


if __name__ == '__main__':
    check_markdown_design()
