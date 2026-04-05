output "endpoint" {
  description = "Redis primary endpoint"
  value       = "${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379"
  sensitive   = true
}

output "primary_endpoint_address" {
  description = "Redis primary endpoint address"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
  sensitive   = true
}

output "security_group_id" {
  description = "Security group ID of the Redis cluster"
  value       = aws_security_group.redis.id
}

output "port" {
  description = "Redis port"
  value       = 6379
}
