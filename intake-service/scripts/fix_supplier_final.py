with open('/app/src/core/extractor/supplier.py', 'r') as f:
    content = f.read()

start = 3766
end = content.find('    def _extract_address', 3766)

new_method = '''    def _extract_name(self, lines: list[str]) -> Optional[str]:
        """Extract supplier name from first meaningful lines."""
        title_keywords = [
            'price list', 'catalog', 'catalogue', 'rate sheet', 'price sheet',
            'product list', 'product catalog', 'wholesale', 'price guide',
            'inventory', 'stock list', 'availability', 'offer', 'quotation', 'quote'
        ]
        location_keywords = [
            'dallas', 'houston', 'austin', 'san antonio', 'fort worth',
            'waco', 'temple', 'killeen', 'oklahoma', 'okc', 'tulsa',
            'new york', 'nyc', 'brooklyn', 'queens', 'manhattan',
            'new jersey', 'nj', 'chicago', 'la', 'los angeles',
            'miami', 'atlanta', 'denver', 'phoenix', 'seattle',
            'calgary', 'edmonton', 'vancouver', 'toronto', 'montreal'
        ]
        
        for line in lines[:15]:
            line = line.strip()
            if not line:
                continue
            if line.startswith(('*', '-', '•', '🔅', '✨', '$', '#')):
                continue
            if re.search(r'^\\d', line):
                continue
            line_lower = line.lower()
            if any(kw in line_lower for kw in ['price list', 'catalog', 'catalogue', 'rate sheet', 'price sheet', 'product list', 'product catalog', 'wholesale', 'price guide', 'inventory', 'stock list', 'availability', 'offer', 'quotation', 'quote']):
                continue
            if line.isupper() and len(line) > 3:
                continue
            if re.match(r'^[\\*_`#\\s]+$', line):
                continue
            if re.match(r'^[\\s\\W]+$', line):
                continue
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in ['dallas', 'houston', 'austin', 'san antonio', 'fort worth', 'waco', 'temple', 'killeen', 'oklahoma', 'okc', 'tulsa', 'new york', 'nyc', 'brooklyn', 'queens', 'manhattan', 'new jersey', 'nj', 'chicago', 'la', 'los angeles', 'miami', 'atlanta', 'denver', 'phoenix', 'seattle', 'calgary', 'edmonton', 'vancouver', 'toronto', 'montreal']):
                    continue
            if len(line) > 3 and len(line) < 200:
                name = re.sub(r'[*_`#>]', '', line).strip()
                if name and len(name) > 3:
                    return name[:200]
        return None

    def _extract_email(self, text: str) -> Optional[str]:
        match = self.EMAIL_PATTERN.search(text)
        return match.group(0) if match else None

    def _extract_phone(self, text: str) -> Optional[str]:
        match = self.PHONE_PATTERN.search(text)
        return match.group(0) if match else None

    def _extract_website(self, text: str) -> Optional[str]:
        match = self.URL_PATTERN.search(text)
        return match.group(0) if match else None

    def _extract_address(self, lines: list[str]) -> Optional[str]:
"""

with open('/app/src/core/extractor/supplier.py', 'r') as f:
    content = f.read()

start = 3766
end = content.find('    def _extract_address', 3766)

new_content = content[:3766] + '''    def _extract_name(self, lines: list[str]) -> Optional[str]:
        """Extract supplier name from first meaningful lines."""
        title_keywords = [
            'price list', 'catalog', 'catalogue', 'rate sheet', 'price sheet',
            'product list', 'product catalog', 'wholesale', 'price guide',
            'inventory', 'stock list', 'availability', 'offer', 'quotation', 'quote'
        ]
        location_keywords = [
            'dallas', 'houston', 'austin', 'san antonio', 'fort worth',
            'waco', 'temple', 'killeen', 'oklahoma', 'okc', 'tulsa',
            'new york', 'nyc', 'brooklyn', 'queens', 'manhattan',
            'new jersey', 'nj', 'chicago', 'la', 'los angeles',
            'miami', 'atlanta', 'denver', 'phoenix', 'seattle',
            'calgary', 'edmonton', 'vancouver', 'toronto', 'montreal'
        ]
        
        for line in lines[:15]:
            line = line.strip()
            if not line:
                continue
            if line.startswith(('*', '-', '•', '🔅', '✨', '$', '#')):
                continue
            if re.search(r'^\\d', line):
                continue
            line_lower = line.lower()
            if any(kw in line_lower for kw in ['price list', 'catalog', 'catalogue', 'rate sheet', 'price sheet', 'product list', 'product catalog', 'wholesale', 'price guide', 'inventory', 'stock list', 'availability', 'offer', 'quotation', 'quote']):
                continue
            if line.isupper() and len(line) > 3:
                continue
            if re.match(r'^[\\*_`#\\s]+$', line):
                continue
            if re.match(r'^[\\s\\W]+$', line):
                continue
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in ['dallas', 'houston', 'austin', 'san antonio', 'fort worth', 'waco', 'temple', 'killeen', 'oklahoma', 'okc', 'tulsa', 'new york', 'nyc', 'brooklyn', 'queens', 'manhattan', 'new jersey', 'nj', 'chicago', 'la', 'los angeles', 'miami', 'atlanta', 'denver', 'phoenix', 'seattle', 'calgary', 'edmonton', 'vancouver', 'toronto', 'montreal']):
                    continue
            if len(line) > 3 and len(line) < 200:
                name = re.sub(r'[*_`#>]', '', line).strip()
                if name and len(name) > 3:
                    return name[:200]
        return None

    def _extract_email(self, text: str) -> Optional[str]:
        match = self.EMAIL_PATTERN.search(text)
        return match.group(0) if match else None

    def _extract_phone(self, text: str) -> Optional[str]:
        match = self.PHONE_PATTERN.search(text)
        return match.group(0) if match else None

    def _extract_website(self, text: str) -> Optional[str]:
        match = self.URL_PATTERN.search(text)
        return match.group(0) if match else None

    def _extract_address(self, lines: list[str]) -> Optional[str]:
""" + content[content.find('    def _extract_address', 3766):]

with open('/app/src/core/extractor/supplier.py', 'w') as f:
    f.write(new_content)
print('Done')