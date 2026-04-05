# Terraform Infrastructure for EDGAR Filing Intelligence Platform

## Prerequisites

- Terraform >= 1.5.0
- AWS CLI configured with appropriate credentials
- An S3 bucket for Terraform state (optional but recommended)

## Quick Start

```bash
cd terraform
terraform init
terraform plan -var="db_password=<your-secure-password>"
terraform apply -var="db_password=<your-secure-password>"
```

## Modules

| Module | Description |
|--------|-------------|
| `rds` | PostgreSQL RDS instance (Multi-AZ in production) |
| `elasticache` | Redis ElastiCache cluster |
| `s3` | Raw filing storage with versioning and encryption |
| `ecs` | ECS Fargate cluster and task definitions |

## Variables

See `variables.tf` for all configurable options.

## Outputs

After apply, outputs include:
- `api_url` - Load balancer DNS for the API service
- `rds_endpoint` - PostgreSQL connection endpoint
- `redis_endpoint` - Redis connection endpoint
- `s3_bucket_name` - Raw storage bucket name

## Remote State (recommended)

```hcl
terraform {
  backend "s3" {
    bucket = "my-terraform-state"
    key    = "edgar-platform/terraform.tfstate"
    region = "us-east-1"
  }
}
```
