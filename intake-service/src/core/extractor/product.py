"""Product candidate extraction from parsed content."""
import re
from typing import Any, Optional
from dataclasses import dataclass, field
from pathlib import Path

from src.core.parser.base import ParsedContent


@dataclass
class ProductCandidate:
    """Extracted product candidate with provenance."""
    # Core identification
    supplier_sku: Optional[str] = None
    supplier_product_name: Optional[str] = None
    
    # Canonical fields
    canonical_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    
    # Attributes
    attributes: dict[str, Any] = field(default_factory=dict)
    
    # Pricing
    price_amount: Optional[float] = None
    price_currency: Optional[str] = None
    price_unit: Optional[str] = None
    price_min_order_qty: Optional[int] = None
    price_valid_from: Optional[str] = None
    price_valid_to: Optional[str] = None
    
    # Availability
    availability_status: Optional[str] = None
    availability_quantity: Optional[int] = None
    availability_lead_time_days: Optional[int] = None
    
    # Shipping
    delivery_terms: Optional[str] = None
    delivery_origin: Optional[str] = None
    delivery_destination: Optional[str] = None
    shipping_weight_kg: Optional[float] = None
    shipping_dimensions_cm: Optional[dict[str, float]] = None
    
    # Packaging
    package_quantity: Optional[int] = None
    master_carton_quantity: Optional[int] = None
    package_type: Optional[str] = None
    
    # Provenance
    source_file: Optional[str] = None
    source_location: Optional[str] = None  # e.g., "table:0,row:3" or "line:15"
    extraction_method: str = "unknown"
    extraction_confidence: float = 0.0
    extraction_notes: str = ""
    raw_source_text: str = ""


class ProductExtractor:
    """Extracts product candidates from parsed content."""
    
    # Price patterns - handles both $100 and 100$ formats
    PRICE_PATTERN = re.compile(
        r'(?:[\$\€\£]\s*)?(\d+(?:[.,]\d{2})?)\s*(?:[\$\€\£])?'
        r'(?:\s*/\s*(?:ea|each|pc|pcs|piece|pieces|unit|units|kg|lb|lbs|ft|m|box|case|pack|packs|set|sets))?',
        re.IGNORECASE
    )
    
    # Also catch trailing currency like 200$
    TRAILING_PRICE_PATTERN = re.compile(
        r'(\d+(?:[.,]\d{2})?)\s*[\$\€\£]',
        re.IGNORECASE
    )
    
    # SKU patterns
    SKU_PATTERN = re.compile(r'\b(?:SKU|sku|Item|item|Product|product|ID|id)[:\s#]*([A-Z0-9\-]{3,20})\b')
    
    # Unit patterns
    UNIT_PATTERN = re.compile(
        r'\b(?:per|/)\s*(?:ea|each|pc|pcs|piece|pieces|unit|units|kg|lb|lbs|ft|m|box|case|pack|packs|set|sets)\b',
        re.IGNORECASE
    )
    
    # Category keywords
    CATEGORY_KEYWORDS = {
        'mattress': ['mattress', 'foam', 'spring', 'pillowtop', 'orthopedic'],
        'furniture': ['bed', 'frame', 'headboard', 'box spring', 'boxspring', 'nightstand', 'dresser', 'sofa', 'sectional', 'loveseat', 'recliner', 'chair', 'table', 'desk'],
        'bedding': ['pillow', 'sheet', 'comforter', 'duvet', 'blanket', 'protector'],
        'electronics': ['laptop', 'monitor', 'keyboard', 'mouse', 'smartphone', 'tablet', 'router', 'headphone'],
    }
    
    # Currency symbols
    CURRENCY_MAP = {
        '$': 'USD', '€': 'EUR', '£': 'GBP', '¥': 'JPY', '₹': 'INR', '₩': 'KRW',
        'cad': 'CAD', 'usd': 'USD', 'eur': 'EUR', 'gbp': 'GBP',
    }

    def extract_from_markdown(self, parsed: ParsedContent, source_file: str) -> list[ProductCandidate]:
        """Extract products from markdown parsed content."""
        candidates = []
        metadata = parsed.metadata or {}
        
        # From raw product lines
        for i, line in enumerate(metadata.get('products_raw', [])):
            candidate = self._extract_from_line(line, f"{source_file}:line:{i}", 'markdown')
            if candidate:
                candidates.append(candidate)
        
        # From tables
        for table_idx, table in enumerate(metadata.get('product_tables', [])):
            candidates = self._extract_from_table(table, f"{source_file}:table:{table_idx}", 'markdown_table')
            candidates.extend(candidates)
        
        # Supplier info from metadata
        supplier_name = metadata.get('supplier_name')
        delivery_areas = metadata.get('delivery_areas', [])
        
        for c in candidates:
            c.extraction_notes = f"Supplier: {supplier_name}; Delivery: {', '.join(delivery_areas)}"
        
        return candidates

    def extract_from_pdf(self, parsed: ParsedContent, source_file: str) -> list[ProductCandidate]:
        """Extract products from PDF tables."""
        candidates = []
        for table_idx, table in enumerate(parsed.tables or []):
            table_candidates = self._extract_from_table(table, f"{source_file}:table:{table_idx}", 'pdf_table')
            candidates.extend(table_candidates)
        return candidates

    def extract_from_spreadsheet(self, parsed: ParsedContent, source_file: str) -> list[ProductCandidate]:
        """Extract products from spreadsheet rows."""
        candidates = []
        for table_idx, table in enumerate(parsed.tables or []):
            table_candidates = self._extract_from_table(table, f"{source_file}:sheet:{table_idx}", 'spreadsheet')
            candidates.extend(table_candidates)
        return candidates

    def extract_from_ocr(self, parsed: ParsedContent, source_file: str) -> list[ProductCandidate]:
        """Extract products from OCR text (images)."""
        candidates = []
        text = parsed.text or ""
        
        # OCR text is often fragmented - look for price patterns
        lines = text.split('\n')
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            candidate = self._extract_from_line(line, f"{source_file}:ocr_line:{i}", 'ocr')
            if candidate:
                candidate.raw_source_text = line
                candidates.append(candidate)
        
        return candidates

    def _extract_from_line(self, line: str, location: str, method: str) -> Optional[ProductCandidate]:
        """Extract product info from a single line of text."""
        line = line.strip()
        if not line or len(line) < 5:
            return None
        
        candidate = ProductCandidate(
            source_file=location.split(':')[0] if ':' in location else None,
            source_location=location,
            extraction_method=method,
            raw_source_text=line,
        )
        
        # Extract price - try both patterns
        price_match = self.PRICE_PATTERN.search(line)
        if not price_match:
            price_match = self.TRAILING_PRICE_PATTERN.search(line)
        
        if price_match:
            price_str = price_match.group(1).replace(',', '')
            try:
                candidate.price_amount = float(price_str)
            except ValueError:
                pass
            
            # Extract currency
            for symbol, code in self.CURRENCY_MAP.items():
                if symbol in line:
                    candidate.price_currency = code
                    break
            
            # Extract unit
            unit_match = self.UNIT_PATTERN.search(line)
            if unit_match:
                candidate.price_unit = unit_match.group(0).replace('per ', '').replace('/', '').strip()
        
        # Extract SKU
        sku_match = self.SKU_PATTERN.search(line)
        if sku_match:
            candidate.supplier_sku = sku_match.group(1)
        
        # Extract product name (text before/after price)
        candidate.supplier_product_name = self._extract_product_name(line, price_match)
        if candidate.supplier_product_name:
            candidate.canonical_name = self._normalize_name(candidate.supplier_product_name)
        
        # Infer category
        candidate.category = self._infer_category(line)
        
        # Calculate confidence
        confidence = 0.0
        if candidate.price_amount is not None:
            confidence += 0.4
        if candidate.supplier_sku:
            confidence += 0.2
        if candidate.supplier_product_name:
            confidence += 0.2
        if candidate.category:
            confidence += 0.1
        if candidate.price_currency:
            confidence += 0.1
        candidate.extraction_confidence = min(confidence, 1.0)
        
        return candidate if confidence > 0.1 else None

    def _extract_from_table(self, table: list[list[str]], location: str, method: str) -> list[ProductCandidate]:
        """Extract products from a table (list of rows)."""
        if not table or len(table) < 2:
            return []
        
        candidates = []
        
        # Try to detect header row
        header_row = table[0]
        header_lower = [h.lower().strip() for h in header_row]
        
        # Map column indices
        col_map = self._map_columns(header_lower)
        
        # If no clear headers, try to infer from first data row
        if not any(col_map.values()) and len(table) > 1:
            col_map = self._infer_columns_from_data(table[1])
        
        # Extract from data rows
        for row_idx, row in enumerate(table[1:], 1):
            candidate = self._extract_from_table_row(row, col_map, f"{location}:row:{row_idx}", method)
            if candidate:
                candidates.append(candidate)
        
        return candidates

    def _map_columns(self, headers: list[str]) -> dict[str, int]:
        """Map header names to column indices."""
        col_map = {}
        for idx, header in enumerate(headers):
            h = header.lower().strip()
            if any(kw in h for kw in ['sku', 'item', 'product', 'id', 'code']):
                col_map['sku'] = idx
            elif any(kw in h for kw in ['name', 'product', 'description', 'item']):
                col_map['name'] = idx
            elif any(kw in h for kw in ['price', 'cost', 'amount', 'rate']):
                col_map['price'] = idx
            elif any(kw in h for kw in ['qty', 'quantity', 'stock', 'avail']):
                col_map['quantity'] = idx
            elif any(kw in h for kw in ['unit', 'uom', 'measure']):
                col_map['unit'] = idx
            elif any(kw in h for kw in ['cat', 'category', 'type']):
                col_map['category'] = idx
            elif any(kw in h for kw in ['brand', 'manufacturer', 'mfr']):
                col_map['brand'] = idx
            elif any(kw in h for kw in ['model', 'series', 'style']):
                col_map['model'] = idx
            elif any(kw in h for kw in ['desc', 'detail', 'spec']):
                col_map['description'] = idx
        return col_map

    def _infer_columns_from_data(self, row: list[str]) -> dict[str, int]:
        """Infer column roles from data patterns."""
        col_map = {}
        for idx, cell in enumerate(row):
            cell = str(cell).strip()
            if not cell:
                continue
            # Price-like
            if re.match(r'^[\$\€\£]?\d+(?:[.,]\d{2})?$', cell):
                col_map['price'] = idx
            # SKU-like (alphanumeric with dashes)
            elif re.match(r'^[A-Z0-9\-]{3,20}$', cell):
                col_map['sku'] = idx
            # Quantity-like (integer)
            elif cell.isdigit() and 0 < int(cell) < 10000:
                col_map['quantity'] = idx
        return col_map

    def _extract_from_table_row(
        self, row: list[str], col_map: dict[str, int], 
        location: str, method: str
    ) -> Optional[ProductCandidate]:
        """Extract product from a table row using column mapping."""
        candidate = ProductCandidate(source_location=location, extraction_method=method)
        
        def get_cell(key: str) -> Optional[str]:
            idx = col_map.get(key)
            if idx is not None and idx < len(row):
                val = str(row[idx]).strip()
                return val if val else None
            return None
        
        candidate.supplier_sku = get_cell('sku')
        candidate.supplier_product_name = get_cell('name')
        candidate.description = get_cell('description')
        candidate.category = get_cell('category')
        candidate.brand = get_cell('brand')
        candidate.model = get_cell('model')
        
        # Price
        price_str = get_cell('price')
        if price_str:
            price_match = re.search(r'[\$\€\£]?\s*(\d+(?:[.,]\d{2})?)', price_str)
            if price_match:
                try:
                    candidate.price_amount = float(price_match.group(1).replace(',', ''))
                except ValueError:
                    pass
                # Currency
                for sym, code in self.CURRENCY_MAP.items():
                    if sym in price_str:
                        candidate.price_currency = code
                        break
        
        # Unit
        unit_str = get_cell('unit')
        if unit_str:
            candidate.price_unit = unit_str
        
        # Quantity
        qty_str = get_cell('quantity')
        if qty_str and qty_str.isdigit():
            candidate.availability_quantity = int(qty_str)
        
        # Fallback name
        if not candidate.supplier_product_name and candidate.supplier_sku:
            candidate.supplier_product_name = candidate.supplier_sku
        
        if candidate.supplier_product_name:
            candidate.canonical_name = self._normalize_name(candidate.supplier_product_name)
        
        # Infer category from any text
        all_text = ' '.join(str(c) for c in row)
        candidate.category = self._infer_category(all_text)
        
        # Confidence
        confidence = 0.0
        if candidate.price_amount is not None: confidence += 0.3
        if candidate.supplier_sku: confidence += 0.2
        if candidate.supplier_product_name: confidence += 0.2
        if candidate.category: confidence += 0.1
        if get_cell('quantity'): confidence += 0.1
        candidate.extraction_confidence = min(confidence, 1.0)
        
        return candidate if confidence > 0.15 else None

    def _extract_product_name(self, line: str, price_match: Optional[re.Match]) -> Optional[str]:
        """Extract product name from line by removing price and noise."""
        if not price_match:
            return line[:200]
        
        # Remove price and surrounding noise
        name = line[:price_match.start()].strip()
        # Remove emojis and markdown symbols
        name = re.sub(r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF]', '', name)  # emojis
        name = re.sub(r'^[\*\-\•\🔅\✨\s]+', '', name)
        name = re.sub(r'[\$\€\£]\s*\d+(?:[.,]\d{2})?\s*', '', name)
        name = re.sub(r'\d+(?:[.,]\d{2})?\s*[\$\€\£]\s*', '', name)  # trailing price
        name = name.strip(' \t\r\n*-\•')
        
        return name[:200] if name else None

    def _normalize_name(self, name: str) -> str:
        """Normalize product name for canonical matching."""
        # Remove emojis
        name = re.sub(r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF]', '', name)
        # Remove extra whitespace
        name = re.sub(r'\s+', ' ', name)
        # Remove special chars but keep alphanumeric, spaces, hyphens, slashes
        name = re.sub(r'[^\w\s\-\/]', '', name)
        # Title case but preserve acronyms
        words = name.split()
        result = []
        for word in words:
            if word.isupper() and len(word) <= 4:
                result.append(word)  # Keep acronyms
            else:
                result.append(word.capitalize())
        return ' '.join(result)[:200]

    def _infer_category(self, text: str) -> Optional[str]:
        """Infer product category from text."""
        text_lower = text.lower()
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                return category
        return None


# Global instance
product_extractor = ProductExtractor()