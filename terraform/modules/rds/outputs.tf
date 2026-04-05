output "endpoint" {
  description = "RDS instance endpoint (host:port)"
  value       = "${aws_db_instance.postgres.address}:${aws_db_instance.postgres.port}"
  sensitive   = true
}

output "address" {
  description = "RDS instance hostname"
  value       = aws_db_instance.postgres.address
  sensitive   = true
}

output "port" {
  description = "RDS instance port"
  value       = aws_db_instance.postgres.port
}

output "security_group_id" {
  description = "Security group ID of the RDS instance"
  value       = aws_security_group.rds.id
}

output "db_name" {
  description = "Database name"
  value       = aws_db_instance.postgres.db_name
}
