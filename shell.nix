{ pkgs ? import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/nixos-24.05.tar.gz") {} }:

pkgs.mkShell {
  packages = with pkgs; [
    git
    kubectl
    pre-commit
    kustomize
    kubernetes-helm
    kind
    jq
    (python3.withPackages (p: with p; [ httpx pyyaml pytest prometheus-client ]))
  ];
}
