# EDGAR Filing Intelligence Platform — Architecture

## System Overview

The platform is a microservices-based, event-driven pipeline for ingesting and querying SEC EDGAR filings.

## Component Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                         SEC EDGAR                                 │
│  RSS Feeds (atom)          Full Index (company.gz)               │
└────────────┬───────────────────────────┬─────────────────────────┘
             │                           │
             ▼                           ▼ (nightly)
    ┌─────────────────┐         ┌─────────────────────┐
    │   RSS Poller    │         │  Reconciliation Job │
    │  (every 2 min)  │         │  (cron 02:00 UTC)   │
    └────────┬────────┘         └──────────┬──────────┘
             │                             │
             └──────────┬──────────────────┘
                        │ Redis Streams
                        │ edgar:filings:raw
                        ▼
               ┌─────────────────┐
               │  Parser Service │
               │  - Normalize    │
               │  - Store raw    │
               │  - Upsert DB    │
               └────────┬────────┘
                        │ Redis Streams
                        │ edgar:filings:parsed
                        ▼
               ┌──────────────────────┐
               │  Enrichment Service  │──── data.sec.gov ───▶ Redis Cache
               │  - Company metadata  │
               │  - Update filing     │
               └──────────┬───────────┘
                          │
                          ▼
               ┌──────────────────────┐
               │     PostgreSQL        │
               │  filings + companies  │
               └──────────┬───────────┘
                          │
                          ▼
               ┌──────────────────────┐
               │     FastAPI API       │
               │  /filings            │
               │  /company/{cik}      │
               │  /search             │
               └──────────────────────┘
```

## Data Flow

### Ingestion Path (real-time)
1. **RSS Poller** fetches EDGAR RSS feeds, extracts accession numbers
2. Deduplication check via Redis SET (`edgar:seen:{accession_key}`, TTL 7 days)
3. New filings published to `edgar:filings:raw` Redis Stream
4. **Parser** consumes stream, validates `RawFiling` model, stores JSON to disk/S3
5. Filing upserted to `filings` PostgreSQL table with status `parsed`
6. Parser publishes to `edgar:filings:parsed` stream
7. **Enrichment** fetches company data from `data.sec.gov/submissions/CIK{cik}.json`
8. Company metadata cached in Redis (24h TTL), upserted to `companies` table
9. Filing status updated to `enriched`

### Reconciliation Path (nightly)
1. Downloads `company.gz` from EDGAR full-index for each lookback day
2. Parses fixed-width format, extracts accession numbers
3. Queries PostgreSQL for existing accessions on that date
4. Missing entries published to `edgar:filings:raw` stream (backfill)

## Message Queue Design

### Streams
| Stream | Producer | Consumer | Purpose |
|--------|----------|----------|---------|
| `edgar:filings:raw` | RSS Poller, Reconciliation | Parser | Raw filing events |
| `edgar:filings:parsed` | Parser | Enrichment | Parsed filing events |
| `edgar:filings:dlq` | Parser, Enrichment | Manual | Dead letter queue |

### At-Least-Once Delivery
- Consumer groups with explicit ACK
- Unacknowledged messages replayed on startup via pending reads (ID `"0"`)
- Failed messages retried up to `max_retry` times, then sent to DLQ

## Rate Limiting

Token bucket algorithm backed by Redis Lua script:
- **Capacity**: 10 tokens (burst)
- **Refill rate**: 8 tokens/second
- Shared across all service instances (Redis-backed)
- Respects SEC EDGAR fair access policy (≤10 req/sec)

## Database Schema

### `filings` table
```sql
accession_number VARCHAR(25) PRIMARY KEY
cik              VARCHAR(20)
company_name     VARCHAR(500)
form_type        VARCHAR(50)
filing_date      DATE
filing_url       TEXT
source           VARCHAR(20)   -- rss | index | fallback
status           ENUM          -- pending | parsed | enriched | failed
raw_storage_path TEXT
ingested_at      TIMESTAMP
parsed_at        TIMESTAMP
enriched_at      TIMESTAMP
```

### `companies` table
```sql
cik                   VARCHAR(20) PRIMARY KEY
name                  VARCHAR(500)
tickers               JSON
exchanges             JSON
sic                   VARCHAR(10)
sic_description       VARCHAR(200)
state_of_incorporation VARCHAR(10)
fiscal_year_end       VARCHAR(10)
addresses             JSON
fetched_at            TIMESTAMP
updated_at            TIMESTAMP
```

## AWS Production Architecture

```
Internet
    │
    ▼
Application Load Balancer (public)
    │
    ▼
ECS Fargate (API service) ── private subnet
    │
    ├── RDS PostgreSQL 15 (Multi-AZ)
    ├── ElastiCache Redis 7
    └── S3 (raw storage, KMS encrypted)

ECS Fargate Workers (private subnet):
├── rss_poller
├── parser
└── enrichment

EventBridge Scheduler ──▶ ECS Run Task (reconciliation, nightly)
```

## Structured Logging

All services emit JSON-structured logs via `structlog`:
```json
{
  "event": "filing_published",
  "level": "info",
  "logger": "rss_poller",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "accession": "0001234567-24-000001",
  "form_type": "8-K",
  "company": "ACME Corp"
}
```
