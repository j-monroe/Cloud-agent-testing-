variable "project_name" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for the ALB"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for ECS tasks"
  type        = list(string)
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "rds_endpoint" {
  description = "RDS endpoint (host:port)"
  type        = string
  sensitive   = true
}

variable "redis_endpoint" {
  description = "Redis endpoint (host:port)"
  type        = string
  sensitive   = true
}

variable "s3_bucket_name" {
  description = "S3 bucket name for raw filing storage"
  type        = string
}

variable "secret_arn" {
  description = "Secrets Manager secret ARN"
  type        = string
}

variable "log_group_prefix" {
  description = "CloudWatch log group prefix"
  type        = string
  default     = "/edgar/development"
}

variable "db_password" {
  description = "Database password"
  type        = string
  sensitive   = true
}

variable "edgar_user_agent" {
  description = "SEC EDGAR User-Agent string"
  type        = string
}

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
  default     = "latest"
}

variable "api_task_cpu" {
  description = "CPU units for the API task"
  type        = number
  default     = 512
}

variable "api_task_memory" {
  description = "Memory (MB) for the API task"
  type        = number
  default     = 1024
}

variable "worker_task_cpu" {
  description = "CPU units for worker tasks"
  type        = number
  default     = 256
}

variable "worker_task_memory" {
  description = "Memory (MB) for worker tasks"
  type        = number
  default     = 512
}
