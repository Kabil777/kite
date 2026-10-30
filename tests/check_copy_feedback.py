"""Fake-only copy feedback and toast geometry; never writes the real clipboard."""
import os
from pathlib import Path
import tempfile
from unittest.mock import patch


def check_copy_feedback():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from main.core import DATA, Result, load_config
    from main.frontend import ToastBanner
    from main.launcher import Jobs, Launcher
    from main.plugin_api import Toast
    app = QApplication.instance() or QApplication([])
    previews = {}
    with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
        'XDG_CONFIG_HOME': temporary + '/config', 'XDG_DATA_HOME': temporary + '/data',
        'XDG_STATE_HOME': temporary + '/state', 'XDG_CACHE_HOME': temporary + '/cache',
    }):
        root = Path(temporary)
        failure = []
        requests = []
        def builtin(self, key, request, callback, timeout=2500):
            if key == 'action':
                requests.append(request)
                callback({'error': 'Simulated copy failure'} if failure else {'results': []})
            else:
                callback({'results': []})
        with patch.object(Jobs, 'builtin', builtin), patch('main.launcher.STATE', root / 'usage.json'), \
                patch('main.applications.Applications.start', lambda self: None):
            cfg = load_config(DATA / 'defaults.yaml')
            cfg['web']['enabled'] = False
            window = Launcher(cfg, favorites_path=root / 'favorites.sqlite')
            window.show(); app.processEvents()
            try:
                for identity, kind in [('math', 'copy'), ('emoji:fixture', 'copy'),
                                       ('plugin:gopass:copy-password', 'copy'), ('history:fixture', 'clip')]:
                    row = Result(identity, 'Fixture result', kind=kind, value='fixture-only')
                    window.apps = [row]
                    window.search.setText('Fixture')
                    window.render(); app.processEvents()
                    query, selected = window.search.text(), window.selected_result()
                    assert selected is not None
                    footer = window.footer.text()
                    window.activate_result(row)
                    assert window.isVisible() and window.mode == 'root', 'Copy closed or navigated KITE'
                    assert window.search.text() == query and window.selected_result() == selected
                    assert window.footer.text() == footer and window.toast.isVisible()
                    assert window.toast.text() == 'Copied to clipboard' and window.toast.timer.isActive()
                    assert 'fixture-only' not in window.toast.accessibleName(), 'Copied contents leaked into toast'
                failure.append(True)
                window.activate_result(row)
                assert window.isVisible() and window.toast.text() == 'Could not copy'
                assert not window.toast.timer.isActive()
                assert len(requests) == 5, 'Copy was retried'
                window.toast.close_button.click()
                assert not window.toast.isVisible()
                failure.clear()
                with patch.object(window, 'close', return_value=True) as close:
                    for kind in ('app', 'focus'):
                        window.activate_result(Result('fixture:' + kind, 'Fixture', kind=kind, value='fixture'))
                    assert close.call_count == 2, 'Application/window activation stopped closing KITE'
                window.render()
                assert isinstance(window.toast, ToastBanner)
                for width, height in ((750, 520), (420, 360)):
                    window.setFixedSize(width, height); app.processEvents()
                    for name, toast in [('copy', Toast('Copied to clipboard', tone='success')),
                                        ('wrapped', Toast('Could not sync passwords', 'Check your Git remote and authentication settings before retrying. ' * 4, 'error'))]:
                        window.show_toast(toast); app.processEvents()
                        banner = window.toast
                        icon, close = banner.icon.geometry(), banner.close_button.geometry()
                        text = banner.title.geometry()
                        if banner.message.isVisible():
                            assert banner.title.x() == banner.message.x()
                            text = text.united(banner.message.geometry())
                        assert abs(icon.center().y() - close.center().y()) <= 1
                        assert abs(icon.center().y() - text.center().y()) <= 1
                        assert text.left() > icon.right() and text.right() < close.left()
                        assert text.bottom() < banner.height() - 8
                        assert banner.width() <= window.panel.width() - 32
                        assert banner.x() == (window.panel.width() - banner.width()) // 2
                        assert banner.y() >= 12
                        previews[f'{name}-{width}'] = window.grab().toImage()
                assert window.toast.close_button.accessibleName() == 'Dismiss notification'
                window.toast.dismiss()
                assert not window.toast.title.text() and not window.toast.message.text()
            finally:
                window.jobs.stop(); window.close(); app.processEvents()
    print('Copy feedback OK: copies stay open, errors/success use safe toasts, app/focus still close; centered short/wrapped banners at normal/narrow widths')
    return previews


if __name__ == '__main__':
    check_copy_feedback()
