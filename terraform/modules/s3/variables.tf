variable "project_name" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "enable_object_lock" {
  description = "Enable S3 Object Lock for WORM compliance"
  type        = bool
  default     = false
}
