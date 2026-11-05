"""Settings contracts and native editors are shared across unrelated backend languages."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch


def check():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication, QPushButton
    from main.plugin_api import _read_manifest
    from main.plugin_settings import default, validate_fields, validate_values
    from main.frontend.settings import SettingsFields
    app = QApplication.instance() or QApplication([])
    fields = [
        {'key': 'name', 'label': 'Name', 'type': 'text', 'default': 'sample'},
        {'key': 'mode', 'label': 'Mode', 'type': 'choice', 'options': ['one', 'two']},
        {'key': 'enabled', 'label': 'Enabled', 'type': 'boolean', 'default': False},
        {'key': 'count', 'label': 'Count', 'type': 'number', 'integer': True, 'min': 0, 'max': 10, 'default': 0},
        {'key': 'time', 'label': 'Time', 'type': 'number', 'optional': True, 'default': None},
        {'key': 'dirs', 'label': 'Folders', 'type': 'directory_list', 'default': []},
        {'key': 'files', 'label': 'Files', 'type': 'file_list', 'default': []},
        {'key': 'tags', 'label': 'Tags', 'type': 'string_list', 'default': []},
        {'key': 'folder', 'label': 'Folder', 'type': 'directory', 'default': ''},
        {'key': 'file', 'label': 'File', 'type': 'file', 'default': ''},
        {'key': 'color', 'label': 'Color', 'type': 'color', 'default': '000000ff'},
    ]
    validate_fields(fields)
    values = validate_values(fields, {'dirs': '["/tmp/fixture images"]'})
    assert values['dirs'] == ['/tmp/fixture images'] and values['count'] == 0
    assert values['enabled'] is False and values['time'] is None
    editor = SettingsFields(fields, values)
    assert editor.values() == values
    editor.editors['count'].setValue(2)
    assert editor.values()['count'] == 2 and 'qt_spinbox_lineedit' not in editor.values()
    editor.inherit['time'].setChecked(False)
    editor.editors['time'].setValue(0.5)
    assert editor.values()['time'] == .5
    with patch('main.frontend.settings.QFileDialog.getExistingDirectory', return_value='/fixture/selected'):
        browse = next(button for button in editor.editors['dirs'].parentWidget().findChildren(QPushButton) if button.text() == 'Browse…')
        browse.click()
        assert '/fixture/selected' in editor.values()['dirs']
        editor.editors['folder'].parentWidget().findChild(QPushButton).click()
        assert editor.values()['folder'] == '/fixture/selected'
    with patch('main.frontend.settings.QColorDialog.getColor', return_value=QColor(17, 34, 51, 68)):
        editor.editors['color'].parentWidget().findChild(QPushButton).click()
        assert editor.values()['color'] == '11223344'
    required = [{'key': 'required', 'label': 'Required', 'type': 'text', 'required': True}]
    validate_fields(required)
    try:
        validate_values(required, {})
    except ValueError:
        pass
    else:
        raise AssertionError('Missing required values accepted')
    for key, invalid in [('count', True), ('count', float('nan')), ('count', 11), ('mode', 'invalid'), ('color', 'xyz'), ('dirs', 'bad')]:
        try:
            validate_values(fields, {key: invalid})
        except ValueError:
            pass
        else:
            raise AssertionError((key, invalid))
    for field in ({'key': 'bad', 'label': 'Bad', 'type': []},
                  {'key': 'bad', 'label': 'Bad', 'type': 'script'},
                  {'key': 'bad', 'label': 'Bad', 'type': 'choice', 'options': []}):
        try:
            validate_fields([field])
        except ValueError:
            pass
        else:
            raise AssertionError(field)
    broken = SettingsFields([fields[5]], {'dirs': 'bad legacy input'})
    try:
        broken.values()
    except ValueError:
        pass
    else:
        raise AssertionError('Malformed stored values must not silently reset')
    with tempfile.TemporaryDirectory() as temporary:
        for identifier, entry in [('python-fixture', {'entry': '__init__.py'}), ('native-fixture', {'command': ['/never/execute-this']})]:
            package = Path(temporary) / identifier
            package.mkdir()
            (package / 'plugin.json').write_text(json.dumps({'id': identifier, 'name': identifier, 'api_version': 2,
                                                           'configuration': fields, **entry}))
            (package / '__init__.py').write_text('raise AssertionError("do not import")')
            with patch('subprocess.Popen', side_effect=AssertionError('do not execute')):
                assert _read_manifest(package)['configuration'] == fields
    editor.deleteLater()
    broken.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    print('Typed Settings: contracts, native values, legacy lists and unrelated Python/executable metadata passed')


if __name__ == '__main__':
    check()
