terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "edgar-platform"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ── VPC ───────────────────────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.project_name}-${var.environment}-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-${var.environment}-igw"
  }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project_name}-${var.environment}-public-${count.index + 1}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 4)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "${var.project_name}-${var.environment}-private-${count.index + 1}"
    Tier = "private"
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_eip" "nat" {
  count  = 1
  domain = "vpc"

  tags = {
    Name = "${var.project_name}-${var.environment}-nat-eip"
  }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id

  tags = {
    Name = "${var.project_name}-${var.environment}-nat"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-private-rt"
  }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ── Secrets Manager ───────────────────────────────────────────────────────────

resource "aws_secretsmanager_secret" "edgar_config" {
  name                    = "${var.project_name}/${var.environment}/config"
  recovery_window_in_days = 7

  tags = {
    Name = "${var.project_name}-${var.environment}-config"
  }
}

resource "aws_secretsmanager_secret_version" "edgar_config" {
  secret_id = aws_secretsmanager_secret.edgar_config.id
  secret_string = jsonencode({
    db_password    = var.db_password
    edgar_user_agent = var.edgar_user_agent
  })
}

# ── CloudWatch Log Groups ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "services" {
  for_each          = toset(["rss-poller", "parser", "enrichment", "api", "reconciliation"])
  name              = "/edgar/${var.environment}/${each.key}"
  retention_in_days = 30
}

# ── Modules ───────────────────────────────────────────────────────────────────

module "rds" {
  source = "./modules/rds"

  project_name    = var.project_name
  environment     = var.environment
  vpc_id          = aws_vpc.main.id
  subnet_ids      = aws_subnet.private[*].id
  db_password     = var.db_password
  instance_class  = var.rds_instance_class
  multi_az        = var.environment == "production"
  allowed_sg_ids  = [module.ecs.service_sg_id]
}

module "elasticache" {
  source = "./modules/elasticache"

  project_name   = var.project_name
  environment    = var.environment
  vpc_id         = aws_vpc.main.id
  subnet_ids     = aws_subnet.private[*].id
  node_type      = var.redis_node_type
  allowed_sg_ids = [module.ecs.service_sg_id]
}

module "s3" {
  source = "./modules/s3"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
}

module "ecs" {
  source = "./modules/ecs"

  project_name        = var.project_name
  environment         = var.environment
  vpc_id              = aws_vpc.main.id
  public_subnet_ids   = aws_subnet.public[*].id
  private_subnet_ids  = aws_subnet.private[*].id
  aws_region          = var.aws_region
  rds_endpoint        = module.rds.endpoint
  redis_endpoint      = module.elasticache.endpoint
  s3_bucket_name      = module.s3.bucket_name
  secret_arn          = aws_secretsmanager_secret.edgar_config.arn
  log_group_prefix    = "/edgar/${var.environment}"
  db_password         = var.db_password
  edgar_user_agent    = var.edgar_user_agent
}
