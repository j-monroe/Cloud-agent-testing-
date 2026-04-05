"""Shared Pydantic and SQLAlchemy models for the EDGAR platform."""
from __future__ import annotations
import enum
import re
from datetime import datetime, date, timezone
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Enums ────────────────────────────────────────────────────────────────────

class FilingStatus(str, enum.Enum):
    PENDING = "pending"
    PARSED = "parsed"
    ENRICHED = "enriched"
    FAILED = "failed"


# ── Pydantic Models ───────────────────────────────────────────────────────────

class RawFiling(BaseModel):
    """Raw filing event from RSS or index."""
    accession_number: str = Field(..., description="SEC accession number, e.g. 0001234567-24-000001")
    cik: str = Field(..., description="Central Index Key")
    company_name: str
    form_type: str
    filing_date: date
    filing_url: str
    source: str = Field(..., description="rss | index | fallback")
    raw_content: Optional[str] = None
    ingested_at: datetime = Field(default_factory=_utcnow)

    @field_validator("accession_number")
    @classmethod
    def validate_accession(cls, v: str) -> str:
        clean = v.replace("-", "")
        if not re.match(r"^\d{18}$", clean):
            raise ValueError(f"Invalid accession number format: {v}")
        return v

    @property
    def accession_key(self) -> str:
        """Return dash-free accession number for use as storage key."""
        return self.accession_number.replace("-", "")


class CompanyMetadata(BaseModel):
    """Company metadata from data.sec.gov."""
    cik: str
    name: str
    tickers: List[str] = []
    exchanges: List[str] = []
    sic: Optional[str] = None
    sic_description: Optional[str] = None
    state_of_incorporation: Optional[str] = None
    fiscal_year_end: Optional[str] = None
    addresses: Dict[str, Any] = {}
    fetched_at: datetime = Field(default_factory=_utcnow)


class EnrichedFiling(BaseModel):
    """Fully enriched filing record."""
    accession_number: str
    cik: str
    company_name: str
    form_type: str
    filing_date: date
    filing_url: str
    source: str
    status: FilingStatus = FilingStatus.ENRICHED
    company_metadata: Optional[CompanyMetadata] = None
    ingested_at: datetime
    enriched_at: Optional[datetime] = None


class FilingQueryParams(BaseModel):
    """Query parameters for the filings API."""
    date: Optional[date] = None
    form_type: Optional[str] = None
    cik: Optional[str] = None
    company_name: Optional[str] = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)


class QueueMessage(BaseModel):
    """Message envelope for queue events."""
    event_type: str
    payload: Dict[str, Any]
    retry_count: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
