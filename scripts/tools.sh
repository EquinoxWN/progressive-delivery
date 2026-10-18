#!/usr/bin/env bash
# Pinned tools into .tmp/bin, each checked against its published SHA-256.
#   scripts/tools.sh kubeconform        (Linux or Windows/Git Bash)
#   scripts/tools.sh kind kubectl helm  (Linux, for the end-to-end run)
set -euo pipefail
cd "$(dirname "$0")/.."
BIN=.tmp/bin
mkdir -p "$BIN" .tmp/downloads

KUBECONFORM=v0.8.0
KIND=v0.33.0
KUBECTL=v1.36.4
HELM=v4.3.0

# get downloads url to file once and checks it.
get() {
  local url=$1 out=$2 sha=$3
  [ -f "$out" ] || curl -fsSL --proto '=https' --retry 3 -o "$out" "$url"
  echo "$sha  $out" | sha256sum -c - >/dev/null || { echo "checksum mismatch: $out" >&2; rm -f "$out"; exit 1; }
}

for tool in "$@"; do
  case "$tool:$(uname -s)" in
    kubeconform:Linux)
      get "https://github.com/yannh/kubeconform/releases/download/$KUBECONFORM/kubeconform-linux-amd64.tar.gz" \
        .tmp/downloads/kubeconform.tar.gz 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
      tar -xzf .tmp/downloads/kubeconform.tar.gz -C "$BIN" kubeconform ;;
    kubeconform:MINGW*|kubeconform:MSYS*)
      get "https://github.com/yannh/kubeconform/releases/download/$KUBECONFORM/kubeconform-windows-amd64.zip" \
        .tmp/downloads/kubeconform.zip e3f56102bcf4f50b034a567e2482a1c5330799983ddd655952310211aef73d93
      unzip -o -q .tmp/downloads/kubeconform.zip kubeconform.exe -d "$BIN" ;;
    kind:Linux)
      get "https://github.com/kubernetes-sigs/kind/releases/download/$KIND/kind-linux-amd64" \
        "$BIN/kind" aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
      chmod +x "$BIN/kind" ;;
    kubectl:Linux)
      get "https://dl.k8s.io/release/$KUBECTL/bin/linux/amd64/kubectl" \
        "$BIN/kubectl" 8b8f088da2dab964f853b38464033b1be15ede2839eca751482357c45abdd05a
      chmod +x "$BIN/kubectl" ;;
    helm:Linux)
      get "https://get.helm.sh/helm-$HELM-linux-amd64.tar.gz" \
        .tmp/downloads/helm.tar.gz 86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb
      tar -xzf .tmp/downloads/helm.tar.gz -C "$BIN" --strip-components=1 linux-amd64/helm ;;
    *) echo "unsupported: $tool on $(uname -s)" >&2; exit 2 ;;
  esac
done
