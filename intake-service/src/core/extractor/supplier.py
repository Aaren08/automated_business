"""Supplier extraction from parsed content."""
import re
from typing import Any, Optional
from dataclasses import dataclass, field

from src.core.parser.base import ParsedContent


@dataclass
class SupplierCandidate:
    """Extracted supplier information candidate."""
    name: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None
    payment_terms: Optional[str] = None
    delivery_areas: list[str] = field(default_factory=list)
    delivery_schedule: Optional[str] = None
    notes: Optional[str] = None
    extraction_confidence: float = 0.0
    extraction_notes: str = ""
    source_file: Optional[str] = None


class SupplierExtractor:
    """Extracts supplier information from parsed content."""
    
    EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    PHONE_PATTERN = re.compile(r'(\+?1?[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}')
    URL_PATTERN = re.compile(r'https?://[^\s]+')
    
    # Keywords for delivery areas
    DELIVERY_KEYWORDS = [
        'deliver', 'delivery', 'ship', 'shipping', 'service area', 
        'serve', 'coverage', 'territory', 'region', 'area'
    ]
    
    # Known cities/regions (expandable)
    KNOWN_LOCATIONS = [
        'dallas', 'houston', 'austin', 'san antonio', 'fort worth',
        'waco', 'temple', 'killeen', 'oklahoma', 'okc', 'tulsa',
        'new york', 'nyc', 'brooklyn', 'queens', 'manhattan',
        'new jersey', 'nj', 'chicago', 'la', 'los angeles',
        'miami', 'atlanta', 'denver', 'phoenix', 'seattle',
        'calgary', 'edmonton', 'vancouver', 'toronto', 'montreal'
    ]
    
    PAYMENT_TERMS_KEYWORDS = [
        'net 30', 'net 15', 'net 60', 'cod', 'cash on delivery',
        'prepaid', 'credit card', 'wire transfer', 'ach', 'terms'
    ]

    def extract(self, parsed: ParsedContent, source_file: str = None) -> SupplierCandidate:
        """Extract supplier info from parsed content."""
        candidate = SupplierCandidate(source_file=source_file)
        confidence_factors = []
        
        # Extract from text
        text = parsed.text or ""
        lines = text.split('\n')
        
        # Supplier name (first meaningful line)
        candidate.name = self._extract_name(lines)
        if candidate.name:
            confidence_factors.append(0.3)
        
        # Contact info
        candidate.email = self._extract_email(text)
        candidate.phone = self._extract_phone(text)
        candidate.website = self._extract_website(text)
        candidate.address = self._extract_address(lines)
        candidate.contact_name = self._extract_contact_name(lines)
        
        if candidate.email:
            confidence_factors.append(0.15)
        if candidate.phone:
            confidence_factors.append(0.1)
        if candidate.website:
            confidence_factors.append(0.05)
        
        # Payment terms
        candidate.payment_terms = self._extract_payment_terms(text)
        if candidate.payment_terms:
            confidence_factors.append(0.1)
        
        # Delivery areas
        candidate.delivery_areas = self._extract_delivery_areas(text, lines)
        if candidate.delivery_areas:
            confidence_factors.append(0.15)
        
        # Delivery schedule
        candidate.delivery_schedule = self._extract_delivery_schedule(text)
        if candidate.delivery_schedule:
            confidence_factors.append(0.05)
        
        # Notes
        candidate.notes = self._extract_notes(text)
        
        # Calculate confidence
        candidate.extraction_confidence = min(sum(confidence_factors), 1.0)
        
        return candidate

    def _extract_name(self, lines: list[str]) -> Optional[str]:
        """Extract supplier name from Supplier.md - only from Supplier.md file.
        
        Strategy:
        1. First, look for a line that looks like a company name (not a title, not a product line)
        2. Skip obvious titles, bullet points, product lines, locations
        3. Return the first line that looks like a company name
        """
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
        bullet_chars = ('*', '-', '•', '🔅', '✨', '$', '#')
        
        candidate_names = []
        
        for line in lines[:30]:  # Check first 30 lines
            line = line.strip()
            if not line:
                continue
            
            # Skip bullet points, prices, markdown symbols
            if line.startswith(bullet_chars):
                continue
            if re.search(r"^\d", line):
                continue
            if re.match(r"^[\*_`#\s]+$", line):
                continue
            if re.match(r"^[\s\W]+$", line):
                continue
            
            line_lower = line.lower()
            
            # Skip title-like lines
            if any(kw in line_lower for kw in [
                'price list', 'catalog', 'catalogue', 'rate sheet', 'price sheet',
                'product list', 'product catalog', 'wholesale', 'price guide',
                'inventory', 'stock list', 'availability', 'offer', 'quotation', 'quote'
            ]):
                continue
            
            # Skip all-caps lines (likely headers)
            if line.isupper() and len(line) > 3:
                continue
            
            # Skip markdown formatting lines
            if re.match(r"^[\*_`#\s]+$", line):
                continue
            
            # Skip emoji/symbol-only lines
            if re.match(r"^[\s\W]+$", line):
                continue
            
            # Skip location lines in parentheses
            if line.startswith('(') and line.endswith(')'):
                content = line[1:-1].lower()
                if any(loc in content for loc in [
                    'dallas', 'houston', 'austin', 'san antonio', 'fort worth',
                    'waco', 'temple', 'killeen', 'oklahoma', 'okc', 'tulsa',
                    'new york', 'nyc', 'brooklyn', 'queens', 'manhattan',
                    'new jersey', 'nj', 'chicago', 'la', 'los angeles',
                    'miami', 'atlanta', 'denver', 'phoenix', 'seattle',
                    'calgary', 'edmonton', 'vancouver', 'toronto', 'montreal'
                ]):
                    continue
            
            # Skip delivery/shipping lines
            line_lower = line.lower()
            if any(kw in line_lower for kw in [
                'deliver', 'delivery', 'ship', 'shipping', 'service area',
                'serve', 'coverage', 'territory', 'region', 'area',
                'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
                'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun',
                'weekday', 'weekend', 'daily', 'weekly', 'biweekly'
            ]):
                continue
            
            # Skip thank you / closing lines
            if any(kw in line_lower for kw in ['thank you', 'thanks', 'regards', 'sincerely']):
                continue
            
            # Skip lines that are just numbers/prices
            if re.match(r'^[\d\$\€\£\s\.\,\*]+$', line):
                continue
            
            # Collect candidate names - prefer lines that look like company names
            if len(line) > 3 and len(line) < 200:
                # Clean up markdown artifacts
                name = re.sub(r"[*_`#>]", "", line).strip()
                if name and len(name) > 3:
                    # Score the candidate
                    score = 0
                    # Prefer lines with proper capitalization
                    if re.search(r'[A-Z][a-z]', name):
                        score += 2
                    # Prefer lines without numbers (less likely to be product lines)
                    if not re.search(r'\d', name):
                        score += 1
                    # Prefer lines without emojis/symbols
                    if not re.search(r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF]', name):
                        score += 1
                    # Prefer longer names (more descriptive)
                    score += min(len(name) / 50, 2)
                    
                    candidate_names.append((score, name[:200]))
        
        # Return highest scoring candidate
        if candidate_names:
            candidate_names.sort(key=lambda x: -x[0])
            return candidate_names[0][1]
        
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

        address_parts = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Look for address patterns (numbers + street words)
            if re.search(r'\d+\s+\w+\s+(st|ave|ave|blvd|blvd|dr|dr|ln|ct|rd|rd|pkwy|pl)', line, re.I):
                address_parts.append(line)
            # Or lines with city, state, zip patterns
            elif re.search(r'\w+,\s*[A-Z]{2}\s*\d{5}', line):
                address_parts.append(line)
        return '; '.join(address_parts) if address_parts else None


    def _extract_address(self, lines: list[str]) -> Optional[str]:
        """Extract physical address from lines."""
        address_parts = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Look for address patterns (numbers + street words)
            if re.search(r"\d+\s+\w+\s+(st|ave|blvd|dr|ln|ct|rd|pkwy|pl)", line, re.I):
                address_parts.append(line)
            # Or lines with city, state, zip patterns
            elif re.search(r"\w+,\s*[A-Z]{2}\s*\d{5}", line):
                address_parts.append(line)
        return "; ".join(address_parts) if address_parts else None

    def _extract_contact_name(self, lines: list[str]) -> Optional[str]:
        """Extract contact person name."""
        for line in lines:
            line = line.strip().lower()
            if any(kw in line for kw in ['contact', 'attn:', 'attention:', 'rep:', 'sales:']):
                # Extract name after keyword
                for kw in ['contact', 'attn:', 'attention:', 'rep:', 'sales:']:
                    if kw in line:
                        parts = line.split(kw, 1)
                        if len(parts) > 1:
                            name = parts[1].strip(' :\t')
                            if name and len(name) > 2:
                                return name[:100]
        return None

    def _extract_payment_terms(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        for term in self.PAYMENT_TERMS_KEYWORDS:
            if term in text_lower:
                # Find the context around the term
                idx = text_lower.index(term)
                start = max(0, idx - 20)
                end = min(len(text), idx + len(term) + 30)
                context = text[start:end].strip()
                return context[:100]
        return None

    def _extract_delivery_areas(self, text: str, lines: list[str]) -> list[str]:
        areas = set()
        text_lower = text.lower()
        
        # Look for known locations in text
        for loc in self.KNOWN_LOCATIONS:
            if loc in text_lower:
                areas.add(loc.title())
        
        # Look for delivery-related lines
        for line in lines:
            line_lower = line.lower()
            if any(kw in line_lower for kw in self.DELIVERY_KEYWORDS):
                # Extract location-like words from this line
                words = re.findall(r'\b[a-zA-Z]{3,}\b', line)
                for word in words:
                    if word.lower() in self.KNOWN_LOCATIONS:
                        areas.add(word.title())
        
        return sorted(list(areas))

    def _extract_delivery_schedule(self, text: str) -> Optional[str]:
        """Extract delivery schedule/days."""
        schedule_keywords = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
                           'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun',
                           'weekday', 'weekend', 'daily', 'weekly', 'biweekly']
        text_lower = text.lower()
        
        for kw in schedule_keywords:
            if kw in text_lower:
                # Get surrounding context
                idx = text_lower.index(kw)
                start = max(0, idx - 40)
                end = min(len(text), idx + 40)
                context = text[start:end].strip()
                return context[:200]
        return None

    def _extract_notes(self, text: str) -> Optional[str]:
        """Extract general notes (first 500 chars of text as fallback)."""
        if text and len(text) > 50:
            return text[:500]
        return None


# Global instance
supplier_extractor = SupplierExtractor()