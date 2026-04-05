# EDGAR Filing Intelligence Platform

A production-ready, event-driven platform for ingesting, parsing, enriching, and querying SEC EDGAR filings in real time.

## Architecture

```
SEC EDGAR RSS Feeds
       │
       ▼
┌─────────────┐     Redis Streams      ┌──────────────┐
│  RSS Poller │ ──────────────────────▶│    Parser    │
│  (2 min)    │  edgar:filings:raw     │   Service    │
└─────────────┘                        └──────┬───────┘
                                              │ edgar:filings:parsed
                                              ▼
                                       ┌──────────────┐
                                       │  Enrichment  │
                                       │   Service    │──▶ data.sec.gov
                                       └──────┬───────┘
                                              │
                                              ▼
                                       ┌──────────────┐
                                       │  PostgreSQL  │
                                       │   Database   │
                                       └──────┬───────┘
                                              │
                                              ▼
                                       ┌──────────────┐
                                       │  FastAPI     │
                                       │  REST API    │
                                       └──────────────┘

Nightly: Reconciliation Job ──▶ EDGAR Full Index ──▶ Backfill Queue
```

## Services

| Service | Description | Technology |
|---------|-------------|------------|
| `rss_poller` | Polls EDGAR RSS feeds every 2 minutes | httpx, feedparser |
| `parser` | Consumes queue, normalizes and stores filings | SQLAlchemy, asyncpg |
| `enrichment` | Fetches company metadata from data.sec.gov | httpx, Redis cache |
| `reconciliation` | Nightly index comparison and backfill | EDGAR full-index |
| `api` | REST API for downstream consumers | FastAPI, PostgreSQL |

## Quick Start

### Prerequisites
- Docker + Docker Compose
- Python 3.11+ (for local development)

### Running Locally

```bash
# Copy environment file
cp .env.example .env
# Edit .env with your EDGAR_USER_AGENT

# Start all services
docker compose up -d

# Check service health
curl http://localhost:8000/health

# Query filings
curl "http://localhost:8000/filings?form_type=8-K&page=1"

# Get a specific filing
curl "http://localhost:8000/filings/0001234567-24-000001"

# Get company info
curl "http://localhost:8000/company/1234567"

# Run reconciliation manually
docker compose --profile reconciliation up reconciliation
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/filings` | List filings (with filters) |
| GET | `/filings/{accession_number}` | Get specific filing |
| GET | `/company/{cik}` | Get company metadata |
| GET | `/company/{cik}/filings` | Get company's filings |
| GET | `/search?q=...` | Full-text search (placeholder) |

#### Filing Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `date` | YYYY-MM-DD | Filter by filing date |
| `form_type` | string | Filter by form type (e.g., `8-K`, `10-K`) |
| `cik` | string | Filter by CIK |
| `company_name` | string | Partial match on company name |
| `page` | int | Page number (default: 1) |
| `page_size` | int | Results per page (default: 50, max: 500) |

## Configuration

See `config/config.yaml` for all platform settings. Key settings:

- `edgar.poll_interval_seconds`: RSS poll frequency (default: 120)
- `edgar.rate_limit.requests_per_second`: SEC API rate limit (default: 8)
- `reconciliation.lookback_days`: Days to check during reconciliation (default: 7)

## Development

### Running Tests

```bash
pip install pydantic==2.7.1 structlog==24.1.0 redis==5.0.4 sqlalchemy==2.0.30 \
            pytest==7.4.0 pytest-asyncio==0.23.0 pyyaml==6.0.1

PYTHONPATH=. python -m pytest tests/ -v
```

### Project Structure

```
├── shared/              # Shared library (models, logger, rate limiter, queue)
├── services/
│   ├── rss_poller/      # RSS polling service
│   ├── parser/          # Filing parser service
│   ├── enrichment/      # Company enrichment service
│   ├── reconciliation/  # Index reconciliation job
│   └── api/             # FastAPI REST API
├── config/              # Platform configuration
├── terraform/           # AWS infrastructure as code
└── tests/               # Unit tests
```

## Production Deployment (AWS)

See [terraform/README.md](terraform/README.md) for full AWS deployment instructions.

Resources provisioned:
- **VPC** with public/private subnets across 2 AZs
- **RDS PostgreSQL 15** (Multi-AZ in production)
- **ElastiCache Redis 7** cluster
- **S3** bucket with versioning, KMS encryption
- **ECS Fargate** cluster with all services
- **Application Load Balancer** for the API
- **Secrets Manager** for credentials
- **CloudWatch** log groups and monitoring

```bash
cd terraform
terraform init
terraform apply -var="db_password=<secure-password>"
```

## SEC EDGAR Fair Access

This platform respects SEC EDGAR's fair access policy:
- Rate limited to **≤10 requests/second** via Redis token bucket
- Proper **User-Agent** header required (set `EDGAR_USER_AGENT` in `.env`)
- Deduplication prevents re-fetching known filings

## License

MIT
