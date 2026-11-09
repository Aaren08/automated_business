with open('/app/src/core/extractor/supplier.py', 'r') as f:
    content = f.read()

old_method = """    def _extract_name(self, lines: list[str]) -> Optional[str]:
        \"\"\"Extract supplier name from first meaningful lines.\"\"\"
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
        
        for line in lines[:15]:  # Check first 15 lines
            line = line.strip()
            if not line:
                continue
            # Skip bullet points, prices, common noise
            if line.startswith(('*', '-', '•', '🔅', '✨', '$', '#')):
                continue
            if re.search(r'^\\d', line):
                continue
            # Skip title-like lines
            line_lower = line.lower()
            if any(kw in line_lower for kw in title_keywords):
                continue
            # Skip lines that are all caps (likely headers)
            if line.isupper() and len(line) > 3:
                continue
            # Skip lines that are just markdown formatting
            if re.match(r'^[\\*_`#\\s]+$', line):
                continue
            # Skip lines that are just emojis/symbols
            if re.match(r'^[\\s\\W]+$', line):
                continue
            # Skip lines that look like location lists (parentheses with city names)
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in location_keywords):
                    continue
            # First substantial line is likely the name
            if len(line) > 3 and len(line) < 200:
                # Clean up markdown artifacts
                name = re.sub(r'[*_`#>]', '', line).strip()
                if name and len(name) > 3:
                    return name[:200]
        return None"""

new_method = """    def _extract_name(self, lines: list[str]) -> Optional[str]:
        \"\"\"Extract supplier name from first meaningful lines.\"\"\"
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
        
        for line in lines[:15]:  # Check first 15 lines
            line = line.strip()
            if not line:
                continue
            # Skip bullet points, prices, common noise
            if line.startswith(('*', '-', '•', '🔅', '✨', '$', '#')):
                continue
            if re.search(r'^\\d', line):
                continue
            # Skip title-like lines
            line_lower = line.lower()
            if any(kw in line_lower for kw in title_keywords):
                continue
            # Skip lines that are all caps (likely headers)
            if line.isupper() and len(line) > 3:
                continue
            # Skip lines that are just markdown formatting
            if re.match(r'^[\\*_`#\\s]+$', line):
                continue
            # Skip lines that are just emojis/symbols
            if re.match(r'^[\\s\\W]+$', line):
                continue
            # Skip lines that look like location lists (parentheses with city names)
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in location_keywords):
                    continue
            # First substantial line is likely the name
            if len(line) > 3 and len(line) < 200:
                # Clean up markdown artifacts
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
        return match.group(0) if match else None"""

with open('/app/src/core/extractor/supplier.py', 'r') as f:
    content = f.read()

old_method = """    def _extract_name(self, lines: list[str]) -> Optional[str]:
        \"\"\"Extract supplier name from first meaningful lines.\"\"\"
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
        
        for line in lines[:15]:  # Check first 15 lines
            line = line.strip()
            if not line:
                continue
            # Skip bullet points, prices, common noise
            if line.startswith(('*', '-', '•', '🔅', '✨', '$', '#')):
                continue
            if re.search(r'^\\d', line):
                continue
            # Skip title-like lines
            line_lower = line.lower()
            if any(kw in line_lower for kw in title_keywords):
                continue
            # Skip lines that are all caps (likely headers)
            if line.isupper() and len(line) > 3:
                continue
            # Skip lines that are just markdown formatting
            if re.match(r'^[\\*_`#\\s]+$', line):
                continue
            # Skip lines that are just emojis/symbols
            if re.match(r'^[\\s\\W]+$', line):
                continue
            # Skip lines that look like location lists (parentheses with city names)
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in location_keywords):
                    continue
            # First substantial line is likely the name
            if len(line) > 3 and len(line) < 200:
                # Clean up markdown artifacts
                name = re.sub(r'[*_`#>]', '', line).strip()
                if name and len(name) > 3:
                    return name[:200]
        return None"""

new_method = """    def _extract_name(self, lines: list[str]) -> Optional[str]:
        \"\"\"Extract supplier name from first meaningful lines.\"\"\"
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
        
        for line in lines[:15]:  # Check first 15 lines
            line = line.strip()
            if not line:
                continue
            # Skip bullet points, prices, common noise
            if line.startswith(('*', '-', '•', '🔅', '✨', '$', '#')):
                continue
            if re.search(r'^\\d', line):
                continue
            # Skip title-like lines
            line_lower = line.lower()
            if any(kw in line_lower for kw in title_keywords):
                continue
            # Skip lines that are all caps (likely headers)
            if line.isupper() and len(line) > 3:
                continue
            # Skip lines that are just markdown formatting
            if re.match(r'^[\\*_`#\\s]+$', line):
                continue
            # Skip lines that are just emojis/symbols
            if re.match(r'^[\\s\\W]+$', line):
                continue
            # Skip lines that look like location lists (parentheses with city names)
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in location_keywords):
                    continue
            # First substantial line is likely the name
            if len(line) > 3 and len(line) < 200:
                # Clean up markdown artifacts
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
        return match.group(0) if match else None"""

with open('/app/src/core/extractor/supplier.py', 'r') as f:
    content = f.read()

old_method = '''    def _extract_name(self, lines: list[str]) -> Optional[str]:
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
        
        for line in lines[:15]:  # Check first 15 lines
            line = line.strip()
            if not line:
                continue
            # Skip bullet points, prices, common noise
            if line.startswith(('*', '-', '•', '🔅', '✨', '$', '#')):
                continue
            if re.search(r'^\\d', line):
                continue
            # Skip title-like lines
            line_lower = line.lower()
            if any(kw in line_lower for kw in title_keywords):
                continue
            # Skip lines that are all caps (likely headers)
            if line.isupper() and len(line) > 3:
                continue
            # Skip lines that are just markdown formatting
            if re.match(r'^[\\*_`#\\s]+$', line):
                continue
            # Skip lines that are just emojis/symbols
            if re.match(r'^[\\s\\W]+$', line):
                continue
            # Skip lines that look like location lists (parentheses with city names)
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in location_keywords):
                    continue
            # First substantial line is likely the name
            if len(line) > 3 and len(line) < 200:
                # Clean up markdown artifacts
                name = re.sub(r'[*_`#>]', '', line).strip()
                if name and len(name) > 3:
                    return name[:200]
        return None'''

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
        
        for line in lines[:15]:  # Check first 15 lines
            line = line.strip()
            if not line:
                continue
            # Skip bullet points, prices, common noise
            if line.startswith(('*', '-', '•', '🔅', '✨', '$', '#')):
                continue
            if re.search(r'^\\d', line):
                continue
            # Skip title-like lines
            line_lower = line.lower()
            if any(kw in line_lower for kw in title_keywords):
                continue
            # Skip lines that are all caps (likely headers)
            if line.isupper() and len(line) > 3:
                continue
            # Skip lines that are just markdown formatting
            if re.match(r'^[\\*_`#\\s]+$', line):
                continue
            # Skip lines that are just emojis/symbols
            if re.match(r'^[\\s\\W]+$', line):
                continue
            # Skip lines that look like location lists (parentheses with city names)
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in location_keywords):
                    continue
            # First substantial line is likely the name
            if len(line) > 3 and len(line) < 200:
                # Clean up markdown artifacts
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
        return match.group(0) if match else None"""

with open('/app/src/core/extractor/supplier.py', 'r') as f:
    content = f.read()

old_method = '''    def _extract_name(self, lines: list[str]) -> Optional[str]:
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
        
        for line in lines[:15]:  # Check first 15 lines
            line = line.strip()
            if not line:
                continue
            # Skip bullet points, prices, common noise
            if line.startswith(('*', '-', '•', '🔅', '✨', '$', '#')):
                continue
            if re.search(r'^\\d', line):
                continue
            # Skip title-like lines
            line_lower = line.lower()
            if any(kw in line_lower for kw in title_keywords):
                continue
            # Skip lines that are all caps (likely headers)
            if line.isupper() and len(line) > 3:
                continue
            # Skip lines that are just markdown formatting
            if re.match(r'^[\\*_`#\\s]+$', line):
                continue
            # Skip lines that are just emojis/symbols
            if re.match(r'^[\\s\\W]+$', line):
                continue
            # Skip lines that look like location lists (parentheses with city names)
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in location_keywords):
                    continue
            # First substantial line is likely the name
            if len(line) > 3 and len(line) < 200:
                # Clean up markdown artifacts
                name = re.sub(r'[*_`#>]', '', line).strip()
                if name and len(name) > 3:
                    return name[:200]
        return None'''

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
        
        for line in lines[:15]:  # Check first 15 lines
            line = line.strip()
            if not line:
                continue
            # Skip bullet points, prices, common noise
            if line.startswith(('*', '-', '•', '🔅', '✨', '$', '#')):
                continue
            if re.search(r'^\\d', line):
                continue
            # Skip title-like lines
            line_lower = line.lower()
            if any(kw in line_lower for kw in title_keywords):
                continue
            # Skip lines that are all caps (likely headers)
            if line.isupper() and len(line) > 3:
                continue
            # Skip lines that are just markdown formatting
            if re.match(r'^[\\*_`#\\s]+$', line):
                continue
            # Skip lines that are just emojis/symbols
            if re.match(r'^[\\s\\W]+$', line):
                continue
            # Skip lines that look like location lists (parentheses with city names)
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in location_keywords):
                    continue
            # First substantial line is likely the name
            if len(line) > 3 and len(line) < 200:
                # Clean up markdown artifacts
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
        return match.group(0) if match else None"""

with open('/app/src/core/extractor/supplier.py', 'r') as f:
    content = f.read()

if old_method in content:
    content = content.replace(old_method, new_method)
    with open('/app/src/core/extractor/supplier.py', 'w') as f:
        f.write(content)
    print('Replaced successfully')
else:
    print('Old method not found')