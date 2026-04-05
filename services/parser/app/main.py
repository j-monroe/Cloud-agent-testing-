"""
Parser Service
Consumes raw filing events, normalizes data, persists to storage,
and publishes for enrichment.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

sys.path.insert(0, "/app")

from shared.logger import get_logger
from shared.models import QueueMessage, RawFiling, FilingStatus
from shared.queue import StreamQueueClient

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import Column, String, Date, DateTime, Text, Enum as SAEnum, UniqueConstraint, select
from sqlalchemy.orm import DeclarativeBase

logger = get_logger("parser")

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


class FilingRecord(Base):
    __tablename__ = "filings"

    accession_number = Column(String(25), primary_key=True)
    cik = Column(String(20), nullable=False, index=True)
    company_name = Column(String(500), nullable=False)
    form_type = Column(String(50), nullable=False, index=True)
    filing_date = Column(Date, nullable=False, index=True)
    filing_url = Column(Text, nullable=False)
    source = Column(String(20), nullable=False)
    status = Column(SAEnum(FilingStatus), default=FilingStatus.PARSED, nullable=False)
    raw_storage_path = Column(Text)
    ingested_at = Column(DateTime, nullable=False)
    parsed_at = Column(DateTime, default=datetime.utcnow)
    enriched_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("accession_number", name="uq_accession"),
    )


class ParserService:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.cfg = config
        self.redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        self.db_url = os.getenv(
            "DATABASE_URL", "postgresql+asyncpg://edgar:edgar@postgres:5432/edgar"
        )
        self._running = True
        self._queue: Optional[StreamQueueClient] = None
        self._enrich_queue: Optional[StreamQueueClient] = None
        self._engine = None
        self._sessionmaker = None
        self._storage_path = Path(config["storage"].get("local_path", "/data/raw"))

    async def setup(self) -> None:
        self._storage_path.mkdir(parents=True, exist_ok=True)

        self._engine = create_async_engine(self.db_url, echo=False)
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

        q_cfg = self.cfg["queue"]
        self._queue = StreamQueueClient(
            redis_url=self.redis_url,
            stream=q_cfg["stream_name"],
            consumer_group=q_cfg["consumer_group"],
            consumer_name="parser_worker",
            dlq_stream=q_cfg["dead_letter_stream"],
        )
        await self._queue.connect()

        self._enrich_queue = StreamQueueClient(
            redis_url=self.redis_url,
            stream=q_cfg["enrichment_stream"],
            consumer_group=q_cfg["consumer_group"],
            consumer_name="parser_worker",
            dlq_stream=q_cfg["dead_letter_stream"],
        )
        await self._enrich_queue.connect()

        logger.info("parser_started")

    async def teardown(self) -> None:
        if self._queue:
            await self._queue.disconnect()
        if self._enrich_queue:
            await self._enrich_queue.disconnect()
        if self._engine:
            await self._engine.dispose()
        logger.info("parser_stopped")

    def _stop(self) -> None:
        self._running = False

    def _raw_storage_path(self, filing: RawFiling) -> Path:
        dt = filing.filing_date
        return (
            self._storage_path
            / str(dt.year)
            / f"{dt.month:02d}"
            / f"{dt.day:02d}"
            / f"{filing.accession_key}.json"
        )

    async def _store_raw(self, filing: RawFiling) -> str:
        path = self._raw_storage_path(filing)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(filing.model_dump(mode="json"), f, indent=2)
        return str(path)

    async def _upsert_filing(self, filing: RawFiling, storage_path: str) -> None:
        assert self._sessionmaker
        async with self._sessionmaker() as session:
            async with session.begin():
                stmt = select(FilingRecord).where(
                    FilingRecord.accession_number == filing.accession_number
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing is None:
                    record = FilingRecord(
                        accession_number=filing.accession_number,
                        cik=filing.cik,
                        company_name=filing.company_name,
                        form_type=filing.form_type,
                        filing_date=filing.filing_date,
                        filing_url=filing.filing_url,
                        source=filing.source,
                        status=FilingStatus.PARSED,
                        raw_storage_path=storage_path,
                        ingested_at=filing.ingested_at,
                        parsed_at=datetime.utcnow(),
                    )
                    session.add(record)
                    logger.info(
                        "filing_inserted",
                        accession=filing.accession_number,
                        form_type=filing.form_type,
                    )
                else:
                    logger.debug("filing_already_exists", accession=filing.accession_number)

    async def _process_message(self, msg_id: str, message: QueueMessage) -> None:
        try:
            filing = RawFiling(**message.payload)
            storage_path = await self._store_raw(filing)
            await self._upsert_filing(filing, storage_path)

            assert self._enrich_queue
            enrich_msg = QueueMessage(
                event_type="parsed_filing",
                payload={
                    "accession_number": filing.accession_number,
                    "cik": filing.cik,
                    "company_name": filing.company_name,
                    "form_type": filing.form_type,
                },
            )
            await self._enrich_queue.publish(enrich_msg)
            assert self._queue
            await self._queue.ack(msg_id)

        except Exception as exc:
            logger.error("message_processing_failed", id=msg_id, error=str(exc), exc_info=True)
            assert self._queue
            await self._queue.nack(msg_id, message)

    async def run(self) -> None:
        await self.setup()
        try:
            assert self._queue
            async for msg_id, message in self._queue.consume():
                if not self._running:
                    break
                if message.event_type != "raw_filing":
                    await self._queue.ack(msg_id)
                    continue
                await self._process_message(msg_id, message)
        finally:
            await self.teardown()


async def main() -> None:
    config = load_config()
    svc = ParserService(config)
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, svc._stop)
    await svc.run()


if __name__ == "__main__":
    asyncio.run(main())
