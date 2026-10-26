#!/bin/bash
# First look at NVIDIA's Curiosity v2 cluster from its login node: Slurm partitions, QoS, accounts,
# GPU node layout, modules, container tools and storage. Read-only; it runs Slurm and module queries
# only, as the cluster policy asks for login nodes. Takes a few seconds. By hand, after scp:
#   bash curiosity_login.sh 2>&1 | tee curiosity-login.txt
# Then submit curiosity_node.sbatch with the account and partition this prints.
set -uo pipefail

section() { printf '\n===== %s =====\n' "$1"; }

section "identity"
echo "user: $(id -un)  groups: $(id -Gn)"
echo "home: $HOME"
grep PRETTY_NAME /etc/os-release
echo "python3: $(python3 --version 2>&1)  ($(command -v python3))"

section "Slurm partitions (name, availability, time limit, nodes, GRES, CPUs, memory MB, nodes)"
sinfo -o '%P|%a|%l|%D|%G|%c|%m|%N'
section "partition details (exclusive nodes? oversubscribe? default QoS?)"
scontrol show partition | grep -E 'PartitionName|AllowAccounts|AllowQos|QoS=|OverSubscribe|ExclusiveUser|DefaultTime|MaxTime|MaxNodes|TRES='
section "my associations (account, partition, QoS): use these for init --account/--partition/--qos"
sacctmgr -nP show assoc user="$(id -un)" format=account,partition,qos,defaultqos,grptres,maxtres,maxjobs
section "QoS limits"
sacctmgr -nP show qos format=name,priority,maxwall,maxtrespu,maxjobspu,maxsubmitpu
section "one GPU node"
NODE=$(sinfo -h -N -o '%N %G' | awk '$2 ~ /gpu/ {print $1; exit}')
if [[ -n "$NODE" ]]; then
  scontrol show node "$NODE" | grep -E 'NodeName|CPUTot|RealMemory|Gres=|CfgTRES|AvailableFeatures|State|OS='
else
  echo "no node with gpu GRES found in sinfo"
fi
section "queue right now"
squeue -o '%.10i %.9P %.20j %.10u %.8T %.10M %.10l %.5D %R' | head -40
echo "jobs in queue: $(squeue -h | wc -l)"
section "Slurm config that matters for us"
scontrol show config | grep -E 'SLURM_VERSION|SelectType |SelectTypeParameters|GresTypes|ProctrackType|TaskPlugin|PrologFlags|JobContainerType|MpiDefault|PlugStackConfig|MaxJobCount'
srun --help 2>&1 | grep -E -- '--container-image|--container-mounts' | head -2 || true
echo "(container-* options above mean Pyxis is installed: srun --container-image=...)"

section "modules"
module -t avail 2>&1 | sort | uniq

section "container tools on PATH (login node)"
for tool in apptainer singularity enroot docker podman; do
  printf '%-12s %s\n' "$tool" "$(command -v "$tool" || echo -)"
done

section "storage"
df -hT "$HOME" 2>/dev/null
df -hT 2>/dev/null | grep -Ei 'nfs|lustre|gpfs|beegfs|wekafs|raid' || true
(quota -s 2>/dev/null || true) | head -20
echo "candidate team folders (the slides promise 300G per team):"
ls -ld /shared /shared/* /projects /projects/* /data /data/* /lustre /raid 2>/dev/null | head -20
