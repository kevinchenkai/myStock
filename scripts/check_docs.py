#!/usr/bin/env python3
"""Read-only checks for the documentation catalog, names, links and frozen evidence."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
CATEGORIES = {'guides', 'plans', 'research', 'records'}
PRODUCERS = {'codex', 'claude', 'cursor', 'grok', 'gpt', 'unknown', 'joint'}
STABLE = {'README.md', 'COLLABORATION.md', 'OPEN_ITEMS.md', 'GOVERNANCE.md'}
NAME = re.compile(r'([a-z0-9]+(?:-[a-z0-9]+)*)_([a-z]+)_(\d{8})\.(md|html)')


class HTMLReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        for attribute in ('href', 'src'):
            if attribute in attrs:
                self.links.append(attrs[attribute])


def references(path):
    content = path.read_text(encoding='utf-8')
    if path.suffix == '.html':
        parser = HTMLReferences()
        parser.feed(content)
        return parser.links, parser.ids
    # Examples in fenced blocks are not navigable links.
    content = re.sub(r'```.*?```|~~~.*?~~~', '', content, flags=re.S)
    links = re.findall(r'\[[^\n]*?\]\(([^\s)]+)\)', content)
    links += re.findall(r'^\s*\[[^\]]+\]:\s*(\S+)', content, flags=re.M)
    return links, []


def check():
    catalog = json.loads((DOCS / 'catalog.json').read_text())
    errors = []
    entries = catalog['documents']
    paths = [e['path'] for e in entries]
    aliases = [a for e in entries for a in e['legacy_paths']]
    for label, values in [('path', paths), ('casefold path', [p.casefold() for p in paths]), ('legacy path', aliases)]:
        errors += [f'duplicate {label}: {v}' for v, n in Counter(values).items() if n > 1]
    actual = {str(p.relative_to(ROOT)) for p in DOCS.rglob('*') if p.suffix in {'.md', '.html'}}
    if actual != set(paths):
        errors.append(f'catalog mismatch: unregistered={sorted(actual-set(paths))}, missing={sorted(set(paths)-actual)}')
    index_links, _ = references(DOCS / 'README.md')
    indexed = {(DOCS / unquote(urlsplit(link).path)).resolve() for link in index_links if not urlsplit(link).scheme}
    for entry in entries:
        path = ROOT / entry['path']
        if not entry.get('provenance'):
            errors.append(f'provenance missing: {path.name}')
        try:
            date.fromisoformat(entry['document_date'])
        except ValueError:
            errors.append(f'invalid date: {path.name}')
        if entry['producer'] not in PRODUCERS:
            errors.append(f'invalid producer: {path.name}')
        if entry['status'] not in {'current', 'historical', 'draft'}:
            errors.append(f'invalid status: {path.name}')
        if entry['category'] == 'index':
            if path.parent != DOCS or path.name not in STABLE:
                errors.append(f'invalid stable entry: {path}')
        else:
            match = NAME.fullmatch(path.name)
            if entry['category'] not in CATEGORIES or path.parent != DOCS / entry['category']:
                errors.append(f'invalid category: {path}')
            if not match or match.group(3) != entry['document_date'].replace('-', '') or match.group(2) != entry['producer']:
                errors.append(f'name and metadata disagree: {path.name}')
        if path != DOCS / 'README.md' and path.resolve() not in indexed:
            errors.append(f'not indexed: {entry["path"]}')
        for alias in entry['legacy_paths']:
            if (ROOT / alias).exists():
                errors.append(f'legacy path still exists: {alias}')
    for item in catalog.get('protected_files', []):
        path = ROOT / item['path']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            errors.append(f'frozen evidence changed: {item["path"]}')

    # Include new docs before git add, and all tracked repository Markdown files.
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    files = {ROOT / p for p in tracked if p.endswith('.md') and (ROOT / p).is_file()}
    files |= {ROOT / p for p in actual}
    count = 0
    for path in sorted(files):
        links, ids = references(path)
        if len(ids) != len(set(ids)):
            errors.append(f'duplicate HTML IDs: {path.relative_to(ROOT)}')
        for link in links:
            parts = urlsplit(link.strip('<>'))
            if parts.scheme or parts.netloc or parts.path.startswith('/'):
                continue  # External URLs and application routes are not repository paths.
            target = (path.parent / unquote(parts.path)).resolve() if parts.path else path
            count += 1
            if not target.exists():
                errors.append(f'broken link: {path.relative_to(ROOT)} -> {link}')
            elif parts.fragment and target.suffix == '.html':
                _, anchors = references(target)
                if unquote(parts.fragment) not in anchors:
                    errors.append(f'missing HTML anchor: {path.relative_to(ROOT)} -> {link}')
    if errors:
        for error in errors:
            print('ERROR:', error)
        return 1
    print(f'Docs OK: {len(entries)} documents, {len(aliases)} legacy mappings, {count} local links/anchors; frozen evidence unchanged.')
    return 0


if __name__ == '__main__':
    raise SystemExit(check())
