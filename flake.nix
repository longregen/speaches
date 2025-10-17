{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    nix-hug = {
      url = "github:longregen/nix-hug";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-utils.follows = "flake-utils";
    };
  };
  outputs =
    {
      nixpkgs,
      flake-utils,
      nix-hug,
      ...
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

        linuxOnlyPkgs =
          with pkgs;
          if system == "x86_64-linux" then
            [
              cudaPackages_12.cudnn
              cudaPackages_12.libcublas
              cudaPackages_12.libcurand
              cudaPackages_12.libcufft
              cudaPackages_12.cuda_cudart
              cudaPackages_12.cuda_nvrtc
            ]
          else
            [ ];

        # https://github.com/nixos/nixpkgs/issues/278976#issuecomment-1879685177
        # NOTE: Without adding `/run/...` the following error occurs
        # RuntimeError: CUDA failed with error CUDA driver version is insufficient for CUDA runtime version
        #
        # NOTE: sometimes it still doesn't work but rebooting the system fixes it
        # TODO: check if `LD_LIBRARY_PATH` needs to be set on MacOS
        linuxLibPath =
          if system == "x86_64-linux" then
            "/run/opengl-driver/lib:${
              pkgs.lib.makeLibraryPath [
                # Needed for `faster-whisper`
                pkgs.cudaPackages_12.cudnn
                pkgs.cudaPackages_12.libcublas

                # The 4 cuda packages below are needed for `onnxruntime-gpu`
                pkgs.cudaPackages_12.libcurand
                pkgs.cudaPackages_12.libcufft
                pkgs.cudaPackages_12.cuda_cudart
                pkgs.cudaPackages_12.cuda_nvrtc

                # Needed for `soundfile`
                pkgs.portaudio

                pkgs.zlib
                pkgs.stdenv.cc.cc
                pkgs.openssl
              ]
            }"
          else
            "";

        # --- Build infrastructure for e2e testing ---

        mkOverlay = {
          pythonVersion,
          cudaSupport ? true,
        }: final: prev: let
          pyPackages = prev."${pythonVersion}Packages";
          customDeps = import ./nix/dependencies.nix {
            pkgs = final;
            inherit pyPackages system;
          };
        in {
          ctranslate2 =
            if cudaSupport && system == "x86_64-linux"
            then
              prev.ctranslate2.override {
                stdenv = prev.gcc14Stdenv;
                withCUDA = true;
                withCuDNN = true;
                cudaPackages = prev.cudaPackages_12;
              }
            else prev.ctranslate2;

          silero-encoder-v5 = prev.fetchurl {
            url = "https://github.com/SYSTRAN/faster-whisper/raw/v1.1.0/faster_whisper/assets/silero_encoder_v5.onnx";
            hash = "sha256-Dp/I9WQHaT0oP5kEX7lcK5D9yjQzzl+D4sEh+05hUHU=";
          };
          silero-decoder-v5 = prev.fetchurl {
            url = "https://github.com/SYSTRAN/faster-whisper/raw/v1.1.0/faster_whisper/assets/silero_decoder_v5.onnx";
            hash = "sha256-jCA0T1CYRqB8zYWCfohXAX+uZ/q9WGib7Br3nh1Igwc=";
          };

          "${pythonVersion}Packages" =
            pyPackages
            // {
              faster-whisper = pyPackages.faster-whisper.overrideAttrs (old: {
                propagatedBuildInputs = old.propagatedBuildInputs ++ [final.ctranslate2];
                postInstall =
                  (old.postInstall or "")
                  + ''
                    assets_dir="$out/${pyPackages.python.sitePackages}/faster_whisper/assets"
                    mkdir -p "$assets_dir"
                    if [ ! -f "$assets_dir/silero_encoder_v5.onnx" ]; then
                      cp ${final.silero-encoder-v5} "$assets_dir/silero_encoder_v5.onnx"
                      cp ${final.silero-decoder-v5} "$assets_dir/silero_decoder_v5.onnx"
                    fi
                  '';
              });
            }
            // customDeps;
        };

        mkSpeaches = {
          pythonVersion ? "python312",
          withCuda ? (system == "x86_64-linux"),
        }: let
          overlay = mkOverlay {
            inherit pythonVersion;
            cudaSupport = withCuda;
          };

          buildPkgs = import nixpkgs {
            inherit system;
            config.allowUnfree = true;
            overlays = [overlay];
          };

          python = buildPkgs.${pythonVersion};
          pythonPackages = buildPkgs."${pythonVersion}Packages";

          pythonEnv = python.withPackages (
            ps: let
              coreDeps =
                [
                  ps.fastapi
                  ps.huggingface-hub
                  ps.numpy
                  ps.pydantic
                  ps.pydantic-settings
                  ps.python-multipart
                  ps.sounddevice
                  ps.soundfile
                  ps.uvicorn
                  ps.openai
                  ps.aiostream
                  ps.cachetools
                  ps.gradio
                  ps.httpx
                  ps.httpx-sse
                  ps.httpx-ws
                  pythonPackages.faster-whisper
                ]
                ++ buildPkgs.lib.optionals (pythonPackages ? kokoro_onnx) [
                  pythonPackages.kokoro_onnx
                  pythonPackages.aiortc
                  pythonPackages.onnx_asr
                  pythonPackages.onnx_diarization
                ];

              piperDeps = buildPkgs.lib.optionals (pythonPackages.piper_tts != null) [
                pythonPackages.piper_tts
                pythonPackages.piper_phonemize
              ];

              otelDeps = [
                ps.opentelemetry-api
                ps.opentelemetry-sdk
                ps.opentelemetry-exporter-otlp
                ps.opentelemetry-instrumentation
                ps.opentelemetry-instrumentation-asgi
                ps.opentelemetry-instrumentation-fastapi
                ps.opentelemetry-instrumentation-httpx
                ps.opentelemetry-instrumentation-logging
                ps.opentelemetry-instrumentation-grpc
                pythonPackages.opentelemetry_instrumentation_openai
                pythonPackages.opentelemetry_instrumentation_openai_v2
              ];
            in
              coreDeps ++ piperDeps ++ otelDeps
          );
        in
          buildPkgs.stdenv.mkDerivation rec {
            pname = "speaches";
            version = "0.1.0";

            src = buildPkgs.lib.cleanSource ./.;

            nativeBuildInputs = [buildPkgs.makeWrapper];

            buildInputs =
              [
                pythonEnv
                buildPkgs.ffmpeg-full
                buildPkgs.portaudio
                buildPkgs.openssl
                buildPkgs.zlib
                buildPkgs.stdenv.cc.cc
                buildPkgs.ctranslate2
                buildPkgs.espeak-ng
              ]
              ++ buildPkgs.lib.optionals withCuda (
                with buildPkgs; [
                  cudaPackages_12.cudnn
                  cudaPackages_12.libcublas
                  cudaPackages_12.libcurand
                  cudaPackages_12.libcufft
                  cudaPackages_12.cuda_cudart
                  cudaPackages_12.cuda_nvrtc
                ]
              );

            installPhase = ''
              mkdir -p $out/share/speaches
              cp -r src pyproject.toml model_aliases.json $out/share/speaches/

              mkdir -p $out/share/speaches/realtime-console
              cp -r realtime-console/dist $out/share/speaches/realtime-console/

              mkdir -p $out/bin
              makeWrapper ${pythonEnv}/bin/python $out/bin/speaches \
                --prefix PATH : ${
                buildPkgs.lib.makeBinPath [
                  buildPkgs.ffmpeg-full
                  buildPkgs.espeak-ng
                ]
              } \
                --prefix LD_LIBRARY_PATH : ${buildPkgs.lib.makeLibraryPath [buildPkgs.espeak-ng]} \
                ${buildPkgs.lib.optionalString withCuda "--prefix LD_LIBRARY_PATH : /run/opengl-driver/lib:${buildPkgs.lib.makeLibraryPath buildInputs}"} \
                --set PYTHONPATH "$out/share/speaches/src" \
                --chdir "$out/share/speaches" \
                --add-flags "-m uvicorn" \
                --add-flags "--factory speaches.main:create_app" \
                --add-flags "--host \''${UVICORN_HOST:-0.0.0.0}" \
                --add-flags "--port \''${UVICORN_PORT:-8000}"
            '';

            meta = with buildPkgs.lib; {
              description = "AI-powered speech processing application";
              homepage = "https://github.com/speaches-ai/speaches";
              license = licenses.mit;
              platforms = platforms.unix;
            };
          };

        models = {
          kokoro-82m = nix-hug.lib.${system}.fetchModel {
            url = "speaches-ai/Kokoro-82M-v1.0-ONNX";
            rev = "main";
            repoInfoHash = "sha256-+eumCsNLTigie1h/syJwzPnF2KR7BAgHvJnmBRQYa20=";
            fileTreeHash = "sha256-+Aea1c28vvS+pfOs2alshOajGzW6I7ujDVIIAQ5KlgI=";
            derivationHash = "sha256-v2BsX7lfzzytuLSTEpJccHHAyG09dzvTsF9pXYBSZOs=";
          };

          silero-vad = nix-hug.lib.${system}.fetchModel {
            url = "onnx-community/silero-vad";
            rev = "main";
            repoInfoHash = "sha256-SHdAJJDNbncunz7YtU3yMTvWzWSaCFcr5YQhqIbK+gA=";
            fileTreeHash = "sha256-f+/9fy13zID9i5mv7FwdwCs0oQskWJlJ7TK3VjOVI4A=";
            derivationHash = "sha256-Lvy4rwZy1Z0IEASdeeJ8VV9MyXjReez7sciPF93ezVQ=";
          };

          whisper-base = nix-hug.lib.${system}.fetchModel {
            url = "Systran/faster-whisper-base";
            rev = "main";
            repoInfoHash = "sha256-ymFb3i5MpvWupimI8Z14143NtRqxsJp5IJ0AuV+9h8Y=";
            fileTreeHash = "sha256-GYgT6udNwSgjZabqajK/i8kL3pvRPbaTC2PQdUfH0EY=";
            derivationHash = "sha256-GM5YAkx4yhNHLvXWNDyk9UqemYzE/CiEX43NU2u+3Hw=";
          };
        };

        speaches-cpu = mkSpeaches { withCuda = false; };

        testModelCache = nix-hug.lib.${system}.buildCache {
          models = [
            models.kokoro-82m
            models.silero-vad
            models.whisper-base
          ];
          hash = "sha256-EXTbMnwEWkDiYcNjbS3wPYB3jfDK0pZxxjsPseGLW18=";
        };

        mkE2eTest = {
          pythonVersion ? "python312",
          fullTest ? true,
        }: let
          testPackage = mkSpeaches {
            inherit pythonVersion;
            withCuda = false;
          };
        in
          pkgs.testers.nixosTest {
            name = "speaches-e2e-test-${pythonVersion}";
            enableOCR = false;

            nodes.machine = {
              config,
              pkgs,
              ...
            }: {
              imports = [./nix/module.nix];

              environment.variables = {
                HF_HUB_CACHE = "${testModelCache}/hub";
                HF_HUB_OFFLINE = "1";
              };

              services.speaches = {
                enable = true;
                package = testPackage;
                host = "127.0.0.1";
                port = 18000;
                environment = {
                  SPEACHES_WHISPER_MODEL = "Systran/faster-whisper-base";
                  HF_HUB_CACHE = "${testModelCache}/hub";
                  HF_HUB_OFFLINE = "1";
                };
              };

              environment.systemPackages = with pkgs;
                [
                  curl
                  jq
                  file
                ]
                ++ (
                  if fullTest
                  then [
                    sox
                    ffmpeg-full
                  ]
                  else []
                );

              virtualisation = {
                memorySize = 4096;
                cores = 2;
              };
            };

            testScript = import ./nix/e2e-test.nix { inherit pythonVersion fullTest; };
          };

      in
      {
        devShells.default = pkgs.mkShell {
          nativeBuildInputs =
            with pkgs;
            [
              act
              docker
              docker-compose
              ffmpeg-full
              go-task
              grafana-loki
              parallel
              pv
              python312
              tempo
              uv
              websocat
            ]
            ++ linuxOnlyPkgs;

          LD_LIBRARY_PATH = linuxLibPath;

          shellHook = ''
            source .venv/bin/activate
            source .env
          '';
        };

        packages = {
          default = mkSpeaches {};
          inherit speaches-cpu;
          inherit (models) kokoro-82m silero-vad whisper-base;
          model-cache = testModelCache;
        };

        checks.e2e = mkE2eTest {};

        formatter = pkgs.nixfmt;
      }
    );
}
