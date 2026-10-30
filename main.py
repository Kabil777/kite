#!/usr/bin/env python3
"""Stable launcher entry point."""
from importlib import import_module
import subprocess
import sys

import yaml

if __name__ == '__main__':
    if sys.argv[1:2] == ['plugins']:
        # Dispatch before importing the launcher: package management never needs Qt.
        sys.exit(import_module('main.plugin_manager').cli(sys.argv[2:]))
    try:
        launcher_main = import_module('main.launcher').main
        sys.exit(launcher_main())
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as error:
        print('KITE:', error, file=sys.stderr)
        subprocess.run(['notify-send', 'KITE configuration/startup error', str(error)], check=False)
        sys.exit(1)
