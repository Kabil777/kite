"""Check human-readable guides without executing their examples or user plugins."""
import ast
import json
from pathlib import Path
import re
from urllib.parse import unquote
from tests.check_plugin_baseline import ROOT, python_blocks
from main.plugin_api import API_VERSION


def headings(path):
    result, fence = set(), ''
    for line in path.read_text().splitlines():
        match = re.match(r'^\s*(`{3,}|~{3,})', line)
        if match:
            if not fence:
                fence = match[1]
            elif match[1][0] == fence[0] and len(match[1]) >= len(fence):
                fence = ''
        elif not fence and re.match(r'^#{1,6} ', line):
            title = re.sub(r'^#+\s+', '', line).strip().lower()
            result.add(re.sub(r'[^\w\s-]', '', title).replace(' ', '-'))
    return result


def prose(text):
    lines, fence = [], ''
    for line in text.splitlines():
        match = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if match:
            if not fence:
                fence = match[1]
            elif match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = ''
        elif not fence:
            lines.append(line)
    return re.sub(r'`+[^`]*`+', '', '\n'.join(lines))


def check_docs():
    files = [ROOT / 'README.md', *(ROOT / 'docs').glob('*.md'), *(ROOT / 'examples').rglob('*.md')]
    count = 0
    for path in files:
        text = path.read_text()
        assert '/home/kabil' not in text, 'Personal path in ' + str(path)
        for block in python_blocks(text):
            ast.parse(block); count += 1
        for block in re.findall(r'^```json\n(.*?)^```\s*$', text, re.S | re.M):
            value = json.loads(block)
            if isinstance(value, dict) and 'api_version' in value:
                assert value['api_version'] == API_VERSION
        for link in re.findall(r'\]\(([^\s)]+)\)', prose(text)):
            if re.match(r'[a-zA-Z]+:', link):
                continue
            target, _, anchor = unquote(link).partition('#')
            destination = (path.parent / target).resolve() if target else path
            assert destination.exists(), f'Broken link in {path.name}: {link}'
            if anchor and destination.suffix == '.md':
                assert anchor in headings(destination), f'Broken anchor in {path.name}: {link}'
    for retired in ('PLUGIN_API.md', 'DESIGN.md', 'plugin-api.ms', 'design.md'):
        assert not (ROOT / retired).exists(), 'Retired root guide returned'
    print(f'Documentation OK: {count} Python snippets, JSON manifests, local links/anchors, portable examples and guide layout')


if __name__ == '__main__':
    check_docs()
