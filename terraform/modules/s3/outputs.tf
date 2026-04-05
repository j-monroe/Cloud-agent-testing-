output "bucket_name" {
  description = "Name of the S3 bucket"
  value       = aws_s3_bucket.raw_filings.id
}

output "bucket_arn" {
  description = "ARN of the S3 bucket"
  value       = aws_s3_bucket.raw_filings.arn
}

output "kms_key_arn" {
  description = "ARN of the KMS key used for S3 encryption"
  value       = aws_kms_key.s3.arn
}
