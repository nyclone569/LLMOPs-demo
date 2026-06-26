variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "ap-southeast-1"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "llmops-platform"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
  default     = "llmops-cluster"
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.32"
}

variable "cluster_endpoint_private_access" {
  description = "Enable private API server endpoint"
  type        = bool
  default     = true
}

variable "cluster_endpoint_public_access" {
  description = "Enable public API server endpoint"
  type        = bool
  default     = true
}

variable "cluster_endpoint_public_access_cidrs" {
  description = "List of CIDR blocks that can access the public API server endpoint"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# System Node Group Configuration
variable "system_node_group_instance_types" {
  description = "Instance types for system node group"
  type        = list(string)
  default     = ["t3.medium"]
}

variable "system_node_group_desired_size" {
  description = "Desired number of nodes in system node group"
  type        = number
  default     = 2
}

variable "system_node_group_min_size" {
  description = "Minimum number of nodes in system node group"
  type        = number
  default     = 2
}

variable "system_node_group_max_size" {
  description = "Maximum number of nodes in system node group"
  type        = number
  default     = 4
}

# Workload Node Group Configuration
variable "workload_node_group_instance_types" {
  description = "Instance types for workload node group"
  type        = list(string)
  default     = ["t3.large"]
}

variable "workload_node_group_desired_size" {
  description = "Desired number of nodes in workload node group"
  type        = number
  default     = 2
}

variable "workload_node_group_min_size" {
  description = "Minimum number of nodes in workload node group"
  type        = number
  default     = 2
}

variable "workload_node_group_max_size" {
  description = "Maximum number of nodes in workload node group"
  type        = number
  default     = 6
}

variable "enable_cluster_autoscaler" {
  description = "Enable cluster autoscaler tags on node groups"
  type        = bool
  default     = true
}

variable "cluster_enabled_log_types" {
  description = "List of control plane logging types to enable"
  type        = list(string)
  default     = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
}

# ── Scheduled Scaling ─────────────────────────────────────────────────────────

variable "enable_scheduled_scaling" {
  description = "Enable scheduled scaling for workload node group (aligns with simulator burst windows)"
  type        = bool
  default     = true
}

variable "peak_desired" {
  description = "Desired node count during peak hours (09:00–09:30 and 14:00–14:20 ICT)"
  type        = number
  default     = 4
}

variable "peak_min" {
  description = "Minimum node count during peak — prevents CA from scaling down mid-burst"
  type        = number
  default     = 3
}

variable "warmup_desired" {
  description = "Desired node count during pre-peak warm-up window (30 min before burst)"
  type        = number
  default     = 3
}

variable "offpeak_desired" {
  description = "Desired node count during off-peak hours (lunch, evening, weekend)"
  type        = number
  default     = 2
}

variable "offpeak_min" {
  description = "Minimum node count during off-peak — baseline for CA scale-down floor"
  type        = number
  default     = 2
}
