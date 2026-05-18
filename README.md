# ITL Control Plane Portal

![Status](https://img.shields.io/badge/status-alpha-orange?style=flat-square)
![Development](https://img.shields.io/badge/development-in%20progress-yellow?style=flat-square)

> **Alpha** — This project is under active development. APIs, data models, and behaviour may change without notice.

Customer self-service portal for ITL Control Plane resource management.

## Overview

The Customer Portal provides **secure resource management** for end users:

- **Resource Browser**: View subscriptions, resource groups, and resources
- **Activity Logs**: Scoped audit trail for your resources
- **Resource Visualization**: Interactive hierarchy graphs
- **Self-Service**: Manage your own cloud resources

## Security Design

This portal is designed for **customer-facing deployment**:

- **NO Docker access**: Docker SDK explicitly excluded
- **NO Kubernetes access**: Kubernetes SDK explicitly excluded
- **NO infrastructure endpoints**: All `/api/infra/*` routes return 403
- **Scoped access**: Users only see their own resources

This ensures that even if a customer gains unauthorized access, they cannot:
- View platform containers or services
- Control infrastructure components
- Access other tenants' resources

## Quick Start

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
uvicorn main:app --host 0.0.0.0 --port 8090 --reload
```

### Docker

```bash
# Build the image
docker build -t itl-portal .

# Run (notice: NO Docker socket mount - intentional!)
docker run -d \
  -p 8090:8090 \
  -e API_GATEWAY_URL=http://api-gateway:8080 \
  -e KEYCLOAK_ISSUER=https://sts.itlusions.com/realms/itl \
  -e KEYCLOAK_CLIENT_ID=portal \
  -e KEYCLOAK_CLIENT_SECRET=your-secret \
  itl-portal
```

### Kubernetes

When running in Kubernetes:
- **DO NOT** mount a ServiceAccount with cluster access
- Use a minimal ServiceAccount or default
- The portal does not need any Kubernetes API access

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: itl-portal
spec:
  template:
    spec:
      # Use a restricted service account
      serviceAccountName: itl-portal-restricted
      automountServiceAccountToken: false  # Disable token mount
      containers:
        - name: portal
          image: itl-portal:latest
          ports:
            - containerPort: 8090
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Server port | `8090` |
| `API_GATEWAY_URL` | ITL API Gateway URL | `http://localhost:8081` |
| `KEYCLOAK_ISSUER` | Keycloak realm URL | (required for auth) |
| `KEYCLOAK_CLIENT_ID` | OIDC client ID | (required for auth) |
| `KEYCLOAK_CLIENT_SECRET` | OIDC client secret | (required for auth) |
| `SECRET_KEY` | Session encryption key | `dev-secret-key` |

## Features

### Resource Management

- Browse all resource types (tenants, subscriptions, resource groups)
- Resource detail pages with properties and relationships
- Tag management and filtering
- Resource visualization with D3.js graphs

### User Experience

- Clean, modern UI with blue color scheme
- Mobile-responsive design
- Quick links and navigation
- Activity log with filtering

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                Customer Portal (8090)                   │
│             Resource management only                    │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │  API Gateway  │
                    │   (8080)      │
                    └───────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
   ┌─────────┐       ┌──────────┐       ┌──────────┐
   │  Core   │       │   IAM    │       │ Compute  │
   │Provider │       │ Provider │       │ Provider │
   └─────────┘       └──────────┘       └──────────┘

   ❌ NO direct Docker/K8s access
```

## Differences from Admin Portal

| Feature | Customer Portal | Admin Portal |
|---------|-----------------|--------------|
| Docker access | ❌ No | ✅ Yes |
| Kubernetes access | ❌ No | ✅ Yes |
| Container management | ❌ No | ✅ Yes |
| Service health | ❌ No | ✅ Yes |
| Tenant access | Scoped | ✅ All |
| Target audience | Customers | Platform operators |

## Security Best Practices

1. **Never mount Docker socket** - Even read-only access exposes container data
2. **Disable K8s ServiceAccount** - Set `automountServiceAccountToken: false`
3. **Use separate OIDC client** - Different client than Admin Portal
4. **Network policies** - Restrict egress to only API Gateway
5. **Rate limiting** - Apply at ingress level

## Related Projects

- [ITL.ControlPlane.Admin](../ITL.ControlPlane.Admin) - Admin portal (operators only)
- [ITL.ControlPlane.Dashboard](../ITL.ControlPlane.Dashboard) - Original combined dashboard
- [ITL.ControlPlane.Api](../ITL.ControlPlane.Api) - API Gateway
