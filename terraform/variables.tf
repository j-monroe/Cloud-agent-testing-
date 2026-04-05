variable "aws_region" {
  description = "AWS region to deploy to"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (development, staging, production)"
  type        = string
  default     = "development"

  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "environment must be one of: development, staging, production"
  }
}

variable "project_name" {
  description = "Project name used in resource naming"
  type        = string
  default     = "edgar"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "db_password" {
  description = "PostgreSQL master password"
  type        = string
  sensitive   = true
}

variable "rds_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.medium"
}

variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
  default     = "cache.t3.micro"
}

variable "edgar_user_agent" {
  description = "User-Agent string for SEC EDGAR API requests"
  type        = string
  default     = "EDGAR Platform edgar@example.com"
}

variable "api_task_cpu" {
  description = "CPU units for the API task (1024 = 1 vCPU)"
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

variable "ecr_image_tag" {
  description = "Docker image tag to deploy"
  type        = string
  default     = "latest"
}
