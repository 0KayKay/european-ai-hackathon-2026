locals {
  project_id = "european-ai-hackathon-2026"
  region     = "europe-west1"
  name       = "hackathon-2026"
}

resource "google_project_service" "required" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
  ])

  project            = local.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_compute_network" "cluster" {
  name                    = local.name
  auto_create_subnetworks = false

  depends_on = [google_project_service.required]
}

resource "google_compute_subnetwork" "cluster" {
  name                     = local.name
  region                   = local.region
  network                  = google_compute_network.cluster.id
  ip_cidr_range            = "10.40.0.0/20"
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.41.0.0/16"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.42.0.0/20"
  }
}

resource "google_compute_router" "cluster" {
  name    = local.name
  region  = local.region
  network = google_compute_network.cluster.id
}

resource "google_compute_address" "egress" {
  name   = "${local.name}-egress"
  region = local.region

  depends_on = [google_project_service.required]
}

resource "google_compute_router_nat" "cluster" {
  name                               = local.name
  router                             = google_compute_router.cluster.name
  region                             = local.region
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [google_compute_address.egress.self_link]
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

data "google_compute_default_service_account" "nodes" {
  project = local.project_id
}

resource "google_project_iam_member" "nodes" {
  project = local.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = data.google_compute_default_service_account.nodes.member
}

resource "google_container_cluster" "cluster" {
  name                = local.name
  location            = local.region
  enable_autopilot    = true
  deletion_protection = true
  network             = google_compute_network.cluster.id
  subnetwork          = google_compute_subnetwork.cluster.id

  release_channel {
    channel = "REGULAR"
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  workload_identity_config {
    workload_pool = "${local.project_id}.svc.id.goog"
  }

  depends_on = [google_project_iam_member.nodes, google_compute_router_nat.cluster]
}

resource "google_artifact_registry_repository" "images" {
  location      = local.region
  repository_id = local.name
  format        = "DOCKER"

  depends_on = [google_project_service.required]
}

resource "google_artifact_registry_repository_iam_member" "node_image_pull" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.repository_id
  role       = "roles/artifactregistry.reader"
  member     = data.google_compute_default_service_account.nodes.member
}

resource "google_compute_global_address" "frontend" {
  name = "${local.name}-frontend"

  depends_on = [google_project_service.required]
}

resource "google_service_account" "agentgateway" {
  account_id   = "hackathon-agentgateway"
  display_name = "Hackathon agentgateway Vertex AI"
}

resource "google_project_iam_member" "agentgateway_vertex" {
  project = local.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agentgateway.email}"
}

resource "google_service_account_iam_member" "agentgateway_workload" {
  service_account_id = google_service_account.agentgateway.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.project_id}.svc.id.goog[default/agentgateway]"

  depends_on = [google_container_cluster.cluster]
}
