"""Company query endpoints."""
from __future__ import annotations
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from shared.logger import get_logger

logger = get_logger("api.companies")
router = APIRouter(prefix="/company", tags=["companies"])


class CompanyResponse(BaseModel):
    cik: str
    name: str
    tickers: list
    exchanges: list
    sic: Optional[str]
    sic_description: Optional[str]
    state_of_incorporation: Optional[str]


@router.get("/{cik}", response_model=CompanyResponse)
async def get_company(cik: str, db: AsyncSession = Depends(get_db)):
    """Get company metadata by CIK."""
    result = await db.execute(
        text(
            "SELECT cik, name, tickers, exchanges, sic, sic_description, state_of_incorporation "
            "FROM companies WHERE cik = :cik"
        ),
        {"cik": cik},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Company with CIK {cik!r} not found")
    return CompanyResponse(**dict(row))


@router.get("/{cik}/filings")
async def get_company_filings(
    cik: str,
    form_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Get all filings for a company."""
    filters = ["cik = :cik"]
    params: dict = {"cik": cik}

    if form_type:
        filters.append("UPPER(form_type) = UPPER(:form_type)")
        params["form_type"] = form_type

    where = "WHERE " + " AND ".join(filters)
    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM filings {where}"), params
    )
    total = count_result.scalar_one()

    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size

    result = await db.execute(
        text(
            f"SELECT accession_number, form_type, filing_date, filing_url, status "
            f"FROM filings {where} ORDER BY filing_date DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    filings = [dict(r) for r in result.mappings().all()]

    return {
        "cik": cik,
        "total": total,
        "page": page,
        "page_size": page_size,
        "filings": filings,
    }
