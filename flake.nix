{
  description = "Mira development environment";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = {nixpkgs, ...}: let
    supportedSystems = ["aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux"];
    forEachSystem = function:
      nixpkgs.lib.genAttrs supportedSystems (system: function nixpkgs.legacyPackages.${system});
  in {
    devShells = forEachSystem (pkgs: {
      default = pkgs.mkShell {
        packages = with pkgs; [
          nodejs_22
          python312
          uv
        ];

        UV_PYTHON = "${pkgs.python312}/bin/python";
        UV_FROZEN = "1";

        shellHook = ''
          uv sync --frozen --all-extras --quiet
        '';
      };
    });
  };
}
