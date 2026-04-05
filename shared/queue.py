"""
Redis Streams-based message queue client.
Provides at-least-once delivery with consumer groups and dead-letter support.
"""
from __future__ import annotations
import json
from typing import AsyncIterator, Optional

import redis.asyncio as aioredis

from shared.logger import get_logger
from shared.models import QueueMessage

logger = get_logger(__name__)


class StreamQueueClient:
    """Async Redis Streams queue client."""

    def __init__(
        self,
        redis_url: str,
        stream: str,
        consumer_group: str,
        consumer_name: str,
        dlq_stream: str = "edgar:filings:dlq",
        max_retry: int = 3,
    ) -> None:
        self.redis_url = redis_url
        self.stream = stream
        self.group = consumer_group
        self.consumer = consumer_name
        self.dlq_stream = dlq_stream
        self.max_retry = max_retry
        self._client: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        self._client = await aioredis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        try:
            await self._client.xgroup_create(
                self.stream, self.group, id="0", mkstream=True
            )
            logger.info("consumer_group_created", stream=self.stream, group=self.group)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def publish(self, message: QueueMessage) -> str:
        """Publish a message to the stream. Returns the message ID."""
        assert self._client, "Not connected"
        data = {"data": message.model_dump_json()}
        msg_id = await self._client.xadd(self.stream, data)
        logger.debug("message_published", stream=self.stream, id=msg_id)
        return msg_id

    async def consume(
        self, batch_size: int = 10, block_ms: int = 5000
    ) -> AsyncIterator[tuple[str, QueueMessage]]:
        """
        Yield (message_id, QueueMessage) tuples from the consumer group.
        Processes pending messages first, then new ones.
        """
        assert self._client, "Not connected"
        # First, process pending (unacknowledged) messages
        pending = await self._client.xreadgroup(
            self.group, self.consumer, {self.stream: "0"}, count=batch_size
        )
        for _stream_name, messages in (pending or []):
            for msg_id, fields in messages:
                yield msg_id, self._decode(fields)

        # Then read new messages
        while True:
            results = await self._client.xreadgroup(
                self.group,
                self.consumer,
                {self.stream: ">"},
                count=batch_size,
                block=block_ms,
            )
            if not results:
                continue
            for _stream_name, messages in results:
                for msg_id, fields in messages:
                    yield msg_id, self._decode(fields)

    async def ack(self, message_id: str) -> None:
        """Acknowledge successful processing."""
        assert self._client
        await self._client.xack(self.stream, self.group, message_id)
        logger.debug("message_acked", id=message_id)

    async def nack(self, message_id: str, message: QueueMessage) -> None:
        """Handle processing failure - retry or send to DLQ."""
        assert self._client
        if message.retry_count >= self.max_retry:
            logger.warning(
                "message_to_dlq",
                id=message_id,
                retry_count=message.retry_count,
            )
            dlq_data = {"data": message.model_dump_json(), "original_id": message_id}
            await self._client.xadd(self.dlq_stream, dlq_data)
        else:
            message.retry_count += 1
            await self.publish(message)
            logger.info(
                "message_retried",
                id=message_id,
                retry_count=message.retry_count,
            )
        await self.ack(message_id)

    def _decode(self, fields: dict) -> QueueMessage:
        raw = json.loads(fields["data"])
        return QueueMessage(**raw)
