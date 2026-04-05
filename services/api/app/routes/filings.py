"""Filings query endpoints."""
from __future__ import annotations
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from shared.logger import get_logger

logger = get_logger("api.filings")
router = APIRouter(prefix="/filings", tags=["filings"])


class FilingResponse(BaseModel):
    accession_number: str
    cik: str
    company_name: str
    form_type: str
    filing_date: date
    filing_url: str
    source: str
    status: str

    model_config = {"from_attributes": True}


class FilingsPage(BaseModel):
    items: List[FilingResponse]
    total: int
    page: int
    page_size: int
    pages: int


@router.get("", response_model=FilingsPage)
async def list_filings(
    filing_date: Optional[date] = Query(None, alias="date", description="Filter by filing date"),
    form_type: Optional[str] = Query(None, description="Filter by form type (e.g. D, 8-K)"),
    cik: Optional[str] = Query(None, description="Filter by CIK"),
    company_name: Optional[str] = Query(None, description="Filter by company name (partial match)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Query filings with optional filters."""
    filters = []
    params: dict = {}

    if filing_date:
        filters.append("filing_date = :filing_date")
        params["filing_date"] = filing_date
    if form_type:
        filters.append("UPPER(form_type) = UPPER(:form_type)")
        params["form_type"] = form_type
    if cik:
        filters.append("cik = :cik")
        params["cik"] = cik
    if company_name:
        filters.append("company_name ILIKE :company_name")
        params["company_name"] = f"%{company_name}%"

    where = "WHERE " + " AND ".join(filters) if filters else ""
    count_sql = text(f"SELECT COUNT(*) FROM filings {where}")
    count_result = await db.execute(count_sql, params)
    total = count_result.scalar_one()

    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    data_sql = text(
        f"SELECT accession_number, cik, company_name, form_type, filing_date, "
        f"filing_url, source, status FROM filings {where} "
        f"ORDER BY filing_date DESC, ingested_at DESC "
        f"LIMIT :limit OFFSET :offset"
    )
    result = await db.execute(data_sql, params)
    rows = result.mappings().all()

    logger.info("filings_queried", total=total, page=page)

    return FilingsPage(
        items=[FilingResponse(**dict(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if page_size > 0 else 0,
    )


@router.get("/{accession_number}", response_model=FilingResponse)
async def get_filing(
    accession_number: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific filing by accession number."""
    result = await db.execute(
        text(
            "SELECT accession_number, cik, company_name, form_type, filing_date, "
            "filing_url, source, status FROM filings WHERE accession_number = :acc"
        ),
        {"acc": accession_number},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Filing not found")
    return FilingResponse(**dict(row))
