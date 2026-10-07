locals {
  quotas = {
    small  = { cpu = "2", memory = "4Gi", pods = "10" }
    medium = { cpu = "4", memory = "8Gi", pods = "20" }
    large  = { cpu = "8", memory = "16Gi", pods = "40" }
  }
  quota = local.quotas[var.size]

  # Kubernetes has labels instead of tags. Label values cannot hold "@" or ":", so the
  # owner and the expiry timestamp go into annotations and the labels carry the ID.
  labels = merge(var.labels, {
    "app.kubernetes.io/managed-by" = "env-portal"
    "portal-environment-id"        = var.environment_id
    "portal-template"              = "k8s-namespace"
  })
  annotations = {
    "env-portal/owner"      = var.owner
    "env-portal/expires-at" = var.expires_at
  }
}

resource "kubernetes_namespace_v1" "this" {
  metadata {
    name        = var.name
    labels      = local.labels
    annotations = local.annotations
  }
}

resource "kubernetes_resource_quota_v1" "this" {
  metadata {
    name      = "environment"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  spec {
    hard = {
      "requests.cpu"    = local.quota.cpu
      "requests.memory" = local.quota.memory
      "limits.cpu"      = local.quota.cpu
      "limits.memory"   = local.quota.memory
      "pods"            = local.quota.pods
      # LoadBalancer services would create cloud resources the portal does not track.
      "services.loadbalancers" = "0"
    }
  }
}

# The quota rejects pods without requests and limits; these defaults keep plain
# manifests working.
resource "kubernetes_limit_range_v1" "this" {
  metadata {
    name      = "defaults"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  spec {
    limit {
      type = "Container"
      default = {
        cpu    = "250m"
        memory = "256Mi"
      }
      default_request = {
        cpu    = "100m"
        memory = "128Mi"
      }
    }
  }
}

resource "kubernetes_network_policy_v1" "default_deny_ingress" {
  metadata {
    name      = "default-deny-ingress"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  spec {
    pod_selector {}
    policy_types = ["Ingress"]
  }
}

resource "kubernetes_network_policy_v1" "allow_same_namespace" {
  metadata {
    name      = "allow-same-namespace"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  spec {
    pod_selector {}
    policy_types = ["Ingress"]

    ingress {
      from {
        pod_selector {}
      }
    }
  }
}

resource "kubernetes_role_binding_v1" "owner" {
  metadata {
    name      = "owner-edit"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "edit"
  }

  subject {
    api_group = "rbac.authorization.k8s.io"
    kind      = "User"
    name      = var.owner
  }
}
