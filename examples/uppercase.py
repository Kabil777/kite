#!/usr/bin/env python3
"""Trusted extension example: JSON request on stdin, JSON results on stdout."""
import json
import sys

request = json.load(sys.stdin)
text = request['query'].upper()
print(json.dumps({'results': [{
    'title': text or 'Type text to uppercase',
    'subtitle': 'Enter to copy',
    'icon': 'edit-copy',
    'action': {'type': 'copy', 'value': text},
}]}))
