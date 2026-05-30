resource "helm_release" "cert_manager" {
  name       = "cert-manager"
  repository = "https://charts.jetstack.io"
  chart      = "cert-manager"
  version    = var.cert_manager_version
  namespace  = kubernetes_namespace.cert_manager.metadata[0].name
  wait       = true
  wait_for_jobs = true

  set {
    name  = "installCRDs"
    value = "true"
  }

  set {
    name  = "global.leaderElection.namespace"
    value = kubernetes_namespace.cert_manager.metadata[0].name
  }

  set {
    name  = "serviceAccount.create"
    value = "true"
  }

  set {
    name  = "serviceAccount.name"
    value = "cert-manager"
  }

  set {
    name  = "prometheus.enabled"
    value = "true"
  }

  set {
    name  = "resources.requests.cpu"
    value = "10m"
  }

  set {
    name  = "resources.requests.memory"
    value = "32Mi"
  }

  set {
    name  = "resources.limits.cpu"
    value = "100m"
  }

  set {
    name  = "resources.limits.memory"
    value = "128Mi"
  }

  depends_on = [kubernetes_namespace.cert_manager]
}

# Use null_resource + kubectl to avoid CRD pre-validation at plan time
resource "null_resource" "cluster_issuer_letsencrypt_staging" {
  triggers = {
    chart_version = helm_release.cert_manager.version
  }

  provisioner "local-exec" {
    command = <<-EOF
      until kubectl get crd clusterissuers.cert-manager.io >/dev/null 2>&1; do
        echo "Waiting for ClusterIssuer CRD..."; sleep 5
      done
      kubectl apply -f - <<YAML
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging
spec:
  acme:
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    email: admin@${var.project_name}.com
    privateKeySecretRef:
      name: letsencrypt-staging
    solvers:
      - http01:
          ingress:
            class: alb
YAML
    EOF
  }

  depends_on = [helm_release.cert_manager]
}

resource "null_resource" "cluster_issuer_letsencrypt_prod" {
  triggers = {
    chart_version = helm_release.cert_manager.version
  }

  provisioner "local-exec" {
    command = <<-EOF
      until kubectl get crd clusterissuers.cert-manager.io >/dev/null 2>&1; do
        echo "Waiting for ClusterIssuer CRD..."; sleep 5
      done
      kubectl apply -f - <<YAML
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: admin@${var.project_name}.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            class: alb
YAML
    EOF
  }

  depends_on = [helm_release.cert_manager]
}
