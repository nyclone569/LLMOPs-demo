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

# Wait for the EKS API server to stabilise after LBC and cert-manager install
# their CRDs. Without this pause, ArgoCD's large CRD batch arrives while the
# API server is still under load, causing "connection reset by peer".
resource "null_resource" "api_server_settle" {
  triggers = {
    lbc_version  = helm_release.aws_load_balancer_controller.version
    cm_version   = helm_release.cert_manager.version
  }

  provisioner "local-exec" {
    command = "sleep 30"
  }

  depends_on = [
    helm_release.aws_load_balancer_controller,
    helm_release.cert_manager,
  ]
}

# Helm release for ArgoCD
resource "helm_release" "argocd" {
  name       = "argocd"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = var.argocd_version
  namespace  = kubernetes_namespace.argocd.metadata[0].name
  wait       = true
  timeout    = 600

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
    value = "HTTP"
  }

  set {
    name  = "server.ingress.annotations.alb\\.ingress\\.kubernetes\\.io/listen-ports"
    value = "[{\"HTTP\":80}]"
  }

  # No hostname restriction — accept all requests to the ALB DNS name
  # set {
  #   name  = "server.ingress.hosts[0]"
  #   value = var.argocd_ingress_host
  # }

  # Run ArgoCD server without TLS — ALB terminates TLS, forwards HTTP to pod
  set {
    name  = "configs.params.server\\.insecure"
    value = "true"
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
    null_resource.helm_repo_update,
    null_resource.api_server_settle,
  ]
}
