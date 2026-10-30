"""Stable top-level import for installed plugins."""
from importlib import import_module
from typing import Any

_api: Any = import_module('main.plugin_api')
API_VERSION = _api.API_VERSION
Action = _api.Action
Catalog = _api.Catalog
Event = _api.Event
HostServices = _api.HostServices
MenuAction = _api.MenuAction
Node = _api.Node
Plugin = _api.Plugin
Result = _api.Result
Toast = _api.Toast
View = _api.View
discover_plugins = _api.discover_plugins
user_plugin_root = _api.user_plugin_root
load_plugins = _api.load_plugins
plugin_config = _api.plugin_config
plugin_root = _api.plugin_root
unavailable_service = _api.unavailable_service
validate_view = _api.validate_view
validate_toast = _api.validate_toast

__all__ = ['API_VERSION', 'Action', 'Catalog', 'Event', 'HostServices', 'MenuAction', 'Node',
           'Plugin', 'Result', 'Toast', 'View', 'load_plugins', 'plugin_config',
           'plugin_root', 'user_plugin_root', 'discover_plugins', 'unavailable_service', 'validate_view', 'validate_toast']
