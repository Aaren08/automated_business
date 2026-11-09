#!/usr/bin/env python3
"""Fix the emoji in supplier.py"""

with open('/app/src/core/extractor/supplier.py', 'r') as f:
    content = f.read()

# Fix the emoji in the startswith call
old_line = 'if line.startswith(("*", "-", "•", "✅", "✨", "$", "#")):'
new_line = 'if line.startswith(("*", "-", "•", "🔅", "✨", "$", "#")):'

if old_line in content:
    content = content.replace(old_line, new_line)
    with open('/app/src/core/extractor/supplier.py', 'w') as f:
        f.write(content)
    print('Fixed!')
else:
    print('Line not found')
    # Search for the line
    idx = content.find('if line.startswith(("*", "-", "•"')
    if idx >= 0:
        print('Found at:', idx)
        print(repr(content[idx:idx+100]))
    else:
        print('Not found')