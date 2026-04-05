"""
Index Reconciliation Job
Downloads EDGAR daily/full index files, compares against database,
and backfills missing filings.
"""
from __future__ import annotations
import asyncio
import gzip
import os
import re
import signal
import sys
from datetime import date, timedelta, datetime
from typing import Any, Dict, List, Optional, Set

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
from shared.models import QueueMessage, RawFiling
from shared.queue import StreamQueueClient
from shared.rate_limiter import TokenBucketRateLimiter
import redis.asyncio as aioredis

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

logger = get_logger("reconciliation")

CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config/config.yaml")


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH) as f:
        raw = f.read()

    def replacer(m: re.Match) -> str:
        key, default = m.group(1), m.group(2)
        return os.getenv(key, default or "")

    raw = re.sub(r"\$\{([A-Z_]+)(?::-(.*?))?\}", replacer, raw)
    return yaml.safe_load(raw)


class ReconciliationJob:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.cfg = config
        edgar_cfg = config["edgar"]
        self.index_base = edgar_cfg["index_base_url"]
        self.user_agent = edgar_cfg["user_agent"]
        self.rate_cfg = edgar_cfg["rate_limit"]
        self.recon_cfg = config["reconciliation"]
        self.redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        self.db_url = os.getenv(
            "DATABASE_URL", "postgresql+asyncpg://edgar:edgar@postgres:5432/edgar"
        )
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
            rate=self.rate_cfg["requests_per_second"],
            capacity=self.rate_cfg["burst"],
        )
        self._engine = create_async_engine(self.db_url, echo=False)
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

        q_cfg = self.cfg["queue"]
        self._queue = StreamQueueClient(
            redis_url=self.redis_url,
            stream=q_cfg["stream_name"],
            consumer_group=q_cfg["consumer_group"],
            consumer_name="reconciliation_worker",
            dlq_stream=q_cfg["dead_letter_stream"],
        )
        await self._queue.connect()

        self._http = httpx.AsyncClient(
            headers={"User-Agent": self.user_agent},
            timeout=60.0,
            follow_redirects=True,
        )
        logger.info("reconciliation_job_started")

    async def teardown(self) -> None:
        if self._http:
            await self._http.aclose()
        if self._queue:
            await self._queue.disconnect()
        if self._redis:
            await self._redis.aclose()
        if self._engine:
            await self._engine.dispose()
        logger.info("reconciliation_job_stopped")

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=5, max=60),
        reraise=True,
    )
    async def _fetch_index(self, url: str) -> bytes:
        assert self._rate_limiter and self._http
        await self._rate_limiter.acquire()
        resp = await self._http.get(url)
        resp.raise_for_status()
        return resp.content

    async def _get_index_entries(self, target_date: date) -> List[Dict]:
        """Download and parse EDGAR full-index for a given date."""
        year = target_date.year
        quarter = (target_date.month - 1) // 3 + 1
        url = f"{self.index_base}/{year}/QTR{quarter}/company.gz"

        try:
            content = await self._fetch_index(url)
            data = gzip.decompress(content).decode("latin-1")
        except Exception as exc:
            logger.warning("index_fetch_failed", url=url, error=str(exc))
            return []

        entries = []
        lines = data.splitlines()
        in_data = False
        for line in lines:
            if line.startswith("---"):
                in_data = True
                continue
            if not in_data:
                continue
            if len(line) < 74:
                continue
            company_name = line[:62].strip()
            form_type = line[62:74].strip()
            cik = line[74:86].strip()
            date_filed_str = line[86:98].strip()
            filename = line[98:].strip()

            try:
                filed_date = date.fromisoformat(date_filed_str)
            except ValueError:
                continue

            if filed_date != target_date:
                continue

            acc_match = re.search(r"(\d{10}-\d{2}-\d{6})", filename)
            if not acc_match:
                continue

            entries.append(
                {
                    "accession_number": acc_match.group(1),
                    "cik": cik.lstrip("0") or "0",
                    "company_name": company_name,
                    "form_type": form_type,
                    "filing_date": filed_date,
                    "filing_url": f"https://www.sec.gov/Archives/{filename}",
                    "source": "index",
                }
            )

        logger.info("index_parsed", date=str(target_date), entries=len(entries))
        return entries

    async def _get_db_accessions(self, target_date: date) -> Set[str]:
        """Get all accession numbers in DB for a given date."""
        assert self._sessionmaker
        async with self._sessionmaker() as session:
            result = await session.execute(
                text("SELECT accession_number FROM filings WHERE filing_date = :d"),
                {"d": target_date},
            )
            return {row[0] for row in result.fetchall()}

    async def _backfill_missing(self, missing: List[Dict]) -> int:
        """Publish missing filings to the ingestion queue."""
        assert self._queue
        count = 0
        batch_size = self.recon_cfg.get("batch_size", 100)
        for i in range(0, len(missing), batch_size):
            batch = missing[i : i + batch_size]
            for entry in batch:
                filing = RawFiling(**entry)
                msg = QueueMessage(
                    event_type="raw_filing",
                    payload=filing.model_dump(mode="json"),
                )
                await self._queue.publish(msg)
                count += 1
        return count

    async def run_once(self) -> None:
        """Run one reconciliation pass."""
        lookback = self.recon_cfg.get("lookback_days", 7)
        today = date.today()

        for delta in range(lookback):
            target_date = today - timedelta(days=delta)
            logger.info("reconciling_date", date=str(target_date))

            index_entries = await self._get_index_entries(target_date)
            if not index_entries:
                continue

            db_accessions = await self._get_db_accessions(target_date)
            index_accessions = {e["accession_number"] for e in index_entries}

            missing_accessions = index_accessions - db_accessions
            missing_entries = [
                e for e in index_entries if e["accession_number"] in missing_accessions
            ]

            logger.info(
                "reconciliation_result",
                date=str(target_date),
                index_count=len(index_accessions),
                db_count=len(db_accessions),
                missing_count=len(missing_accessions),
            )

            if missing_entries:
                backfilled = await self._backfill_missing(missing_entries)
                logger.info("backfilled", date=str(target_date), count=backfilled)

    async def run(self) -> None:
        await self.setup()
        try:
            await self.run_once()
        finally:
            await self.teardown()


async def main() -> None:
    config = load_config()
    job = ReconciliationJob(config)
    await job.run()


if __name__ == "__main__":
    asyncio.run(main())
