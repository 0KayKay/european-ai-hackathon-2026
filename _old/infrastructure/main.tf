terraform {
  required_version = ">= 1.6.0"
  backend "gcs" {
    bucket = "aixchange-terraform-state"
  }
  required_providers {
    google      = { source = "hashicorp/google", version = "6.28.0" }
    google-beta = { source = "hashicorp/google-beta", version = ">= 7.0" }
    random      = { source = "hashicorp/random", version = ">= 3.5" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
provider "google" {
  alias                 = "apikeys"
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "compute.googleapis.com",
    "apikeys.googleapis.com",
    "identitytoolkit.googleapis.com",
    "servicenetworking.googleapis.com"
  ])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "challenge_registry" {
  location      = var.region
  project       = var.project_id
  repository_id = "challenge-repository"
  description   = "Challenge Repository"
  format        = "DOCKER"
  cleanup_policies {
    id     = "delete_old_images"
    action = "DELETE"
    condition { older_than = "432000s" }
  }
  cleanup_policies {
    id     = "keep_last_5_versions"
    action = "KEEP"
    most_recent_versions { keep_count = 5 }
  }
  depends_on = [google_project_service.apis]
}

resource "random_password" "db_password" {
  length  = 32
  special = true
}
resource "random_password" "signing_secret" {
  length  = 48
  special = true
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "app-db-password"
  replication {
    auto {}
  }
}
resource "google_secret_manager_secret_version" "db_password_v" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result
}
resource "google_secret_manager_secret" "signing_secret" {
  secret_id = "app-signing-secret"
  replication {
    auto {}
  }
}
resource "google_secret_manager_secret_version" "signing_secret_v" {
  secret      = google_secret_manager_secret.signing_secret.id
  secret_data = random_password.signing_secret.result
}

resource "google_apikeys_key" "idp_key" {
  name         = "idp-api-key"
  display_name = "Identity Platform REST key"
  provider     = google.apikeys
  project      = var.project_id

  restrictions {
    api_targets { service = "identitytoolkit.googleapis.com" }
    api_targets { service = "securetoken.googleapis.com" }
  }
  depends_on = [google_project_service.apis]
}
resource "google_secret_manager_secret" "idp_api_key" {
  secret_id = "identity-platform-api-key"
  replication {
    auto {}
  }
}
resource "google_secret_manager_secret_version" "idp_api_key_v" {
  secret      = google_secret_manager_secret.idp_api_key.id
  secret_data = google_apikeys_key.idp_key.key_string
}

resource "google_sql_database_instance" "pg" {
  name             = var.db_instance_name
  region           = var.region
  project          = var.project_id
  database_version = var.db_version
  settings {
    tier = var.db_tier
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.main.id
    }
  }

  deletion_protection = false
  depends_on = [
    google_project_service.apis,
    google_service_networking_connection.private_vpc_connection
  ]
}
resource "google_sql_database" "app_db" {
  name     = var.db_name
  instance = google_sql_database_instance.pg.name
}
resource "google_sql_user" "app_user" {
  name     = var.db_user
  password = random_password.db_password.result
  instance = google_sql_database_instance.pg.name
}

resource "google_service_account" "run_sa" {
  account_id   = "crun-app-sa"
  display_name = "Cloud Run app service account"
}
resource "google_project_iam_member" "run_sa_cloudsql" {
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.run_sa.email}"
  project = var.project_id
}
resource "google_project_iam_member" "run_sa_secret_access" {
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.run_sa.email}"
  project = var.project_id
}

resource "google_cloud_run_v2_service" "app" {
  name                = var.service_name
  location            = var.region
  deletion_protection = false

  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    session_affinity = true
    service_account  = google_service_account.run_sa.email

    containers {
      image = var.image
      resources {
        cpu_idle = true
        limits = {
          cpu    = "1000m"
          memory = "1024Mi"
        }
      }

      env {
        name  = "APP_ENV"
        value = "prod"
      }
      env {
        name  = "LOG_FORMAT"
        value = "json"
      }
      env {
        name = "IDENTITY_PLATFORM_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.idp_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "SIGNING_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.signing_secret.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "DB_USER"
        value = var.db_user
      }

      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "DB_NAME"
        value = var.db_name
      }

      env {
        name  = "DB_HOST"
        value = google_sql_database_instance.pg.private_ip_address
      }

      env {
        name  = "CLOUD_SQL_CONNECTION_NAME"
        value = google_sql_database_instance.pg.connection_name
      }
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main_subnet.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  name     = google_cloud_run_v2_service.app.name
  location = google_cloud_run_v2_service.app.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

locals {
  run_host = try(element(split("/", google_cloud_run_v2_service.app.uri), 2), null)
  authorized_domains = compact([
    var.app_domain,
    "${var.service_name}-${var.project_number}.${var.region}.run.app",
    local.run_host,
    var.allow_localhost ? "localhost" : null
  ])
}

resource "google_identity_platform_config" "idp" {
  provider = google.apikeys
  project  = var.project_id
  sign_in {
    allow_duplicate_emails = false
    email {
      enabled           = true
      password_required = false
    }
  }
  authorized_domains = local.authorized_domains
  depends_on         = [google_cloud_run_v2_service.app]
}

resource "google_service_account" "deploy_sa" {
  account_id   = "github-deployer"
  display_name = "GitHub Deployer"
}

resource "google_project_iam_member" "deploy_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.deploy_sa.email}"
}

resource "google_service_account_iam_member" "deploy_sa_user_on_run_sa" {
  service_account_id = google_service_account.run_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy_sa.email}"
}

resource "google_project_iam_member" "deploy_ar_writer_project" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deploy_sa.email}"
}

resource "google_compute_network" "main" {
  name                    = "main-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "main_subnet" {
  name          = "main-subnet"
  ip_cidr_range = "10.8.0.0/24"
  region        = var.region
  network       = google_compute_network.main.id
}

resource "google_compute_global_address" "google_services_range" {
  name          = "google-services-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network = google_compute_network.main.id
  service = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [
    google_compute_global_address.google_services_range.name
  ]
}

resource "google_project_iam_member" "run_sa_aiplatform_user" {
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.run_sa.email}"
  project = var.project_id
}


resource "google_compute_global_address" "lb_ip" {
  name = "${var.service_name}-lb-ip"
}

resource "google_compute_managed_ssl_certificate" "ssl_cert" {
  name = "${var.service_name}-ssl-cert-managed"
  managed {
    domains = [var.app_domain]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_region_network_endpoint_group" "serverless_neg" {
  name                  = "${var.service_name}-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.app.name
  }
}

resource "google_compute_backend_service" "backend_service" {
  name                  = "${var.service_name}-backend"
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  enable_cdn            = false
  backend {
    group = google_compute_region_network_endpoint_group.serverless_neg.id
  }
}

resource "google_compute_url_map" "url_map" {
  name            = "${var.service_name}-url-map"
  default_service = google_compute_backend_service.backend_service.id
}

resource "google_compute_target_https_proxy" "https_proxy" {
  name             = "${var.service_name}-https-proxy"
  url_map          = google_compute_url_map.url_map.id
  ssl_certificates = [google_compute_managed_ssl_certificate.ssl_cert.id]
}

resource "google_compute_global_forwarding_rule" "https_forwarding_rule" {
  name                  = "${var.service_name}-https-rule"
  target                = google_compute_target_https_proxy.https_proxy.id
  ip_address            = google_compute_global_address.lb_ip.address
  port_range            = "443"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_url_map" "http_redirect" {
  name = "${var.service_name}-http-redirect"
  default_url_redirect {
    https_redirect         = true
    strip_query            = false
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
  }
}

resource "google_compute_target_http_proxy" "http_proxy" {
  name    = "${var.service_name}-http-proxy"
  url_map = google_compute_url_map.http_redirect.id
}

resource "google_compute_global_forwarding_rule" "http_forwarding_rule" {
  name                  = "${var.service_name}-http-rule"
  target                = google_compute_target_http_proxy.http_proxy.id
  ip_address            = google_compute_global_address.lb_ip.address
  port_range            = "80"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}


output "run_url" { value = google_cloud_run_v2_service.app.uri }
output "sql_connection" { value = google_sql_database_instance.pg.connection_name }
output "image_hint" { value = "${var.region}-docker.pkg.dev/${var.project_id}/challenge-repository/<name>:<tag>" }
output "load_balancer_ip" {
  value = google_compute_global_address.lb_ip.address
}
