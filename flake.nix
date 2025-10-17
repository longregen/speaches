{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    nix-hug = {
      url = "github:longregen/nix-hug";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      nixpkgs,
      nix-hug,
      ...
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      perSystem =
        system:
        let
          mkOverlay = import ./nix/overlay.nix;

          mkSpeaches = import ./nix/package.nix {
            inherit nixpkgs system mkOverlay;
            src = nixpkgs.lib.cleanSource ./.;
          };

          cpuPkgs = import nixpkgs {
            inherit system;
            config = {
              allowUnfree = true;
              allowBroken = true;
              cudaSupport = false;
            };
            overlays = [
              (mkOverlay {
                pythonVersion = "python312";
                cudaSupport = false;
              })
            ];
          };

          defaultPkgs = import nixpkgs {
            inherit system;
            config = {
              allowUnfree = true;
              allowBroken = true;
              cudaSupport = true;
              cudaCapabilities = [
                "8.9"
                "12.0"
              ];
              cudaEnableForwardCompat = true;
            };
            overlays = [
              (mkOverlay {
                pythonVersion = "python312";
                cudaSupport = true;
              })
            ];
          };

          models = {
            kokoro-82m = nix-hug.lib.${system}.fetchModel {
              url = "speaches-ai/Kokoro-82M-v1.0-ONNX";
              rev = "dc196c76d64fed9203906231372bcb98135815df";
              fileTreeHash = "sha256-+Aea1c28vvS+pfOs2alshOajGzW6I7ujDVIIAQ5KlgI=";
            };
            silero-vad = nix-hug.lib.${system}.fetchModel {
              url = "onnx-community/silero-vad";
              rev = "e71cae966052b992a7eca6b17738916ce0eca4ec";
              fileTreeHash = "sha256-Ngj+Sq0vWS2MEPbOzpCoUe1iBORhDyaK2Eluq/RmUEs=";
            };
            whisper-base = nix-hug.lib.${system}.fetchModel {
              url = "Systran/faster-whisper-base";
              rev = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66";
              fileTreeHash = "sha256-GYgT6udNwSgjZabqajK/i8kL3pvRPbaTC2PQdUfH0EY=";
            };
          };

          speaches = mkSpeaches { };
          speaches-cpu = mkSpeaches { withCuda = false; };
          speaches-dev = mkSpeaches { withDev = true; };
          speaches-python313 = mkSpeaches { pythonVersion = "python313"; };
          speaches-python314 = mkSpeaches { pythonVersion = "python314"; };
          speaches-cpu-python313 = mkSpeaches {
            pythonVersion = "python313";
            withCuda = false;
          };
          speaches-cpu-python314 = mkSpeaches {
            pythonVersion = "python314";
            withCuda = false;
          };

          testModelCache = nix-hug.lib.${system}.buildCache {
            models = with models; [
              kokoro-82m
              silero-vad
              whisper-base
            ];
          };

          mkE2eTest = import ./nix/vm-test.nix {
            pkgs = defaultPkgs;
            inherit mkSpeaches testModelCache system;
          };
        in
        {
          devShells.default = import ./nix/devshell.nix {
            pkgs = defaultPkgs;
            inherit system;
          };

          packages = {
            default = speaches;
            inherit
              speaches
              speaches-cpu
              speaches-dev
              speaches-python313
              speaches-python314
              speaches-cpu-python313
              speaches-cpu-python314
              ;
            inherit (models)
              kokoro-82m
              silero-vad
              whisper-base
              ;
            e2e-test = import ./nix/e2e-test.nix {
              pkgs = cpuPkgs;
              speachesPackage = speaches-cpu;
              modelCache = testModelCache;
            };
            e2e-test-cuda = import ./nix/e2e-test.nix {
              pkgs = defaultPkgs;
              speachesPackage = speaches;
              modelCache = testModelCache;
            };
            e2e-test-python314 = import ./nix/e2e-test.nix {
              pkgs = cpuPkgs;
              speachesPackage = speaches-cpu-python314;
              modelCache = testModelCache;
            };
          };

          apps.default = {
            type = "app";
            program = "${speaches}/bin/speaches";
          };

          checks =
            let
              e2e-python312 = mkE2eTest { pythonVersion = "python312"; };
              e2e-python313 = mkE2eTest { pythonVersion = "python313"; };
              e2e-python314 = mkE2eTest { pythonVersion = "python314"; };
            in
            {
              inherit e2e-python312 e2e-python313 e2e-python314;
              e2e = e2e-python312;
            };

          formatter = defaultPkgs.nixfmt;
        };

      all = nixpkgs.lib.genAttrs systems perSystem;
    in
    {
      overlays.default = import ./nix/overlay.nix { };
      nixosModules.default = import ./nix/module.nix;
      devShells = nixpkgs.lib.mapAttrs (_: v: v.devShells) all;
      packages = nixpkgs.lib.mapAttrs (_: v: v.packages) all;
      apps = nixpkgs.lib.mapAttrs (_: v: v.apps) all;
      checks = nixpkgs.lib.mapAttrs (_: v: v.checks) all;
      formatter = nixpkgs.lib.mapAttrs (_: v: v.formatter) all;
    };
}
