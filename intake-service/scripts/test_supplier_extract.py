#!/usr/bin/env python3
"""Test supplier name extraction"""

import sys
sys.path.insert(0, '/app')

from src.core.parser import parser_registry
from src.core.extractor import supplier_extractor
from pathlib import Path

result = parser_registry.parse(Path('/data/inbox/Batch-2026-11/supplier.md'))
text = result.text
lines = text.split('\n')

print('Lines:')
for i, line in enumerate(lines[:5]):
    line_stripped = line.strip()
    print(f'{i}: {repr(line_stripped)}')
    if line_stripped:
        bullet_chars = ('*', '-', '•', '🔅', '✨', '$', '#')
        print('  startswith bullet:', line_stripped.startswith(bullet_chars))
        print('  startswith (: ', line_stripped.startswith('('))
        print('  endswith ): ', line_stripped.endswith(')'))
        if line_stripped.startswith('(') and line_stripped.endswith(')'):
            content = line_stripped[1:-1].lower()
            print('  content:', content)
            locs = ['dallas', 'houston', 'austin', 'san antonio', 'fort worth', 'waco', 'temple', 'killeen', 'oklahoma', 'okc', 'tulsa', 'new york', 'nyc', 'brooklyn', 'queens', 'manhattan', 'new jersey', 'nj', 'chicago', 'la', 'los angeles', 'miami', 'atlanta', 'denver', 'phoenix', 'seattle', 'calgary', 'edmonton', 'vancouver', 'toronto', 'montreal']
            print('  has location:', any(loc in content for loc in locs))
    print()

# Test supplier extractor
from src.core.extractor import supplier_extractor
candidate = supplier_extractor.extract(result, 'supplier.md')
print('Supplier candidate name:', candidate.name)
print('Supplier candidate delivery_areas:', candidate.delivery_areas)