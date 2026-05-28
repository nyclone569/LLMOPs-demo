# Generate random password for ArgoCD if not provided
resource "random_password" "argocd_admin" {
  count   = var.argocd_admin_password == "" ? 1 : 0
  length  = 16
  special = true
}

# Bcrypt hash the password
locals {
  argocd_admin_password = var.argocd_admin_password != "" ? var.argocd_admin_password : random_password.argocd_admin[0].result
}

# Helm release for ArgoCD
resource "helm_release" "argocd" {
  name       = "argocd"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = var.argocd_version
  namespace  = kubernetes_namespace.argocd.metadata[0].name

  # Disable default admin password generation
  set {
    name  = "configs.secret.argocdServerAdminPassword"
    value = bcrypt(local.argocd_admin_password)
  }

  set {
    name  = "configs.secret.argocdServerAdminPasswordMtime"
    value = timestamp()
  }

  # Server configuration
  set {
    name  = "server.service.type"
    value = var.argocd_ingress_enabled ? "ClusterIP" : "LoadBalancer"
  }

  # Ingress configuration
  set {
    name  = "server.ingress.enabled"
    value = var.argocd_ingress_enabled
  }

  set {
    name  = "server.ingress.ingressClassName"
    value = "alb"
  }

  set {
    name  = "server.ingress.annotations.alb\\.ingress\\.kubernetes\\.io/scheme"
    value = "internet-facing"
  }

  set {
    name  = "server.ingress.annotations.alb\\.ingress\\.kubernetes\\.io/target-type"
    value = "ip"
  }

  set {
    name  = "server.ingress.annotations.alb\\.ingress\\.kubernetes\\.io/backend-protocol"
    value = "HTTPS"
  }

  set {
    name  = "server.ingress.annotations.alb\\.ingress\\.kubernetes\\.io/listen-ports"
    value = "[{\"HTTPS\":443}]"
  }

  set {
    name  = "server.ingress.hosts[0]"
    value = var.argocd_ingress_host
  }

  # Enable metrics
  set {
    name  = "server.metrics.enabled"
    value = "true"
  }

  set {
    name  = "controller.metrics.enabled"
    value = "true"
  }

  set {
    name  = "repoServer.metrics.enabled"
    value = "true"
  }

  # Resource limits
  set {
    name  = "server.resources.requests.cpu"
    value = "100m"
  }

  set {
    name  = "server.resources.requests.memory"
    value = "128Mi"
  }

  set {
    name  = "server.resources.limits.cpu"
    value = "500m"
  }

  set {
    name  = "server.resources.limits.memory"
    value = "512Mi"
  }

  depends_on = [
    kubernetes_namespace.argocd,
    helm_release.aws_load_balancer_controller
  ]
}
