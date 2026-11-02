# ---- PostgreSQL 16 with pgvector (RDS supports the `vector` extension natively) ----
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_parameter_group" "pg16" {
  name   = "agentgrid-pg16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "postgres" {
  identifier                   = "agentgrid-${var.environment}"
  engine                       = "postgres"
  engine_version               = "16.4"
  instance_class               = var.db_instance_class
  allocated_storage            = 50
  max_allocated_storage        = 500
  storage_encrypted            = true
  db_name                      = "agentgrid"
  username                     = "agentgrid"
  password                     = random_password.db.result
  multi_az                     = var.environment == "prod"
  db_subnet_group_name         = module.vpc.database_subnet_group_name
  vpc_security_group_ids       = [aws_security_group.data.id]
  parameter_group_name         = aws_db_parameter_group.pg16.name
  backup_retention_period      = 14
  deletion_protection          = true
  performance_insights_enabled = true
  skip_final_snapshot          = false
  final_snapshot_identifier    = "agentgrid-${var.environment}-final"
}

# ---- Redis (sessions, rate limits, caches, pub/sub for the live inbox) ----
resource "aws_elasticache_subnet_group" "redis" {
  name       = "agentgrid-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "agentgrid-${var.environment}"
  description                = "AgentGrid Redis"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  num_cache_clusters         = var.environment == "prod" ? 2 : 1
  automatic_failover_enabled = var.environment == "prod"
  subnet_group_name          = aws_elasticache_subnet_group.redis.name
  security_group_ids         = [aws_security_group.data.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  parameter_group_name       = "default.redis7"
}

# ---- Kafka event bus (MSK Serverless, IAM auth) ----
resource "aws_msk_serverless_cluster" "events" {
  cluster_name = "agentgrid-${var.environment}"
  vpc_config {
    subnet_ids         = module.vpc.private_subnets
    security_group_ids = [aws_security_group.data.id]
  }
  client_authentication {
    sasl {
      iam {
        enabled = true
      }
    }
  }
}

# ---- S3: knowledge-base originals, exports and DB dumps ----
resource "aws_s3_bucket" "files" {
  bucket = "agentgrid-${var.environment}-files-${data.aws_caller_identity.current.account_id}"
}
resource "aws_s3_bucket_public_access_block" "files" {
  bucket                  = aws_s3_bucket.files.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "files" {
  bucket = aws_s3_bucket.files.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

data "aws_caller_identity" "current" {}
