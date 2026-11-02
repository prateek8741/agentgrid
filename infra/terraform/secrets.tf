# Application secrets live in Secrets Manager and are injected into ECS tasks
# as environment variables; nothing sensitive is stored in task definitions.
resource "random_password" "jwt" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "agentgrid/${var.environment}/app"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    JWT_SECRET   = random_password.jwt.result
    DATABASE_URL = "postgresql+asyncpg://agentgrid:${random_password.db.result}@${aws_db_instance.postgres.address}:5432/agentgrid?sslmode=require"
    REDIS_URL    = "rediss://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0"
    # Filled in by operators after apply (kept out of state history):
    GROQ_API_KEY          = ""
    GEMINI_API_KEY        = ""
    WHATSAPP_APP_SECRET   = ""
    WHATSAPP_ACCESS_TOKEN = ""
    SENTRY_DSN            = ""
  })
  lifecycle {
    ignore_changes = [secret_string]
  }
}
