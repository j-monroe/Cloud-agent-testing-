"""
Enrichment Service
Fetches company metadata from data.sec.gov and enriches filing records.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import signal
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
import yaml
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

sys.path.insert(0, "/app")

from shared.logger import get_logger
from shared.models import QueueMessage, CompanyMetadata
from shared.queue import StreamQueueClient
from shared.rate_limiter import TokenBucketRateLimiter
import redis.asyncio as aioredis

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import Column, String, DateTime, JSON, select, text
from sqlalchemy.orm import DeclarativeBase

logger = get_logger("enrichment")

CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config/config.yaml")


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH) as f:
        raw = f.read()

    def replacer(m: re.Match) -> str:
        key, default = m.group(1), m.group(2)
        return os.getenv(key, default or "")

    raw = re.sub(r"\$\{([A-Z_]+)(?::-(.*?))?\}", replacer, raw)
    return yaml.safe_load(raw)


class Base(DeclarativeBase):
    pass


class CompanyRecord(Base):
    __tablename__ = "companies"

    cik = Column(String(20), primary_key=True)
    name = Column(String(500), nullable=False)
    tickers = Column(JSON, default=list)
    exchanges = Column(JSON, default=list)
    sic = Column(String(10))
    sic_description = Column(String(200))
    state_of_incorporation = Column(String(10))
    fiscal_year_end = Column(String(10))
    addresses = Column(JSON, default=dict)
    fetched_at = Column(DateTime, server_default=text("NOW()"))
    updated_at = Column(DateTime, server_default=text("NOW()"), onupdate=lambda: datetime.now(timezone.utc))


class EnrichmentService:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.cfg = config
        self.redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        self.db_url = os.getenv(
            "DATABASE_URL", "postgresql+asyncpg://edgar:edgar@postgres:5432/edgar"
        )
        self.user_agent = config["edgar"]["user_agent"]
        self.data_api_base = config["edgar"]["data_api_base"]
        self._running = True
        self._redis: Optional[aioredis.Redis] = None
        self._rate_limiter: Optional[TokenBucketRateLimiter] = None
        self._queue: Optional[StreamQueueClient] = None
        self._engine = None
        self._sessionmaker = None
        self._http: Optional[httpx.AsyncClient] = None

    async def setup(self) -> None:
        self._redis = await aioredis.from_url(
            self.redis_url, encoding="utf-8", decode_responses=True
        )
        self._rate_limiter = TokenBucketRateLimiter(
            self._redis,
            rate=self.cfg["edgar"]["rate_limit"]["requests_per_second"],
            capacity=self.cfg["edgar"]["rate_limit"]["burst"],
        )
        self._engine = create_async_engine(self.db_url, echo=False)
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

        q_cfg = self.cfg["queue"]
        self._queue = StreamQueueClient(
            redis_url=self.redis_url,
            stream=q_cfg["enrichment_stream"],
            consumer_group=q_cfg["consumer_group"],
            consumer_name="enrichment_worker",
            dlq_stream=q_cfg["dead_letter_stream"],
        )
        await self._queue.connect()

        self._http = httpx.AsyncClient(
            headers={"User-Agent": self.user_agent},
            timeout=30.0,
            follow_redirects=True,
        )
        logger.info("enrichment_service_started")

    async def teardown(self) -> None:
        if self._http:
            await self._http.aclose()
        if self._queue:
            await self._queue.disconnect()
        if self._redis:
            await self._redis.aclose()
        if self._engine:
            await self._engine.dispose()
        logger.info("enrichment_service_stopped")

    def _stop(self) -> None:
        self._running = False

    def _cik_padded(self, cik: str) -> str:
        """Pad CIK to 10 digits for data.sec.gov API."""
        return cik.zfill(10)

    async def _get_cached_company(self, cik: str) -> Optional[CompanyMetadata]:
        assert self._redis
        cached = await self._redis.get(f"edgar:company:{cik}")
        if cached:
            return CompanyMetadata(**json.loads(cached))
        return None

    async def _cache_company(self, metadata: CompanyMetadata) -> None:
        assert self._redis
        await self._redis.set(
            f"edgar:company:{metadata.cik}",
            metadata.model_dump_json(),
            ex=86400,
        )

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=False,
    )
    async def _fetch_company_metadata(self, cik: str) -> Optional[CompanyMetadata]:
        assert self._rate_limiter and self._http
        await self._rate_limiter.acquire()
        padded = self._cik_padded(cik)
        url = f"{self.data_api_base}/submissions/CIK{padded}.json"
        try:
            resp = await self._http.get(url)
            if resp.status_code == 404:
                logger.debug("company_not_found", cik=cik)
                return None
            resp.raise_for_status()
            data = resp.json()

            return CompanyMetadata(
                cik=cik,
                name=data.get("name", ""),
                tickers=data.get("tickers", []),
                exchanges=data.get("exchanges", []),
                sic=data.get("sic"),
                sic_description=data.get("sicDescription"),
                state_of_incorporation=data.get("stateOfIncorporation"),
                fiscal_year_end=data.get("fiscalYearEnd"),
                addresses=data.get("addresses", {}),
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("company_fetch_failed", cik=cik, status=exc.response.status_code)
            return None

    async def _upsert_company(self, metadata: CompanyMetadata) -> None:
        assert self._sessionmaker
        async with self._sessionmaker() as session:
            async with session.begin():
                stmt = select(CompanyRecord).where(CompanyRecord.cik == metadata.cik)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing is None:
                    record = CompanyRecord(
                        cik=metadata.cik,
                        name=metadata.name,
                        tickers=metadata.tickers,
                        exchanges=metadata.exchanges,
                        sic=metadata.sic,
                        sic_description=metadata.sic_description,
                        state_of_incorporation=metadata.state_of_incorporation,
                        fiscal_year_end=metadata.fiscal_year_end,
                        addresses=metadata.addresses,
                        fetched_at=metadata.fetched_at,
                    )
                    session.add(record)
                else:
                    existing.name = metadata.name
                    existing.tickers = metadata.tickers
                    existing.exchanges = metadata.exchanges
                    existing.sic = metadata.sic
                    existing.sic_description = metadata.sic_description
                    existing.updated_at = datetime.now(timezone.utc)

    async def _update_filing_status(self, accession_number: str) -> None:
        assert self._sessionmaker
        async with self._sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE filings SET status='enriched', enriched_at=:now "
                        "WHERE accession_number=:acc"
                    ),
                    {"now": datetime.now(timezone.utc), "acc": accession_number},
                )

    async def _process_message(self, msg_id: str, message: QueueMessage) -> None:
        try:
            payload = message.payload
            cik = payload["cik"]
            accession = payload["accession_number"]

            metadata = await self._get_cached_company(cik)
            if metadata is None:
                metadata = await self._fetch_company_metadata(cik)
                if metadata:
                    await self._cache_company(metadata)
                    await self._upsert_company(metadata)
                    logger.info("company_enriched", cik=cik, name=metadata.name)

            await self._update_filing_status(accession)
            assert self._queue
            await self._queue.ack(msg_id)
            logger.info("filing_enriched", accession=accession, cik=cik)

        except Exception as exc:
            logger.error("enrichment_failed", id=msg_id, error=str(exc), exc_info=True)
            assert self._queue
            await self._queue.nack(msg_id, message)

    async def run(self) -> None:
        await self.setup()
        try:
            assert self._queue
            async for msg_id, message in self._queue.consume():
                if not self._running:
                    break
                if message.event_type != "parsed_filing":
                    await self._queue.ack(msg_id)
                    continue
                await self._process_message(msg_id, message)
        finally:
            await self.teardown()


async def main() -> None:
    config = load_config()
    svc = EnrichmentService(config)
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, svc._stop)
    await svc.run()


if __name__ == "__main__":
    asyncio.run(main())
