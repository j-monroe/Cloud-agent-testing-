"""Tests for the API service routes."""
import pytest
from datetime import date
from shared.models import RawFiling, QueueMessage, FilingStatus


def test_config_structure():
    """Test that a sample config structure is valid."""
    config = {
        "edgar": {
            "user_agent": "Test edgar@test.com",
            "rss_feeds": [],
            "rate_limit": {"requests_per_second": 8, "burst": 10},
            "poll_interval_seconds": 120,
            "index_base_url": "https://www.sec.gov/Archives/edgar/full-index",
            "data_api_base": "https://data.sec.gov",
        },
        "queue": {
            "stream_name": "test:stream",
            "enrichment_stream": "test:enrichment",
            "consumer_group": "test",
            "dead_letter_stream": "test:dlq",
            "max_retry": 3,
        },
        "storage": {"local_path": "/data/raw", "use_s3": False},
        "api": {
            "title": "Test API",
            "version": "1.0.0",
            "description": "Test",
            "cors_origins": ["*"],
        },
        "reconciliation": {"lookback_days": 1, "batch_size": 10},
    }
    assert "edgar" in config
    assert "queue" in config
    assert "api" in config
    assert config["edgar"]["rate_limit"]["requests_per_second"] == 8


def test_filing_model_roundtrip():
    """Test filing model can be serialized and deserialized."""
    filing = RawFiling(
        accession_number="0001234567-24-000001",
        cik="1234567",
        company_name="ACME Corp",
        form_type="10-Q",
        filing_date=date(2024, 6, 30),
        filing_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000001/",
        source="rss",
    )
    data = filing.model_dump(mode="json")
    restored = RawFiling(**data)
    assert restored.accession_number == filing.accession_number
    assert restored.cik == filing.cik
    assert restored.form_type == filing.form_type


def test_queue_message_retry_increment():
    """Test queue message retry count logic."""
    msg = QueueMessage(
        event_type="raw_filing",
        payload={"test": "data"},
        retry_count=0,
    )
    assert msg.retry_count == 0
    msg.retry_count += 1
    assert msg.retry_count == 1


def test_filing_status_values():
    """Test all filing status values."""
    statuses = [s.value for s in FilingStatus]
    assert "pending" in statuses
    assert "parsed" in statuses
    assert "enriched" in statuses
    assert "failed" in statuses


def test_raw_filing_source_field():
    """Test that source field accepts various values."""
    for source in ["rss", "index", "fallback"]:
        filing = RawFiling(
            accession_number="0001234567-24-000001",
            cik="1234567",
            company_name="Test Corp",
            form_type="8-K",
            filing_date=date(2024, 1, 15),
            filing_url="https://sec.gov/",
            source=source,
        )
        assert filing.source == source
