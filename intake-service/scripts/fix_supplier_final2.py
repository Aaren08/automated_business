#!/usr/bin/env python3
"""Rewrite the _extract_name method in supplier.py with correct indentation."""

with open('/app/src/core/extractor/supplier.py', 'r') as f:
    lines = f.readlines()

# Lines to replace (0-indexed): 105 to 166 (inclusive)
start_idx = 105
end_idx = 167

new_method_lines = [
    '    def _extract_name(self, lines: list[str]) -> Optional[str]:\n',
    '        """Extract supplier name from first meaningful lines."""\n',
    '        title_keywords = [\n',
    "            'price list', 'catalog', 'catalogue', 'rate sheet', 'price sheet',\n",
    "            'product list', 'product catalog', 'wholesale', 'price guide',\n",
    "            'inventory', 'stock list', 'availability', 'offer', 'quotation', 'quote'\n",
    '        ]\n',
    '        location_keywords = [\n',
    "            'dallas', 'houston', 'austin', 'san antonio', 'fort worth',\n",
    "            'waco', 'temple', 'killeen', 'oklahoma', 'okc', 'tulsa',\n",
    "            'new york', 'nyc', 'brooklyn', 'queens', 'manhattan',\n",
    "            'new jersey', 'nj', 'chicago', 'la', 'los angeles',\n",
    "            'miami', 'atlanta', 'denver', 'phoenix', 'seattle',\n",
    "            'calgary', 'edmonton', 'vancouver', 'toronto', 'montreal'\n",
    '        ]\n',
    '\n',
    '        for line in lines[:15]:\n',
    '            line = line.strip()\n',
    '            if not line:\n',
    '                continue\n',
    '            if line.startswith(("*", "-", "\u2022", "\u2705", "\u2728", "$", "#")):\n',
    '                continue\n',
    '            if re.search(r"^\\d", line):\n',
    '                continue\n',
    '            line_lower = line.lower()\n',
    "            if any(kw in line_lower for kw in ['price list', 'catalog', 'catalogue', 'rate sheet', 'price sheet', 'product list', 'product catalog', 'wholesale', 'price guide', 'inventory', 'stock list', 'availability', 'offer', 'quotation', 'quote']):\n",
    '                continue\n',
    '            if line.isupper() and len(line) > 3:\n',
    '                continue\n',
    '            if re.match(r"^[\\*_`#\\s]+$", line):\n',
    '                continue\n',
    '            if re.match(r"^[\\s\\W]+$", line):\n',
    '                continue\n',
    "            if line.startswith('(') and line.endswith(')'):\n",
    '                content = line[1:-1].lower()\n',
    "                if any(loc in content for loc in ['dallas', 'houston', 'austin', 'san antonio', 'fort worth', 'waco', 'temple', 'killeen', 'oklahoma', 'okc', 'tulsa', 'new york', 'nyc', 'brooklyn', 'queens', 'manhattan', 'new jersey', 'nj', 'chicago', 'la', 'los angeles', 'miami', 'atlanta', 'denver', 'phoenix', 'seattle', 'calgary', 'edmonton', 'vancouver', 'toronto', 'montreal']):\n",
    '                continue\n',
    '            if len(line) > 3 and len(line) < 200:\n',
    '                name = re.sub(r"[*_`#>]", "", line).strip()\n',
    '                if name and len(name) > 3:\n',
    '                    return name[:200]\n',
    '        return None\n',
    '\n',
    '    def _extract_email(self, text: str) -> Optional[str]:\n',
    '        match = self.EMAIL_PATTERN.search(text)\n',
    '        return match.group(0) if match else None\n',
    '\n',
    '    def _extract_phone(self, text: str) -> Optional[str]:\n',
    '        match = self.PHONE_PATTERN.search(text)\n',
    '        return match.group(0) if match else None\n',
    '\n',
    '    def _extract_website(self, text: str) -> Optional[str]:\n',
    '        match = self.URL_PATTERN.search(text)\n',
    '        return match.group(0) if match else None\n',
    '\n',
    '    def _extract_address(self, lines: list[str]) -> Optional[str]:\n',
]

# Replace lines 105-166 (0-indexed, so 105 to 166 inclusive = 63 lines)
# start_idx = 105, end_idx = 167 (exclusive)
new_lines = lines[:105] + new_method_lines + lines[167:]

with open('/app/src/core/extractor/supplier.py', 'w') as f:
    f.writelines(new_lines)

print('Successfully rewrote _extract_name method!')
print(f'Total lines now: {len(new_lines)}')