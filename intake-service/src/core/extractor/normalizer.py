"""Normalization and validation of extracted product data."""
import re
from typing import Any, Optional
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass
class NormalizedProduct:
    """Normalized and validated product data ready for database."""
    # Core identification
    supplier_sku: Optional[str] = None
    supplier_product_name: Optional[str] = None
    canonical_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    attributes: dict[str, Any] = None
    
    # Pricing
    price_amount: Optional[Decimal] = None
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
    shipping_weight_kg: Optional[Decimal] = None
    shipping_dimensions_cm: Optional[dict[str, float]] = None
    
    # Packaging
    package_quantity: Optional[int] = None
    master_carton_quantity: Optional[int] = None
    package_type: Optional[str] = None

    # Extraction confidence (carried from the candidate for price-entry scoring)
    extraction_confidence: float = 0.0
    
    # Validation
    validation_errors: list[str] = None
    validation_warnings: list[str] = None
    
    def __post_init__(self):
        if self.attributes is None:
            self.attributes = {}
        if self.validation_errors is None:
            self.validation_errors = []
        if self.validation_warnings is None:
            self.validation_warnings = []


class ProductNormalizer:
    """Normalizes and validates product candidates."""
    
    # Valid currencies (ISO 4217 subset)
    VALID_CURRENCIES = {
        'USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CNY', 'INR', 'MXN', 'BRL',
        'CHF', 'NOK', 'SEK', 'DKK', 'PLN', 'CZK', 'HUF', 'RON', 'BGN', 'HRK'
    }
    
    # Valid availability statuses
    VALID_AVAILABILITY = {
        'in_stock', 'out_of_stock', 'made_to_order', 'preorder', 'discontinued', 'unknown'
    }
    
    # Valid delivery terms (Incoterms)
    VALID_DELIVERY_TERMS = {
        'EXW', 'FCA', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP',
        'FAS', 'FOB', 'CFR', 'CIF',
        'EX Works', 'Free Carrier', 'Carriage Paid To', 'Carriage and Insurance Paid To',
        'Delivered at Place', 'Delivered at Place Unloaded', 'Delivered Duty Paid',
        'Free Alongside Ship', 'Free on Board', 'Cost and Freight', 'Cost Insurance Freight'
    }
    
    # Unit standardization
    UNIT_MAP = {
        'ea': 'each', 'each': 'each', 'pc': 'each', 'pcs': 'each', 
        'piece': 'each', 'pieces': 'each', 'unit': 'each', 'units': 'each',
        'kg': 'kg', 'kilogram': 'kg', 'kilograms': 'kg',
        'lb': 'lb', 'lbs': 'lb', 'pound': 'lb', 'pounds': 'lb',
        'ft': 'ft', 'foot': 'ft', 'feet': 'ft',
        'm': 'm', 'meter': 'm', 'meters': 'm',
        'box': 'box', 'boxes': 'box',
        'case': 'case', 'cases': 'case',
        'pack': 'pack', 'packs': 'pack',
        'set': 'set', 'sets': 'set',
    }
    
    # Category normalization
    CATEGORY_MAP = {
        'mattress': 'Mattress',
        'furniture': 'Furniture',
        'bedding': 'Bedding',
        'electronics': 'Electronics',
    }

    def normalize(self, candidate: Any) -> NormalizedProduct:
        """Normalize a product candidate."""
        normalized = NormalizedProduct()
        warnings = []
        errors = []
        
        # Supplier SKU
        normalized.supplier_sku = self._normalize_sku(candidate.supplier_sku)
        
        # Names
        normalized.supplier_product_name = self._clean_text(candidate.supplier_product_name)
        normalized.canonical_name = self._normalize_name(candidate.canonical_name or candidate.supplier_product_name)
        normalized.description = self._clean_text(candidate.description)
        
        # Category & Brand
        normalized.category = self._normalize_category(candidate.category)
        normalized.brand = self._clean_text(candidate.brand)
        normalized.model = self._clean_text(candidate.model)
        
        # Attributes
        normalized.attributes = self._normalize_attributes(candidate.attributes)
        normalized.extraction_confidence = float(candidate.extraction_confidence or 0.0)
        
        # Price
        normalized.price_amount = self._normalize_price(candidate.price_amount, errors)
        normalized.price_currency = self._normalize_currency(candidate.price_currency, errors)
        normalized.price_unit = self._normalize_unit(candidate.price_unit)
        normalized.price_min_order_qty = self._normalize_int(candidate.price_min_order_qty, min_val=1)
        
        # Availability
        normalized.availability_status = self._normalize_availability(candidate.availability_status)
        normalized.availability_quantity = self._normalize_int(candidate.availability_quantity, min_val=0)
        normalized.availability_lead_time_days = self._normalize_int(candidate.availability_lead_time_days, min_val=0)
        
        # Shipping
        normalized.delivery_terms = self._normalize_delivery_terms(candidate.delivery_terms, errors)
        normalized.delivery_origin = self._clean_text(candidate.delivery_origin)
        normalized.delivery_destination = self._clean_text(candidate.delivery_destination)
        normalized.shipping_weight_kg = self._normalize_decimal(candidate.shipping_weight_kg)
        normalized.shipping_dimensions_cm = self._normalize_dimensions(candidate.shipping_dimensions_cm)
        
        # Packaging
        normalized.package_quantity = self._normalize_int(candidate.package_quantity, min_val=1)
        normalized.master_carton_quantity = self._normalize_int(candidate.master_carton_quantity, min_val=1)
        normalized.package_type = self._clean_text(candidate.package_type)
        
        # Validation
        normalized.validation_errors = errors
        normalized.validation_warnings = warnings
        
        return normalized

    def _normalize_sku(self, sku: Optional[str]) -> Optional[str]:
        if not sku:
            return None
        # Remove spaces, keep alphanumeric and hyphens
        sku = re.sub(r'[^\w\-]', '', str(sku).strip().upper())
        return sku[:50] if sku else None

    def _clean_text(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        # Remove extra whitespace, keep reasonable chars
        text = re.sub(r'\s+', ' ', str(text).strip())
        # Remove control chars
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
        return text[:500] if text else None

    def _normalize_name(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        name = self._clean_text(name)
        if not name:
            return None
        # Title case but preserve acronyms
        words = name.split()
        result = []
        for word in words:
            if word.isupper() and len(word) <= 4:
                result.append(word)  # Keep acronyms
            else:
                result.append(word.capitalize())
        return ' '.join(result)[:200]

    def _normalize_category(self, category: Optional[str]) -> Optional[str]:
        if not category:
            return None
        cat_lower = category.lower().strip()
        return self.CATEGORY_MAP.get(cat_lower, category.title())

    def _normalize_attributes(self, attrs: Any) -> dict:
        if not attrs:
            return {}
        if isinstance(attrs, dict):
            return {str(k): str(v) for k, v in attrs.items() if v is not None}
        return {}

    def _normalize_price(self, amount: Any, errors: list) -> Optional[Decimal]:
        if amount is None:
            return None
        try:
            if isinstance(amount, str):
                # Remove currency symbols and commas
                amount = re.sub(r'[^\d\.]', '', amount)
            return Decimal(str(amount)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
        except Exception:
            errors.append(f"Invalid price amount: {amount}")
            return None

    def _normalize_currency(self, currency: Optional[str], errors: list) -> Optional[str]:
        if not currency:
            return None
        currency = currency.strip().upper()
        if currency in self.VALID_CURRENCIES:
            return currency
        # Try to map common variants
        currency_map = {
            'US$': 'USD', 'EURO': 'EUR', 'POUND': 'GBP', 'CAD$': 'CAD',
        }
        if currency in currency_map:
            return currency_map[currency]
        errors.append(f"Invalid currency: {currency}")
        return None

    def _normalize_unit(self, unit: Optional[str]) -> Optional[str]:
        if not unit:
            return None
        unit = unit.strip().lower()
        return self.UNIT_MAP.get(unit, unit)

    def _normalize_int(self, value: Any, min_val: int = None, max_val: int = None) -> Optional[int]:
        if value is None:
            return None
        try:
            if isinstance(value, str):
                value = value.strip()
                # Extract first number
                match = re.search(r'\d+', value)
                if match:
                    value = int(match.group())
                else:
                    return None
            elif isinstance(value, float):
                value = int(value)
            elif not isinstance(value, int):
                return None
            
            if min_val is not None and value < min_val:
                return None
            if max_val is not None and value > max_val:
                return None
            return value
        except Exception:
            return None

    def _normalize_decimal(self, value: Any) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            if isinstance(value, str):
                value = re.sub(r'[^\d\.]', '', value)
            return Decimal(str(value)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
        except Exception:
            return None

    def _normalize_availability(self, status: Optional[str]) -> Optional[str]:
        if not status:
            return 'unknown'
        status = status.lower().strip().replace(' ', '_').replace('-', '_')
        if status in self.VALID_AVAILABILITY:
            return status
        # Map common variants
        variant_map = {
            'instock': 'in_stock', 'outofstock': 'out_of_stock',
            'made_to_order': 'made_to_order', 'pre-order': 'preorder',
            'available': 'in_stock', 'unavailable': 'out_of_stock',
            'backorder': 'made_to_order', 'coming_soon': 'preorder',
        }
        return variant_map.get(status, 'unknown')

    def _normalize_delivery_terms(self, terms: Optional[str], errors: list) -> Optional[str]:
        if not terms:
            return None
        terms = terms.strip().upper()
        # Check exact match
        if terms in self.VALID_DELIVERY_TERMS:
            return terms
        # Try to match partial
        for valid in self.VALID_DELIVERY_TERMS:
            if valid in terms or terms in valid:
                return valid
        errors.append(f"Invalid delivery terms: {terms}")
        return terms  # Keep original for review

    def _normalize_dimensions(self, dims: Any) -> Optional[dict[str, float]]:
        if not dims:
            return None
        if isinstance(dims, dict):
            result = {}
            for k, v in dims.items():
                try:
                    result[str(k).lower()] = float(v)
                except Exception:
                    pass
            return result if result else None
        return None


# Global instance
product_normalizer = ProductNormalizer()