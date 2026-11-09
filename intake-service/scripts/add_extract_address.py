#!/usr/bin/env python3
"""Add _extract_address method to supplier.py"""

with open('/app/src/core/extractor/supplier.py', 'r') as f:
    lines = f.readlines()

# Find _extract_contact_name method
insert_idx = -1
for i, line in enumerate(lines):
    if 'def _extract_contact_name' in line:
        insert_idx = i
        break

if insert_idx == -1:
    print("ERROR: _extract_contact_name not found")
    exit(1)

# Method to insert
new_method = [
    '\n',
    '    def _extract_address(self, lines: list[str]) -> Optional[str]:\n',
    '        """Extract physical address from lines."""\n',
    '        address_parts = []\n',
    '        for line in lines:\n',
    '            line = line.strip()\n',
    '            if not line:\n',
    '                continue\n',
    '            # Look for address patterns (numbers + street words)\n',
    '            if re.search(r"\\d+\\s+\\w+\\s+(st|ave|blvd|dr|ln|ct|rd|pkwy|pl)", line, re.I):\n',
    '                address_parts.append(line)\n',
    '            # Or lines with city, state, zip patterns\n',
    '            elif re.search(r"\\w+,\\s*[A-Z]{2}\\s*\\d{5}", line):\n',
    '                address_parts.append(line)\n',
    '        return "; ".join(address_parts) if address_parts else None\n',
    '\n',
]

# Insert before _extract_contact_name
insert_idx_0based = lines.index(next(l for l in lines if 'def _extract_contact_name' in l))
new_lines = lines[:insert_idx] + new_method + lines[insert_idx:]

with open('/app/src/core/extractor/supplier.py', 'w') as f:
    f.writelines(new_lines)

print(f'Inserted _extract_address at line {insert_idx}')
print(f'Total lines now: {len(lines) + len(new_method)}')