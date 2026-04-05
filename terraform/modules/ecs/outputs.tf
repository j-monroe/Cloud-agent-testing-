output "cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "cluster_arn" {
  description = "ECS cluster ARN"
  value       = aws_ecs_cluster.main.arn
}

output "api_url" {
  description = "API load balancer DNS name"
  value       = "http://${aws_lb.api.dns_name}"
}

output "alb_arn" {
  description = "Application Load Balancer ARN"
  value       = aws_lb.api.arn
}

output "service_sg_id" {
  description = "Security group ID used by ECS services"
  value       = aws_security_group.services.id
}

output "ecr_urls" {
  description = "Map of service name to ECR repository URL"
  value = {
    for k, v in aws_ecr_repository.services : k => v.repository_url
  }
}

output "task_execution_role_arn" {
  description = "ECS task execution role ARN"
  value       = aws_iam_role.ecs_task_execution.arn
}

output "task_role_arn" {
  description = "ECS task role ARN"
  value       = aws_iam_role.ecs_task.arn
}
