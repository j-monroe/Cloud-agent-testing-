data "aws_caller_identity" "current" {}

resource "aws_kms_key" "s3" {
  description             = "KMS key for EDGAR S3 bucket encryption"
  deletion_window_in_days = 10
  enable_key_rotation     = true

  tags = {
    Name = "${var.project_name}-${var.environment}-s3-key"
  }
}

resource "aws_kms_alias" "s3" {
  name          = "alias/${var.project_name}-${var.environment}-s3"
  target_key_id = aws_kms_key.s3.key_id
}

resource "aws_s3_bucket" "raw_filings" {
  bucket = "${var.project_name}-${var.environment}-raw-filings-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "${var.project_name}-${var.environment}-raw-filings"
  }
}

resource "aws_s3_bucket_versioning" "raw_filings" {
  bucket = aws_s3_bucket.raw_filings.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw_filings" {
  bucket = aws_s3_bucket.raw_filings.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "raw_filings" {
  bucket = aws_s3_bucket.raw_filings.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "raw_filings" {
  bucket = aws_s3_bucket.raw_filings.id

  rule {
    id     = "transition-to-ia"
    status = "Enabled"

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER"
    }
  }
}

resource "aws_s3_bucket_object_lock_configuration" "raw_filings" {
  count  = var.enable_object_lock ? 1 : 0
  bucket = aws_s3_bucket.raw_filings.id

  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = 365
    }
  }

  depends_on = [aws_s3_bucket_versioning.raw_filings]
}
