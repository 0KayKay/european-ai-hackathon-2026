set default-list := true

# Start the local Kubernetes stack and Tilt dashboard.
up:
    tilt up

# Remove the stack while keeping the Postgres volume.
down:
    tilt down

# Show running resources.
status:
    kubectl get deployments,services,pvc -l app.kubernetes.io/part-of=hackathon

# Run the Tilt build and deployment check once.
check:
    tilt ci
