# Infrastructure as code (reference: written, not applied)

| Path | What it is |
|------|------------|
| `terraform/` | AWS production reference architecture: VPC across 3 AZs, ECS Fargate (Graviton) API and worker services, ALB, CloudFront, RDS PostgreSQL 16 with pgvector, ElastiCache Redis, MSK Serverless (Kafka), S3, Secrets Manager, CloudWatch logs and autoscaling. |
| `k8s/` | Kubernetes manifests for any cluster (EKS, GKE, AKS or k3s). API and worker Deployments, Service, HPA, PDB, ConfigMap, Secret template, migration Job, NGINX Ingress with WebSocket timeouts, and a Kustomization. |

Both use the same multi-arch Docker image as `docker-compose.yml`.

## Terraform

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # edit the domain and certificates
terraform init
terraform plan
```

Formatting and syntax are checked with `terraform fmt -check`. Nothing has been applied. See
[`../docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md) for the full AWS rollout plan and cost notes.

## Kubernetes

```bash
kubectl apply -f infra/k8s/namespace.yaml
kubectl -n agentgrid create secret generic agentgrid-secrets --from-env-file=.env.production
kubectl apply -f infra/k8s/migrate-job.yaml      # alembic upgrade head
kubectl apply -k infra/k8s
```
