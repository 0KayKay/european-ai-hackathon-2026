output "cluster_name" {
  value = google_container_cluster.cluster.name
}

output "region" {
  value = local.region
}

output "frontend_ip" {
  value = google_compute_global_address.frontend.address
}

output "agentgateway_egress_ip" {
  description = "Static IP to allowlist on the future custom LLM cluster."
  value       = google_compute_address.egress.address
}

output "image_repository" {
  value = "${local.region}-docker.pkg.dev/${local.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "agentgateway_service_account" {
  value = google_service_account.agentgateway.email
}
