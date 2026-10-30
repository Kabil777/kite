"""Safe, read-only Notion-style documents using KITE's configured colors."""
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QImage, QTextCharFormat, QTextCursor,
                           QTextDocument, QTextDocumentFragment, QTextFormat, QTextFrameFormat,
                           QTextLength, QTextOption, QTextTable, QTextTableFormat)
from PySide6.QtWidgets import QTextBrowser
from .widgets import pixel_font

MAX_MARKDOWN_BYTES = 128 * 1024


class _MarkdownDocument(QTextDocument):
    def loadResource(self, resource_type, url):
        # A valid placeholder prevents Qt's importer retrying local image paths.
        if resource_type == QTextDocument.ResourceType.ImageResource.value:
            image = QImage(1, 1, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            return image
        return QByteArray()


class MarkdownView(QTextBrowser):
    BODY_FONT = 'sans-serif'
    CODE_FONT = 'JetBrainsMono Nerd Font'

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.setDocument(_MarkdownDocument(self))
        self.BACKGROUND = colors['background']
        self.TEXT = colors['text']
        self.MUTED = colors['muted']
        self.SURFACE = colors['search']
        self.BORDER = colors['border']
        self.ACCENT = colors['accent']
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f'background: {self.BACKGROUND}; color: {self.TEXT}; border: none; '
                          f"selection-background-color: {colors['selected']};")
        self.setFont(pixel_font(self.BODY_FONT, 16))
        self.anchorClicked.connect(self.open_link)

    def resizeEvent(self, event):
        # Notion's default reading column is 720px, plus our 32px page insets.
        gutter = max(0, (self.contentsRect().width() - 784) // 2)
        self.setViewportMargins(gutter, 0, gutter, 0)
        super().resizeEvent(event)

    def open_link(self, url):
        if url.scheme().lower() in ('http', 'https') and url.host():
            QDesktopServices.openUrl(url)

    def set_markdown(self, text):
        if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_MARKDOWN_BYTES:
            raise ValueError('Markdown exceeds 128 KiB or is not text')
        document = self.document()
        document.setDefaultFont(pixel_font(self.BODY_FONT, 16))
        document.setIndentWidth(24)
        document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub
                             | QTextDocument.MarkdownFeature.MarkdownNoHTML)
        points_per_pixel = 72 / self.logicalDpiY()
        alerts = {'NOTE': 'ℹ️', 'TIP': '💡', 'IMPORTANT': '📌', 'WARNING': '⚠️', 'CAUTION': '🛑'}
        groups = []
        block = document.begin()
        while block.isValid():
            fmt = block.blockFormat()
            code = fmt.nonBreakableLines()
            quote = fmt.property(QTextFormat.Property.BlockQuoteLevel)
            label = next((name for name in alerts if block.text().startswith(f'[!{name}]')), None)
            if (code or quote) and (not groups or block.position() > groups[-1][1].position()):
                last = block
                while last.next().isValid():
                    following = last.next().blockFormat()
                    if code:
                        if (not following.nonBreakableLines()
                                or following.property(QTextFormat.Property.BlockCodeLanguage) != fmt.property(QTextFormat.Property.BlockCodeLanguage)):
                            break
                    elif (not following.property(QTextFormat.Property.BlockQuoteLevel)
                          or following.property(QTextFormat.Property.BlockQuoteLevel) < quote
                          or any(last.next().text().startswith(f'[!{name}]') for name in alerts)):
                        break
                    last = last.next()
                groups.append((block, last, None if code else label or 'QUOTE'))
            fragment = block.begin()
            while not fragment.atEnd():
                run = fragment.fragment()
                style = run.charFormat()
                inline_code = not code and 'monospace' in (style.fontFamilies() or [])
                style.setFontFamilies([self.CODE_FONT if code or inline_code else self.BODY_FONT])
                style.setFontPointSize((14 if code or inline_code else 16) * points_per_pixel)
                style.clearProperty(QTextFormat.Property.FontSizeAdjustment)
                style.setForeground(QColor(self.TEXT))
                if style.fontWeight() >= QFont.Weight.Bold:
                    style.setFontWeight(QFont.Weight.DemiBold)
                if style.isAnchor():
                    style.setForeground(QColor(self.MUTED))
                    style.setFontUnderline(True)
                elif inline_code:
                    style.setForeground(QColor(self.ACCENT))
                    style.setBackground(QColor(self.SURFACE))
                cursor = QTextCursor(document)
                cursor.setPosition(run.position())
                cursor.setPosition(run.position() + run.length(), QTextCursor.MoveMode.KeepAnchor)
                cursor.setCharFormat(style)
                fragment += 1
            fmt.setLineHeight(150, 1)  # ProportionalHeight: 16px body / 24px leading.
            fmt.setTopMargin(0)
            fmt.setBottomMargin(6)
            if code:
                fmt.setNonBreakableLines(False)
                fmt.setLineHeight(150, 1)
                fmt.setBottomMargin(0)
            elif fmt.headingLevel():
                level = fmt.headingLevel()
                fmt.setTopMargin({1: 32, 2: 26}.get(level, 20) if block.previous().isValid() else 0)
                fmt.setBottomMargin(6)
                fmt.setLineHeight(130, 1)
            elif block.textList():
                fmt.setBottomMargin(3)
            QTextCursor(block).setBlockFormat(fmt)
            if fmt.headingLevel():
                cursor = QTextCursor(block)
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                heading = QTextCharFormat()
                heading.setFontPointSize({1: 30, 2: 24, 3: 20}.get(fmt.headingLevel(), 18) * points_per_pixel)
                heading.setForeground(QColor(self.TEXT))
                heading.setFontWeight(QFont.Weight.DemiBold)
                cursor.mergeCharFormat(heading)
            block = block.next()
        for frame in document.rootFrame().childFrames():
            if isinstance(frame, QTextTable):
                fmt = frame.format().toTableFormat()
                fmt.setBorder(1)
                fmt.setBorderBrush(QColor(self.BORDER))
                fmt.setCellPadding(6)
                fmt.setCellSpacing(0)
                fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                frame.setFormat(fmt)
                for row in range(frame.rows()):
                    for column in range(frame.columns()):
                        cell = frame.cellAt(row, column)
                        if row == 0:
                            header = cell.format()
                            header.setBackground(QColor(self.SURFACE))
                            cell.setFormat(header)
                        block = cell.firstCursorPosition().block()
                        while block.isValid() and block.position() <= cell.lastCursorPosition().position():
                            compact = block.blockFormat()
                            compact.setTopMargin(0)
                            compact.setBottomMargin(0)
                            QTextCursor(block).setBlockFormat(compact)
                            block = block.next()
        # Native document frames/tables supply padding and quote rules without HTML.
        for first, last, label in reversed(groups):
            cursor = QTextCursor(first)
            cursor.setPosition(last.position() + last.length() - 1, QTextCursor.MoveMode.KeepAnchor)
            if label:
                content = QTextDocumentFragment(cursor)
                cursor.removeSelectedText()
                block_format = cursor.blockFormat()
                block_format.clearProperty(QTextFormat.Property.BlockQuoteLevel)
                block_format.setIndent(0)
                block_format.setLeftMargin(0)
                cursor.setBlockFormat(block_format)
                fmt = QTextTableFormat()
                fmt.setBorder(0)
                fmt.setBorderBrush(QColor(self.BORDER))
                fmt.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
                fmt.setBorderCollapse(True)
                fmt.setCellSpacing(0)
                fmt.setCellPadding(0)
                fmt.setTopMargin(6)
                fmt.setBottomMargin(12)
                fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                if label != 'QUOTE':
                    fmt.setBackground(QColor(self.SURFACE))
                    fmt.setColumnWidthConstraints([QTextLength(QTextLength.Type.FixedLength, 44),
                                                   QTextLength(QTextLength.Type.VariableLength, 1)])
                frame = cursor.insertTable(1, 1 if label == 'QUOTE' else 2, fmt)
                cell = frame.cellAt(0, 0 if label == 'QUOTE' else 1)
                cell_format = cell.format().toTableCellFormat()
                cell_format.setBorder(0)
                if label != 'QUOTE':
                    cell_format.setBackground(QColor(self.SURFACE))
                cell_format.setLeftPadding(14 if label == 'QUOTE' else 0)
                cell_format.setRightPadding(0 if label == 'QUOTE' else 16)
                cell_format.setTopPadding(4 if label == 'QUOTE' else 16)
                cell_format.setBottomPadding(4 if label == 'QUOTE' else 16)
                if label == 'QUOTE':
                    cell_format.setLeftBorder(3)
                    cell_format.setLeftBorderBrush(QColor(self.TEXT))
                    cell_format.setLeftBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
                cell.setFormat(cell_format)
                cell.firstCursorPosition().insertFragment(content)
                if label != 'QUOTE':
                    icon = frame.cellAt(0, 0)
                    icon_format = icon.format().toTableCellFormat()
                    icon_format.setBorder(0)
                    icon_format.setBackground(QColor(self.SURFACE))
                    icon_format.setLeftPadding(12)
                    icon_format.setTopPadding(16)
                    icon.setFormat(icon_format)
                    icon_style = QTextCharFormat()
                    icon_style.setFontFamilies([self.BODY_FONT])
                    icon_style.setFontPointSize(18 * points_per_pixel)
                    icon.firstCursorPosition().insertText(alerts[label], icon_style)
                start, end = cell.firstCursorPosition(), cell.lastCursorPosition()
            else:
                fmt = QTextFrameFormat()
                fmt.setBackground(QColor(self.SURFACE))
                fmt.setBorder(0)
                fmt.setPadding(16)
                fmt.setTopMargin(6)
                fmt.setBottomMargin(12)
                frame = cursor.insertFrame(fmt)
                start, end = frame.firstCursorPosition(), frame.lastCursorPosition()
            if label and label != 'QUOTE':
                title = QTextCursor(start)
                title.setPosition(title.position() + len(label) + 3, QTextCursor.MoveMode.KeepAnchor)
                title.insertText(label.title())
                title.insertBlock()
                if title.block().text().startswith(' '):
                    title.deleteChar()
                title = cell.firstCursorPosition()
                title.setPosition(title.position() + len(label), QTextCursor.MoveMode.KeepAnchor)
                emphasis = QTextCharFormat()
                emphasis.setForeground(QColor(self.TEXT))
                emphasis.setFontWeight(QFont.Weight.DemiBold)
                title.mergeCharFormat(emphasis)
            inner = start.block()
            while inner.isValid() and inner.position() <= end.position():
                fmt = inner.blockFormat()
                fmt.clearProperty(QTextFormat.Property.BlockQuoteLevel)
                fmt.setLeftMargin(0)
                fmt.setRightMargin(0)
                fmt.setTopMargin(0)
                fmt.setBottomMargin(6 if label and inner.next().isValid() and inner.next().position() <= end.position() else 0)
                QTextCursor(inner).setBlockFormat(fmt)
                inner = inner.next()
            for boundary in (frame.firstCursorPosition().block().previous(), frame.lastCursorPosition().block().next()):
                if boundary.isValid() and not boundary.text():
                    fmt = boundary.blockFormat()
                    fmt.setTopMargin(0)
                    fmt.setBottomMargin(0)
                    fmt.setLineHeight(1, 2)  # FixedHeight: no extra empty paragraph around blocks.
                    QTextCursor(boundary).setBlockFormat(fmt)
        document.setDocumentMargin(32)
        option = document.defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.setTextCursor(cursor)
        self.verticalScrollBar().setValue(0)
