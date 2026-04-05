"""Tests for shared Pydantic models."""
import json
import pytest
from datetime import date, datetime, timezone
from shared.models import RawFiling, QueueMessage, FilingStatus, CompanyMetadata


def test_raw_filing_valid():
    filing = RawFiling(
        accession_number="0001234567-24-000001",
        cik="1234567",
        company_name="Test Corp",
        form_type="8-K",
        filing_date=date(2024, 1, 15),
        filing_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000001/",
        source="rss",
    )
    assert filing.accession_number == "0001234567-24-000001"
    assert filing.accession_key == "000123456724000001"


def test_raw_filing_invalid_accession():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RawFiling(
            accession_number="INVALID",
            cik="123",
            company_name="Test",
            form_type="8-K",
            filing_date=date(2024, 1, 1),
            filing_url="https://sec.gov/",
            source="rss",
        )


def test_queue_message_serialization():
    msg = QueueMessage(
        event_type="raw_filing",
        payload={"accession_number": "0001234567-24-000001", "cik": "123"},
    )
    assert msg.retry_count == 0
    json_str = msg.model_dump_json()
    data = json.loads(json_str)
    assert data["event_type"] == "raw_filing"


def test_filing_status_enum():
    assert FilingStatus.PENDING == "pending"
    assert FilingStatus.ENRICHED == "enriched"


def test_company_metadata():
    company = CompanyMetadata(
        cik="1234567",
        name="Test Corp Inc.",
        tickers=["TSTC"],
        exchanges=["NASDAQ"],
        sic="7372",
        sic_description="Prepackaged Software",
    )
    assert company.cik == "1234567"
    assert "TSTC" in company.tickers


def test_raw_filing_accession_with_leading_zeros():
    filing = RawFiling(
        accession_number="0000950170-24-001234",
        cik="950170",
        company_name="Another Corp",
        form_type="10-K",
        filing_date=date(2024, 3, 15),
        filing_url="https://www.sec.gov/Archives/edgar/data/950170/000095017024001234/",
        source="index",
    )
    assert filing.accession_key == "000095017024001234"


def test_enriched_filing_defaults():
    from shared.models import EnrichedFiling
    ef = EnrichedFiling(
        accession_number="0001234567-24-000001",
        cik="1234567",
        company_name="Test Corp",
        form_type="8-K",
        filing_date=date(2024, 1, 15),
        filing_url="https://sec.gov/",
        source="rss",
        ingested_at=datetime.now(timezone.utc),
    )
    assert ef.status == FilingStatus.ENRICHED
    assert ef.company_metadata is None
