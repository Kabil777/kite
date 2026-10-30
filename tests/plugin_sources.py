"""Locate explicitly checked-out optional plugins; never install or fetch them."""
import os
from pathlib import Path
from unittest import SkipTest


def gopass_source():
    source = Path(os.environ.get('KITE_GOPASS_SOURCE',
                  str(Path(__file__).resolve().parents[2] / 'kite_plugins/plugins/gopass')))
    if not (source / 'plugin.json').is_file():
        raise SkipTest('Optional Gopass checks need a sibling kite_plugins checkout or KITE_GOPASS_SOURCE')
    return source
