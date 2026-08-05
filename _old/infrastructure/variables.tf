variable "project_id" {
  type    = string
  default = "prompt-injection-challenge"
}
variable "project_number" {
  type    = string
  default = "275067861523"
}
variable "region" {
  type    = string
  default = "europe-west1"
}
variable "app_domain" {
  type    = string
  default = "challenge.aixchange-conference.de"
}
variable "allow_localhost" {
  type    = bool
  default = true
}

variable "service_name" {
  type    = string
  default = "aixchange-challenge"
}

variable "image" {
  type    = string
  default = "europe-west1-docker.pkg.dev/prompt-injection-challenge/challenge-repository/application:latest"
}

variable "db_instance_name" {
  type    = string
  default = "instance"
}
variable "db_version" {
  type    = string
  default = "POSTGRES_16"
}
variable "db_tier" {
  type    = string
  default = "db-perf-optimized-N-2"
}
variable "db_name" {
  type    = string
  default = "challenge_db"
}
variable "db_user" {
  type    = string
  default = "challenge_db_user"
}
