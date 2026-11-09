#!/usr/bin/env python3
"""Debug supplier name extraction"""

from src.core.parser import parser_registry
from pathlib import Path

result = parser_registry.parse(Path('/data/inbox/Batch-2026-11/supplier.md'))
text = result.text
lines = text.split('\n')

print('First 10 lines:')
for i, line in enumerate(lines[:10]):
    print(f'{i}: {repr(line)}')
    if line:
        bullet_chars = ('*', '-', '\u2022', '\U0001F525', '\u2728', '$', '#')
        print(f'  starts with bullet: {line.startswith(bullet_chars)}')
        print(f'  first char: {repr(line[0])}')
        print(f'  first char ord: {ord(line[0])}')
        print(f'  isupper: {line.isupper()}')
        print(f'  starts with (: {line.startswith("(")}')
        print(f'  ends with ): {line.endswith(")")}')
        if line.startswith('(') and line.endswith(')'):
            content = line[1:-1].lower()
            print(f'  content: {content}')
            locs = ['dallas', 'houston', 'austin', 'san antonio', 'fort worth', 'waco', 'temple', 'killeen', 'oklahoma', 'okc', 'tulsa', 'new york', 'nyc', 'brooklyn', 'queens', 'manhattan', 'new jersey', 'nj', 'chicago', 'la', 'los angeles', 'miami', 'atlanta', 'denver', 'phoenix', 'seattle', 'calgary', 'edmonton', 'vancouver', 'toronto', 'montreal']
            print(f'  has location: {any(loc in content for loc in ["dallas", "houston", "austin", "san antonio", "fort worth", "waco", "temple", "killeen", "oklahoma", "okc", "tulsa", "new york", "nyc", "brooklyn", "queens", "manhattan", "new jersey", "nj", "chicago", "la", "los angeles", "miami", "atlanta", "denver", "phoenix", "seattle", "calgary", "edmonton", "vancouver", "toronto", "montreal"])}')
    print()