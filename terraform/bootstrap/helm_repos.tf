# Populate the local Helm repo cache before any helm_release resource runs.
# The Terraform Helm provider resolves chart URLs against ~/.cache/helm/repository/,
# which is empty on a fresh machine. This local-exec runs `helm repo add` + `helm repo update`
# once per apply to ensure the cache is warm.
resource "null_resource" "helm_repo_update" {
  triggers = {
    # Re-run whenever the set of repo URLs changes.
    repos = join(",", [
      "https://aws.github.io/eks-charts",
      "https://charts.jetstack.io",
      "https://kubernetes-sigs.github.io/metrics-server/",
      "https://charts.external-secrets.io",
      "https://argoproj.github.io/argo-helm",
    ])
  }

  provisioner "local-exec" {
    command = <<-EOT
      helm repo add eks             https://aws.github.io/eks-charts
      helm repo add jetstack        https://charts.jetstack.io
      helm repo add metrics-server  https://kubernetes-sigs.github.io/metrics-server/
      helm repo add external-secrets https://charts.external-secrets.io
      helm repo add argo            https://argoproj.github.io/argo-helm
      helm repo update
    EOT
  }
}
