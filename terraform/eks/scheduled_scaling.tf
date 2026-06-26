# Scheduled scaling for workload node group
# Aligns with traffic simulator burst windows (UTC):
#   (2, 30)  = 09:00–09:30 ICT  morning peak
#   (7, 20)  = 14:00–14:20 ICT  afternoon peak
#
# All cron expressions are UTC. ICT = UTC+7.

locals {
  workload_asg_name = aws_eks_node_group.workload.resources[0].autoscaling_groups[0].name
}

# ── Morning warm-up (07:30 ICT, Mon–Fri) ─────────────────────────────────────
# Start provisioning 30 min before 09:00 burst so nodes are Ready by peak.
resource "aws_autoscaling_schedule" "morning_warmup" {
  count                  = var.enable_scheduled_scaling ? 1 : 0
  scheduled_action_name  = "${var.cluster_name}-morning-warmup"
  autoscaling_group_name = local.workload_asg_name
  recurrence             = "30 0 * * 1-5"
  desired_capacity       = var.warmup_desired
  min_size               = var.offpeak_min
  max_size               = var.workload_node_group_max_size
  time_zone              = "UTC"
}

# ── Morning peak (09:00 ICT, Mon–Fri) ────────────────────────────────────────
# Raise min so CA doesn't scale down mid-burst.
resource "aws_autoscaling_schedule" "morning_peak" {
  count                  = var.enable_scheduled_scaling ? 1 : 0
  scheduled_action_name  = "${var.cluster_name}-morning-peak"
  autoscaling_group_name = local.workload_asg_name
  recurrence             = "0 2 * * 1-5"
  desired_capacity       = var.peak_desired
  min_size               = var.peak_min
  max_size               = var.workload_node_group_max_size
  time_zone              = "UTC"
}

# ── Post-morning (12:00 ICT, Mon–Fri) ────────────────────────────────────────
# Lunch lull — let CA scale down to baseline.
resource "aws_autoscaling_schedule" "midday_lull" {
  count                  = var.enable_scheduled_scaling ? 1 : 0
  scheduled_action_name  = "${var.cluster_name}-midday-lull"
  autoscaling_group_name = local.workload_asg_name
  recurrence             = "0 5 * * 1-5"
  desired_capacity       = var.offpeak_desired
  min_size               = var.offpeak_min
  max_size               = var.workload_node_group_max_size
  time_zone              = "UTC"
}

# ── Afternoon warm-up (13:45 ICT, Mon–Fri) ───────────────────────────────────
# Pre-warm 15 min before 14:00 burst window.
resource "aws_autoscaling_schedule" "afternoon_warmup" {
  count                  = var.enable_scheduled_scaling ? 1 : 0
  scheduled_action_name  = "${var.cluster_name}-afternoon-warmup"
  autoscaling_group_name = local.workload_asg_name
  recurrence             = "45 6 * * 1-5"
  desired_capacity       = var.warmup_desired
  min_size               = var.offpeak_min
  max_size               = var.workload_node_group_max_size
  time_zone              = "UTC"
}

# ── Afternoon peak (14:00 ICT, Mon–Fri) ──────────────────────────────────────
resource "aws_autoscaling_schedule" "afternoon_peak" {
  count                  = var.enable_scheduled_scaling ? 1 : 0
  scheduled_action_name  = "${var.cluster_name}-afternoon-peak"
  autoscaling_group_name = local.workload_asg_name
  recurrence             = "0 7 * * 1-5"
  desired_capacity       = var.peak_desired
  min_size               = var.peak_min
  max_size               = var.workload_node_group_max_size
  time_zone              = "UTC"
}

# ── End of day (17:00 ICT, Mon–Fri) ──────────────────────────────────────────
resource "aws_autoscaling_schedule" "end_of_day" {
  count                  = var.enable_scheduled_scaling ? 1 : 0
  scheduled_action_name  = "${var.cluster_name}-end-of-day"
  autoscaling_group_name = local.workload_asg_name
  recurrence             = "0 10 * * 1-5"
  desired_capacity       = var.offpeak_desired
  min_size               = var.offpeak_min
  max_size               = var.workload_node_group_max_size
  time_zone              = "UTC"
}

# ── Weekend (19:00 ICT Friday → 07:30 ICT Monday) ────────────────────────────
# Explicit Friday night scale-down to prevent weekend cost bleed.
resource "aws_autoscaling_schedule" "weekend" {
  count                  = var.enable_scheduled_scaling ? 1 : 0
  scheduled_action_name  = "${var.cluster_name}-weekend"
  autoscaling_group_name = local.workload_asg_name
  recurrence             = "0 12 * * 5"
  desired_capacity       = var.offpeak_desired
  min_size               = var.offpeak_min
  max_size               = var.workload_node_group_max_size
  time_zone              = "UTC"
}
