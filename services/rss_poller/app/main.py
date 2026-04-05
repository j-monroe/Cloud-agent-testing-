"""
RSS Poller Service
Polls EDGAR RSS feeds and publishes new filings to the message queue.
"""
from __future__ import annotations
import asyncio
import os
import re
import signal
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import feedparser
import httpx
import yaml
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import sys
sys.path.insert(0, "/app")

from shared.logger import get_logger
from shared.models import QueueMessage, RawFiling
from shared.queue import StreamQueueClient
from shared.rate_limiter import TokenBucketRateLimiter
import redis.asyncio as aioredis

logger = get_logger("rss_poller")

CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config/config.yaml")


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH) as f:
        raw = f.read()

    def replacer(m: re.Match) -> str:
        key, default = m.group(1), m.group(2)
        return os.getenv(key, default or "")

    raw = re.sub(r"\$\{([A-Z_]+)(?::-(.*?))?\}", replacer, raw)
    return yaml.safe_load(raw)


class RSSPoller:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.cfg = config
        edgar_cfg = config["edgar"]
        queue_cfg = config["queue"]

        self.feeds: List[Dict] = edgar_cfg["rss_feeds"]
        self.user_agent: str = edgar_cfg["user_agent"]
        self.poll_interval: int = edgar_cfg["poll_interval_seconds"]
        self.rate_cfg = edgar_cfg["rate_limit"]
        self.queue_cfg = queue_cfg

        self.redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        self._running = True
        self._redis: Optional[aioredis.Redis] = None
        self._rate_limiter: Optional[TokenBucketRateLimiter] = None
        self._queue: Optional[StreamQueueClient] = None
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
        self._queue = StreamQueueClient(
            redis_url=self.redis_url,
            stream=self.queue_cfg["stream_name"],
            consumer_group=self.queue_cfg["consumer_group"],
            consumer_name="rss_poller",
            dlq_stream=self.queue_cfg["dead_letter_stream"],
        )
        await self._queue.connect()
        self._http = httpx.AsyncClient(
            headers={"User-Agent": self.user_agent},
            timeout=30.0,
            follow_redirects=True,
        )
        logger.info("rss_poller_started", feeds=len(self.feeds), interval=self.poll_interval)

    async def teardown(self) -> None:
        if self._http:
            await self._http.aclose()
        if self._queue:
            await self._queue.disconnect()
        if self._redis:
            await self._redis.aclose()
        logger.info("rss_poller_stopped")

    def _stop(self) -> None:
        self._running = False

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        reraise=True,
    )
    async def _fetch_feed(self, url: str) -> bytes:
        assert self._rate_limiter and self._http
        await self._rate_limiter.acquire()
        resp = await self._http.get(url)
        resp.raise_for_status()
        return resp.content

    async def _poll_feed(self, feed_config: Dict) -> int:
        url = feed_config["url"]
        name = feed_config["name"]
        published = 0
        try:
            content = await self._fetch_feed(url)
            parsed = feedparser.parse(content)
            entries = parsed.get("entries", [])
            logger.info("feed_fetched", feed=name, entries=len(entries))

            for entry in entries:
                filing = self._parse_entry(entry, name)
                if filing is None:
                    continue

                assert self._redis
                key = f"edgar:seen:{filing.accession_key}"
                is_new = await self._redis.set(key, "1", nx=True, ex=86400 * 7)
                if not is_new:
                    logger.debug("duplicate_skipped", accession=filing.accession_number)
                    continue

                assert self._queue
                msg = QueueMessage(
                    event_type="raw_filing",
                    payload=filing.model_dump(mode="json"),
                )
                await self._queue.publish(msg)
                published += 1
                logger.info(
                    "filing_published",
                    accession=filing.accession_number,
                    form_type=filing.form_type,
                    company=filing.company_name,
                )

        except Exception as exc:
            logger.error("feed_poll_failed", feed=name, error=str(exc))

        return published

    def _parse_entry(self, entry: Dict, source: str) -> Optional[RawFiling]:
        try:
            link = entry.get("link", "")
            acc_match = re.search(r"/(\d{10}-\d{2}-\d{6})/", link)
            if not acc_match:
                acc_match = re.search(r"(\d{10}-\d{2}-\d{6})", entry.get("id", ""))
            if not acc_match:
                return None
            accession = acc_match.group(1)

            cik_match = re.search(r"/data/(\d+)/", link)
            cik = cik_match.group(1) if cik_match else "000000000"

            form_type = entry.get("edgar_type", entry.get("category", "UNKNOWN"))
            if isinstance(form_type, list) and form_type:
                form_type = form_type[0].get("term", "UNKNOWN")

            company_name = entry.get("title", "Unknown")
            if " - " in company_name:
                parts = company_name.split(" - ", 1)
                if parts[0].strip() == form_type:
                    company_name = parts[1].strip()

            filing_date_str = entry.get("published", entry.get("updated", ""))
            try:
                filing_date = parsedate_to_datetime(filing_date_str).date()
            except Exception:
                filing_date = date.today()

            return RawFiling(
                accession_number=accession,
                cik=cik.lstrip("0") or "0",
                company_name=company_name,
                form_type=str(form_type),
                filing_date=filing_date,
                filing_url=link,
                source=source,
            )
        except Exception as exc:
            logger.warning("entry_parse_failed", error=str(exc), entry_id=entry.get("id"))
            return None

    async def run(self) -> None:
        await self.setup()
        try:
            while self._running:
                start = asyncio.get_event_loop().time()
                total = 0
                tasks = [self._poll_feed(fc) for fc in self.feeds]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, int):
                        total += r
                logger.info("poll_cycle_complete", published=total)
                elapsed = asyncio.get_event_loop().time() - start
                sleep_time = max(0, self.poll_interval - elapsed)
                await asyncio.sleep(sleep_time)
        finally:
            await self.teardown()


async def main() -> None:
    config = load_config()
    poller = RSSPoller(config)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, poller._stop)

    await poller.run()


if __name__ == "__main__":
    asyncio.run(main())
