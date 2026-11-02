variable "region" {
  type    = string
  default = "ap-south-1" # Mumbai: data residency for Indian customers
}
variable "environment" {
  type    = string
  default = "prod"
}
variable "domain_name" {
  type        = string
  description = "Public hostname for the console/API, e.g. app.agentgrid.example"
}
variable "acm_certificate_arn" {
  type        = string
  description = "ACM certificate (regional, for the ALB)"
}
variable "cloudfront_certificate_arn" {
  type        = string
  description = "ACM certificate in us-east-1 for CloudFront"
}
variable "image_tag" {
  type    = string
  default = "latest"
}
variable "api_desired_count" {
  type    = number
  default = 2
}
variable "api_max_count" {
  type    = number
  default = 10
}
variable "worker_desired_count" {
  type    = number
  default = 1
}
variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}
variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}
