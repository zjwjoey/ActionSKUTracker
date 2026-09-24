"""Scrapling Core Parser shadow experiment; never used by the production chain."""

from .parser import ScraplingDetailParser, parse_detail_html
from .validator import ValidationResult, validate_detail

__all__ = ["ScraplingDetailParser", "parse_detail_html", "ValidationResult", "validate_detail"]
