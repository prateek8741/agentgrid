terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.70" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Use an S3 backend with DynamoDB locking in a real account:
  # backend "s3" { bucket = "agentgrid-tfstate" key = "prod/terraform.tfstate" region = "ap-south-1" dynamodb_table = "agentgrid-tflock" }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "agentgrid", Environment = var.environment, ManagedBy = "terraform" }
  }
}
