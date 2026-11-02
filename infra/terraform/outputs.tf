output "ecr_repository_url" { value = aws_ecr_repository.api.repository_url }
output "alb_dns_name" { value = aws_lb.api.dns_name }
output "cloudfront_domain" { value = aws_cloudfront_distribution.edge.domain_name }
output "rds_endpoint" { value = aws_db_instance.postgres.address }
output "redis_endpoint" { value = aws_elasticache_replication_group.redis.primary_endpoint_address }
output "msk_cluster_arn" { value = aws_msk_serverless_cluster.events.arn }
output "app_secret_arn" { value = aws_secretsmanager_secret.app.arn }
