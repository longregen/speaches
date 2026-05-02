{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/b12141ef619e0a9c1c84dc8c684040326f27cdcc";
    nix-hug = {
      url = "github:longregen/nix-hug";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
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
      forEachSystem = nixpkgs.lib.genAttrs systems;

      perSystem =
        system:
        let
          isLinux = (system == "x86_64-linux" || system == "aarch64-linux");

          # Local CUDA config override -- defaults to {} upstream.
          # Edit nix/local-cuda-config.nix with your cudaCapabilities, then run:
          #   git update-index --assume-unchanged nix/local-cuda-config.nix
          localCudaConfig = import ./nix/local-cuda-config.nix;

          cudaDepsFor =
            pkgs: with pkgs.cudaPackages_12; [
              cudnn
              libcublas
              libcurand
              libcufft
              cuda_cudart
              cuda_nvrtc
            ];

          mkPythonEnv =
            {
              python,
              customDeps,
              lib,
              withDev ? false,
              withDocs ? false,
            }:
            python.withPackages (
              ps:
              [
                ps.fastapi
                ps.huggingface-hub
                ps.numpy
                ps.pydantic
                ps.pydantic-settings
                ps.python-multipart
                ps.soundfile
                ps.uvicorn
                ps.openai
                ps.aiostream
                ps.cachetools
                ps.gradio
                ps.httpx
                ps.httpx-sse
                ps.httpx-ws
                ps.faster-whisper
                ps.opentelemetry-api
                ps.opentelemetry-sdk
                ps.opentelemetry-exporter-otlp
                ps.opentelemetry-instrumentation
                ps.opentelemetry-instrumentation-asgi
                ps.opentelemetry-instrumentation-fastapi
                ps.opentelemetry-instrumentation-logging
                ps.opentelemetry-instrumentation-grpc
                customDeps.opentelemetry_instrumentation_asyncio
                customDeps.opentelemetry_instrumentation_httpx
              ]
              # pyannote-audio's dep chain (speechbrain -> moto -> cfn-lint) uses pydantic v1
              # which crashes at import on python 3.14+.
              ++ lib.optionals (!(ps.python.pythonAtLeast "3.14")) [ ps.pyannote-audio ]
              ++ lib.optionals (customDeps ? kokoro_onnx) [
                customDeps.kokoro_onnx
                customDeps.aiortc
                customDeps.onnx_asr
                customDeps.onnx_diarization
              ]
              ++ lib.optionals (customDeps ? kokoro_pytorch) [ customDeps.kokoro_pytorch ]
              ++ lib.optionals (customDeps ? pocket_tts) [ customDeps.pocket_tts ]
              ++ lib.optionals (customDeps ? qwen_tts) [ customDeps.qwen_tts ]
              ++ lib.optionals (customDeps.piper_tts != null) [ customDeps.piper_tts ]
              ++ lib.optionals withDev [
                ps.pyperf
                ps.anyio
                ps.pytest-asyncio
                ps.pytest
                ps.pytest-mock
                ps.ruff
                ps.srt
                customDeps.webvtt_py
                customDeps.pytest_antilru
              ]
              ++ lib.optionals withDocs [
                ps.mdx-truly-sane-lists
                ps.mkdocs
                ps.mkdocs-material
                ps.mkdocstrings
                ps.mkdocstrings-python
                customDeps.mkdocs_render_swagger_plugin
              ]
            );

          mkOverlay =
            {
              pythonVersion,
              cudaSupport ? true,
            }:
            final: prev:
            let
              isCuda = cudaSupport && isLinux;
              targetPyVersion = prev.${pythonVersion}.pythonVersion;
            in
            {
              # Override ctranslate2 for CUDA support
              ctranslate2 =
                if isCuda then
                  prev.ctranslate2.override {
                    stdenv = prev.gcc15Stdenv;
                    withCUDA = true;
                    withCuDNN = true;
                    cudaPackages = prev.cudaPackages_12;
                  }
                else
                  prev.ctranslate2;

              # Silero VAD assets (bundled with faster-whisper source but may be missing in Nix build)
              silero-encoder-v5 = prev.fetchurl {
                url = "https://github.com/SYSTRAN/faster-whisper/raw/v1.1.0/faster_whisper/assets/silero_encoder_v5.onnx";
                hash = "sha256-Dp/I9WQHaT0oP5kEX7lcK5D9yjQzzl+D4sEh+05hUHU=";
              };
              silero-decoder-v5 = prev.fetchurl {
                url = "https://github.com/SYSTRAN/faster-whisper/raw/v1.1.0/faster_whisper/assets/silero_decoder_v5.onnx";
                hash = "sha256-jCA0T1CYRqB8zYWCfohXAX+uZ/q9WGib7Br3nh1Igwc=";
              };
              silero-vad-v6 = prev.fetchurl {
                url = "https://github.com/SYSTRAN/faster-whisper/raw/v1.2.1/faster_whisper/assets/silero_vad_v6.onnx";
                hash = "sha256-TL9Um4Mm9g+A8lNtnu/rRQqavoM2WgmAMciXGfG+F9I=";
              };

              # kvazaar (HEVC encoder pulled in by ffmpeg-full) ships ctest cases that get
              # SIGKILLed in the macOS sandbox. Speaches doesn't encode HEVC; tests are noise.
              kvazaar = prev.kvazaar.overrideAttrs (
                prev.lib.optionalAttrs prev.stdenv.isDarwin { doCheck = false; }
              );

              # chromaprint (audio fingerprinting, ffmpeg-full transitive dep) tests get
              # SIGKILLed in the macOS sandbox. Not used by speaches at runtime.
              chromaprint = prev.chromaprint.overrideAttrs (
                prev.lib.optionalAttrs prev.stdenv.isDarwin { doCheck = false; }
              );

              # ffmpeg-{headless,full} on darwin: nixpkgs' generic.nix doesn't pull in
              # autoSignDarwinBinariesHook, and stdenv's strip runs *after* the linker's
              # adhoc signature, leaving every libav*/libsw*.dylib with `tainted:1`. macOS
              # arm64 then SIGKILLs anything that loads them (e.g. python importing PyAV).
              # Re-sign every dylib in $lib/lib so the page hashes match the stripped bytes.
              ffmpeg-headless = prev.ffmpeg-headless.overrideAttrs (
                old:
                prev.lib.optionalAttrs prev.stdenv.isDarwin {
                  postFixup =
                    (old.postFixup or "")
                    + ''
                      for dylib in $lib/lib/*.dylib; do
                        [ -L "$dylib" ] && continue
                        ${prev.darwin.sigtool}/bin/codesign -f -s - "$dylib" || true
                      done
                    '';
                }
              );
              ffmpeg-full = prev.ffmpeg-full.overrideAttrs (
                old:
                prev.lib.optionalAttrs prev.stdenv.isDarwin {
                  postFixup =
                    (old.postFixup or "")
                    + ''
                      for dylib in $lib/lib/*.dylib; do
                        [ -L "$dylib" ] && continue
                        ${prev.darwin.sigtool}/bin/codesign -f -s - "$dylib" || true
                      done
                    '';
                }
              );


              # Override onnxruntime in the python fixed-point for the target Python version only
              pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
                (
                  pyFinal: pyPrev:
                  let
                    inherit (prev) lib;
                    pyVer = pyPrev.python.pythonVersion;
                    isDarwin = prev.stdenv.isDarwin;
                    isTarget = pyVer == targetPyVersion;
                    disableTests = name: tests: {
                      ${name} = pyPrev.${name}.overridePythonAttrs (old: {
                        disabledTests = (old.disabledTests or [ ]) ++ tests;
                      });
                    };
                    disableTestPaths = name: paths: {
                      ${name} = pyPrev.${name}.overridePythonAttrs (old: {
                        disabledTestPaths = (old.disabledTestPaths or [ ]) ++ paths;
                      });
                    };
                    noCheck = name: { ${name} = pyPrev.${name}.overridePythonAttrs { doCheck = false; }; };
                  in
                  lib.optionalAttrs (isCuda && isTarget) {
                    onnxruntime = pyPrev.onnxruntime.override {
                      onnxruntime = prev.onnxruntime.override {
                        cudaSupport = true;
                        cudaPackages = prev.cudaPackages_12;
                        python3Packages = pyPrev;
                        pythonSupport = true;
                      };
                    };
                  }
                  // lib.optionalAttrs isTarget {
                    faster-whisper = pyPrev.faster-whisper.overrideAttrs (old: {
                      propagatedBuildInputs = old.propagatedBuildInputs ++ [ final.ctranslate2 ];
                      postInstall = (old.postInstall or "") + ''
                        assets_dir="$out/${pyPrev.python.sitePackages}/faster_whisper/assets"
                        mkdir -p "$assets_dir"
                        if [ ! -f "$assets_dir/silero_encoder_v5.onnx" ]; then
                          cp ${final.silero-encoder-v5} "$assets_dir/silero_encoder_v5.onnx"
                          cp ${final.silero-decoder-v5} "$assets_dir/silero_decoder_v5.onnx"
                        fi
                        if [ ! -f "$assets_dir/silero_vad_v6.onnx" ]; then
                          cp ${final.silero-vad-v6} "$assets_dir/silero_vad_v6.onnx"
                        fi
                      '';
                    });
                  }
                  # Darwin-only: tests that fail in the macOS sandbox (socket/network restrictions).
                  # Each target is in the speaches build-time closure (test deps of gradio etc.);
                  # entries pruned were ones whose target package never gets built.
                  // lib.optionalAttrs isDarwin (
                    disableTestPaths "geoip2" [ "tests/webservice_test.py" ]
                    // disableTests "scipy" [ "TestDatasets" ]
                    // disableTests "opentelemetry-exporter-otlp-proto-grpc" [
                      "test_permanent_failure"
                      "test_shutdown"
                      "test_shutdown_wait_last_export"
                      "test_success"
                      "test_unavailable_delay"
                    ]
                    // disableTests "opentelemetry-instrumentation-requests" [ "TestURLLib3InstrumentorWithRealSocket" ]
                    # Twisted uses trial (not pytest), so disabledTests has no effect.
                    // noCheck "twisted"
                    // disableTestPaths "aiohttp" [ "tests/test_connector.py" ]
                    # requests-futures' tests bind localhost sockets which are blocked
                    # in the macOS sandbox.
                    // noCheck "requests-futures"
                    # audioread checkPhase tries to open audio backends (gst/coreaudio)
                    # that aren't available in the macOS sandbox.
                    // noCheck "audioread"
                    # sentry-sdk tracing tests fail in sandbox (timing/profiler-dependent).
                    // disableTestPaths "sentry-sdk" [ "tests/tracing/test_span_streaming.py" ]
                  )
                  # Runtime fixes for all platforms. optuna/wandb tests fail in sandbox
                  # (network/filesystem checks); both are gradio transitive deps.
                  # jupyter-server has flaky tornado timeout tests in sandbox.
                  // noCheck "optuna"
                  // noCheck "jupyter-server"
                  # wandb's let-bound parquet-rust-wrapper crate runs http_file_reader tests
                  # against an httpbin shim that fails in sandbox. Skip checks for the rust
                  # subcrates by replacing rustPlatform with a no-check variant.
                  // {
                    wandb =
                      let
                        noCheckRust = prev.rustPlatform // {
                          buildRustPackage =
                            args: prev.rustPlatform.buildRustPackage (args // { doCheck = false; });
                        };
                      in
                      (pyPrev.wandb.override { rustPlatform = noCheckRust; }).overridePythonAttrs {
                        doCheck = false;
                      };
                  }
                  # gradio: test_pipelines.py collection crashes on Linux.
                  # Re-expose `override` passthrough (needed by gradio's own sans-reverse-deps variant).
                  # gradio-client: tests pull in `gradio.passthru.sans-reverse-dependencies`,
                  # forcing a near-duplicate full gradio build. Skipping tests removes that
                  # branch from the closure entirely.
                  // {
                    gradio =
                      let
                        base = pyPrev.gradio.overridePythonAttrs {
                          doCheck = false;
                          dontCheckRuntimeDeps = true;
                        };
                      in
                      base // { inherit (pyPrev.gradio) override; };
                    gradio-client = pyPrev.gradio-client.overridePythonAttrs {
                      doCheck = false;
                      doInstallCheck = false;
                      dontCheckRuntimeDeps = true;
                    };
                    # hyperpyyaml: dontCheckRuntimeDeps because the package's runtime-deps
                    # walker dislikes one of its propagated inputs. (`meta.broken = false`
                    # was needed earlier; nixpkgs unmarked it.)
                    hyperpyyaml = pyPrev.hyperpyyaml.overridePythonAttrs {
                      dontCheckRuntimeDeps = true;
                    };
                    pyannote-audio = pyPrev.pyannote-audio.overridePythonAttrs (old: {
                      dontCheckRuntimeDeps = true;
                      disabledTestPaths = (old.disabledTestPaths or [ ]) ++ [ "tests/test_train.py" ];
                    });
                    pyannote-pipeline = pyPrev.pyannote-pipeline.overridePythonAttrs { dontCheckRuntimeDeps = true; };
                    # speechbrain ships a top-level docs/ dir into site-packages that
                    # collides with cryptography's docs/conf.py when buildEnv merges them.
                    speechbrain = pyPrev.speechbrain.overridePythonAttrs (old: {
                      dontCheckRuntimeDeps = true;
                      postInstall = (old.postInstall or "") + ''
                        rm -rf "$out/${pyPrev.python.sitePackages}/docs"
                      '';
                    });
                  }
                  # Disabled upstream for py3.14; re-enable without tests on darwin.
                  // builtins.listToAttrs (
                    map
                      (n: {
                        name = n;
                        value = pyPrev.${n}.overridePythonAttrs {
                          disabled = false;
                          doCheck = !isDarwin;
                        };
                      })
                      [
                        "aws-sam-translator"
                        "cfn-lint"
                      ]
                  )
                  # Darwin-only test disables for packages in the build-time closure.
                  # jupyter-server entry pruned: shadowed by the cross-platform `noCheck "jupyter-server"`.
                  // lib.optionalAttrs isDarwin (
                    disableTests "python-ulid" [ "test_same_millisecond_overflow" ]
                    # django uses unittest + runtests.py, not pytest, so disabledTests has
                    # no effect. test_crafted_xml_performance is timing-based and flakes
                    # on darwin under sandbox load.
                    // noCheck "django"
                    // disableTests "pyarrow" [
                      "test_batch_lifetime"
                      "test_timezone_absent"
                    ]
                  )
                )
              ];

            };

          mkSpeaches =
            {
              pythonVersion ? "python312",
              withCuda ? isLinux,
              withCoreML ? false,
              withDev ? false,
              # Pass the caller's pkgs to share dependency builds (torch, numpy, etc.)
              # instead of creating a separate nixpkgs evaluation.
              # The speaches overlay is applied internally via .extend -- do not pre-apply it.
              basePkgs ? null,
              extraNixpkgsConfig ? { },
              extraOverlays ? [ ],
            }:
            assert basePkgs == null || extraNixpkgsConfig == { };
            let
              overlay = mkOverlay {
                inherit pythonVersion;
                cudaSupport = withCuda;
              };

              speachesOverlays = [
                overlay
              ]
              ++ nixpkgs.lib.optionals withCoreML [
                (import ./nix/onnxruntime-coreml.nix)
              ]
              ++ extraOverlays;

              pkgs =
                if basePkgs != null then
                  basePkgs.extend (nixpkgs.lib.composeManyExtensions speachesOverlays)
                else
                  import nixpkgs {
                    inherit system;
                    config = {
                      allowUnfree = true;
                    }
                    // extraNixpkgsConfig;
                    overlays = speachesOverlays;
                  };

              python = pkgs.${pythonVersion};

              # Import custom deps using the fixed-point python packages (includes CUDA onnxruntime)
              customDeps = import ./nix/dependencies.nix {
                inherit pkgs system;
                pyPackages = python.pkgs;
              };

              pythonEnv = mkPythonEnv {
                inherit
                  python
                  customDeps
                  withDev
                  ;
                inherit (pkgs) lib;
              };
            in
            pkgs.stdenv.mkDerivation rec {
              pname = "speaches";
              version = "0.1.0";

              src = pkgs.lib.cleanSourceWith {
                src = ./.;
                filter =
                  path: type:
                  let
                    relPath = pkgs.lib.removePrefix (toString ./. + "/") (toString path);
                    allowedPrefixes = [
                      "src"
                      "realtime-console/dist"
                    ];
                    allowedFiles = [
                      "pyproject.toml"
                      "model_aliases.json"
                    ];
                    # For directories, also check if any allowedPrefix starts with this path
                    # (so parent dirs like "realtime-console" pass when "realtime-console/dist" is allowed)
                    isParentOfAllowed =
                      type == "directory"
                      && builtins.any (prefix: pkgs.lib.hasPrefix (relPath + "/") prefix) allowedPrefixes;
                  in
                  builtins.any (prefix: pkgs.lib.hasPrefix prefix relPath) allowedPrefixes
                  || builtins.elem relPath allowedFiles
                  || isParentOfAllowed;
              };

              nativeBuildInputs = [ pkgs.makeWrapper ] ++ pkgs.lib.optionals withDev [ pkgs.basedpyright ];

              buildInputs = [
                pythonEnv
                pkgs.ffmpeg-full
                pkgs.portaudio
                pkgs.openssl
                pkgs.zlib
                pkgs.stdenv.cc.cc
                pkgs.ctranslate2
                pkgs.espeak-ng
              ]
              ++ pkgs.lib.optionals withCuda (cudaDepsFor pkgs);

              installPhase = ''
                mkdir -p $out/share/speaches
                cp -r src pyproject.toml model_aliases.json $out/share/speaches/

                # Copy the realtime console UI
                mkdir -p $out/share/speaches/realtime-console
                cp -r realtime-console/dist $out/share/speaches/realtime-console/

                mkdir -p $out/bin
                makeWrapper ${pythonEnv}/bin/python $out/bin/speaches \
                  --prefix PATH : ${
                    pkgs.lib.makeBinPath [
                      pkgs.ffmpeg-full
                      pkgs.espeak-ng
                    ]
                  } \
                  --prefix LD_LIBRARY_PATH : ${pkgs.lib.makeLibraryPath [ pkgs.espeak-ng ]} \
                  ${pkgs.lib.optionalString withCuda "--prefix LD_LIBRARY_PATH : /run/opengl-driver/lib:${pkgs.lib.makeLibraryPath buildInputs}"} \
                  --set PYTHONPATH "$out/share/speaches/src" \
                  --chdir "$out/share/speaches" \
                  --add-flags "-m uvicorn" \
                  --add-flags "--factory speaches.main:create_app" \
                  --add-flags "--host \''${UVICORN_HOST:-0.0.0.0}" \
                  --add-flags "--port \''${UVICORN_PORT:-8000}"

                ${pkgs.lib.optionalString withDev ''
                  makeWrapper ${pythonEnv}/bin/python $out/bin/speaches-python \
                    --prefix PATH : ${
                      pkgs.lib.makeBinPath [
                        pkgs.ffmpeg-full
                        pkgs.espeak-ng
                      ]
                    } \
                    --prefix LD_LIBRARY_PATH : ${pkgs.lib.makeLibraryPath [ pkgs.espeak-ng ]} \
                    --set PYTHONPATH "$out/share/speaches/src"
                ''}
              '';

              passthru = { inherit pythonEnv; };

              meta = with pkgs.lib; {
                description = "AI-powered speech processing application";
                homepage = "https://github.com/speaches-ai/speaches";
                license = licenses.mit;
                platforms = platforms.unix;
              };
            };

          # Default packages for convenience
          defaultPkgs = import nixpkgs {
            inherit system;
            config = {
              allowUnfree = true;
            }
            // (if isLinux then localCudaConfig else { });
            overlays = [
              (mkOverlay {
                pythonVersion = "python312";
                cudaSupport = true;
              })
            ];
          };

          devPkgs = import nixpkgs {
            inherit system;
            config = {
              allowUnfree = true;
            }
            // (if isLinux then localCudaConfig else { });
            overlays = [
              (mkOverlay {
                pythonVersion = "python312";
                cudaSupport = true;
              })
            ];
          };

          # Model fetchers using nix-hug with proper hashes
          models = {
            # Kokoro TTS model (primary TTS engine)
            kokoro-82m = nix-hug.lib.${system}.fetchModel {
              url = "speaches-ai/Kokoro-82M-v1.0-ONNX";
              rev = "dc196c76d64fed9203906231372bcb98135815df";
              fileTreeHash = "sha256-+Aea1c28vvS+pfOs2alshOajGzW6I7ujDVIIAQ5KlgI=";
            };

            # Kokoro PyTorch model (GPU-accelerated TTS)
            kokoro-82m-pytorch = nix-hug.lib.${system}.fetchModel {
              url = "hexgrad/Kokoro-82M";
              rev = "f3ff3571791e39611d31c381e3a41a3af07b4987";
              fileTreeHash = "sha256:1v5kig4nhgykdpqka5mvdsw8qm0n6lw92n5baz3f0ablp8pbjdm9";
            };

            # Silero VAD model (voice activity detection)
            silero-vad = nix-hug.lib.${system}.fetchModel {
              url = "onnx-community/silero-vad";
              rev = "e71cae966052b992a7eca6b17738916ce0eca4ec";
              fileTreeHash = "sha256-Ngj+Sq0vWS2MEPbOzpCoUe1iBORhDyaK2Eluq/RmUEs=";
            };

            # Whisper STT model (base version for lower RAM usage)
            whisper-base = nix-hug.lib.${system}.fetchModel {
              url = "Systran/faster-whisper-base";
              rev = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66";
              fileTreeHash = "sha256-GYgT6udNwSgjZabqajK/i8kL3pvRPbaTC2PQdUfH0EY=";
            };

            # Whisper STT model (tiny.en for fast tests)
            whisper-tiny-en = nix-hug.lib.${system}.fetchModel {
              url = "Systran/faster-whisper-tiny.en";
              rev = "0d3d19a32d3338f10357c0889762bd8d64bbdeba";
              fileTreeHash = "sha256-5vcmhdQIKuVlf4X737KGqtHxLONtAYfsHaG/+vbNjRE=";
            };

            # Kyutai Pocket TTS model (without voice cloning, ungated)
            pocket-tts = nix-hug.lib.${system}.fetchModel {
              url = "kyutai/pocket-tts-without-voice-cloning";
              rev = "075c0abfe7e41450521b0200b5168cfbc16bc77b";
              fileTreeHash = "sha256:1zvwcnwz97b68lci4fzn908yv9yjfayj1ad2slvjrqfiix4pnz3y";
            };

            # Wespeaker speaker embedding model
            wespeaker = nix-hug.lib.${system}.fetchModel {
              url = "pyannote/wespeaker-voxceleb-resnet34-LM";
              rev = "837717ddb9ff5507820346191109dc79c958d614";
              fileTreeHash = "sha256-X6meYLcrkjfV2X8rLebIzgY8BTC99R7qL8Bqsn7gEzg=";
            };

          };

          # Package variants -- pass localCudaConfig so torch/CUDA derivations
          # match colmena and can be reused from the Nix store.
          cudaConfig = if isLinux then localCudaConfig else { };
          speaches = mkSpeaches { extraNixpkgsConfig = cudaConfig; };
          speaches-cpu = mkSpeaches { withCuda = false; };
          speaches-coreml = mkSpeaches {
            withCuda = false;
            withCoreML = true;
          };
          speaches-dev = mkSpeaches {
            withDev = true;
            extraNixpkgsConfig = cudaConfig;
          };

          mkPythonVariant =
            pythonVersion:
            mkSpeaches {
              inherit pythonVersion;
              extraNixpkgsConfig = cudaConfig;
            };
          speaches-python312 = mkPythonVariant "python312";
          speaches-python313 = mkPythonVariant "python313";
          speaches-python314 = mkPythonVariant "python314";
          speaches-python315 = (mkPythonVariant "python315").overrideAttrs { meta.broken = true; };

          # Shared test model cache
          testModelCache = nix-hug.lib.${system}.buildCache {
            models = [
              models.kokoro-82m
              models.kokoro-82m-pytorch
              models.silero-vad
              models.whisper-base
              models.whisper-tiny-en
              models.wespeaker
              models.pocket-tts
            ];
          };

          testPython = defaultPkgs.python312.withPackages (ps: [ ps.httpx ]);

          # Parameterized NixOS VM e2e test (Linux-only)
          mkE2eTest =
            {
              pythonVersion,
              fullTest ? true,
            }:
            let
              testPackage = mkSpeaches {
                inherit pythonVersion;
                withCuda = false;
              };
            in
            defaultPkgs.testers.nixosTest {
              name = "speaches-e2e-test-${pythonVersion}";
              enableOCR = false;

              nodes.machine =
                {
                  config,
                  pkgs,
                  ...
                }:
                {
                  imports = [ ./nix/module.nix ];

                  documentation.enable = false;

                  environment.variables = {
                    HF_HUB_CACHE = "${testModelCache}";
                    HF_HUB_OFFLINE = "1";
                  };

                  services.speaches = {
                    enable = true;
                    package = testPackage;
                    host = "127.0.0.1";
                    port = 18000;
                    environment = {
                      SPEACHES_WHISPER_MODEL = "Systran/faster-whisper-base";
                      HF_HUB_CACHE = "${testModelCache}";
                      HF_HUB_OFFLINE = "1";
                    };
                  };

                  environment.systemPackages = [
                    (pkgs.python312.withPackages (ps: [ ps.httpx ]))
                  ];

                  virtualisation = {
                    memorySize = 4096;
                    cores = 2;
                  };
                };

              testScript =
                let
                  e2eScript = "${./tests/e2e_api.py}";
                  otelCheck = ''
                    machine.succeed("""
                      ${testPackage.pythonEnv}/bin/python -c "
                    import opentelemetry.instrumentation.asyncio
                    import opentelemetry.instrumentation.asgi
                    import opentelemetry.instrumentation.fastapi
                    import opentelemetry.instrumentation.httpx
                    import opentelemetry.instrumentation.logging
                    import opentelemetry.instrumentation.grpc
                    print('All opentelemetry modules imported successfully')
                    "
                    """)
                  '';
                in
                if fullTest then
                  ''
                    import time
                    machine.start()
                    machine.wait_for_unit("speaches.service")
                    machine.wait_for_open_port(18000)
                    time.sleep(5)
                    ${otelCheck}
                    print("OpenTelemetry modules importable: PASS")
                    machine.succeed("python ${e2eScript} http://127.0.0.1:18000")
                    print("All NixOS VM e2e tests passed with ${pythonVersion}!")
                  ''
                else
                  ''
                    machine.start()
                    machine.wait_for_unit("speaches.service")
                    machine.wait_for_open_port(18000)
                    print("Testing ${pythonVersion} package...")
                    machine.succeed("python ${e2eScript} http://127.0.0.1:18000")
                    print("${pythonVersion} e2e test passed!")
                  '';
            };

          # Shell snippet: trap server cleanup and poll /health on $SPEACHES_PORT.
          waitForServer = ''
            SERVER_PID=$!
            cleanup() { kill $SERVER_PID 2>/dev/null || true; wait $SERVER_PID 2>/dev/null || true; }
            trap cleanup EXIT
            echo "Waiting for server..."
            for i in $(seq 1 120); do
              ${defaultPkgs.curl}/bin/curl -s http://127.0.0.1:$SPEACHES_PORT/health >/dev/null 2>&1 && break
              [ "$i" -eq 120 ] && echo "Server failed to start" && exit 1
              sleep 1
            done
            echo "Server ready."
          '';

          mkE2eTestScript =
            { name, serverPackage }:
            defaultPkgs.writeShellScriptBin name ''
              set -euo pipefail
              export HF_HUB_CACHE="${testModelCache}"
              export HF_HUB_OFFLINE=1
              SPEACHES_PORT=18000
              ${serverPackage}/bin/speaches --host 127.0.0.1 --port $SPEACHES_PORT &
              ${waitForServer}
              ${testPython}/bin/python ${./tests/e2e_api.py} http://127.0.0.1:$SPEACHES_PORT
            '';

          devCustomDeps = import ./nix/dependencies.nix {
            pkgs = devPkgs;
            pyPackages = devPkgs.python312.pkgs;
            system = system;
          };

          lib = nixpkgs.lib;

          testSrc = devPkgs.lib.cleanSourceWith {
            src = self;
            filter =
              path: type:
              let
                relPath = devPkgs.lib.removePrefix (toString self + "/") (toString path);
              in
              devPkgs.lib.hasPrefix "src" relPath
              || devPkgs.lib.hasPrefix "tests" relPath
              || relPath == "pyproject.toml"
              || relPath == "model_aliases.json"
              || relPath == "audio.wav";
          };

          mkPytestCheck =
            {
              pythonVersion ? "python312",
              extraIgnore ? [ ],
            }:
            let
              testPkgs = import nixpkgs {
                inherit system;
                config.allowUnfree = true;
                overlays = [
                  (mkOverlay {
                    inherit pythonVersion;
                    cudaSupport = false;
                  })
                ];
              };
              testCustomDeps = import ./nix/dependencies.nix {
                pkgs = testPkgs;
                pyPackages = testPkgs.${pythonVersion}.pkgs;
                inherit system;
              };
              pytestEnv = mkPythonEnv {
                python = testPkgs.${pythonVersion};
                customDeps = testCustomDeps;
                inherit (testPkgs) lib;
                withDev = true;
              };
            in
            testPkgs.runCommand "speaches-pytest-${pythonVersion}"
              {
                nativeBuildInputs = [
                  pytestEnv
                  testPkgs.espeak-ng
                  testPkgs.ffmpeg-full
                  testPkgs.cacert
                ];
              }
              ''
                export SSL_CERT_FILE=${testPkgs.cacert}/etc/ssl/certs/ca-bundle.crt
                export PYANNOTE_METRICS_ENABLED=0
                cp -r ${testSrc}/src src
                cp -r ${testSrc}/tests tests
                cp ${testSrc}/pyproject.toml pyproject.toml
                cp ${testSrc}/model_aliases.json model_aliases.json
                cp ${testSrc}/audio.wav audio.wav
                mkdir -p realtime-console/dist
                export HF_HUB_CACHE="${testModelCache}"
                export HF_HUB_OFFLINE=1
                PYTHONPATH=src ${pytestEnv}/bin/python -m pytest tests/ \
                  --ignore=tests/e2e_api.py \
                  --ignore=tests/e2e_realtime.py \
                  --ignore=tests/e2e_pocket_tts.py \
                  --ignore=tests/e2e_kokoro_pytorch.py \
                  ${testPkgs.lib.concatMapStringsSep " " (p: "--ignore=${p}") extraIgnore} \
                  -x -q
                touch $out
              '';

          pytestCheck = mkPytestCheck { };
        in
        {
          # Development shell
          devShells.default = devPkgs.mkShell {
            nativeBuildInputs =
              with devPkgs;
              [
                (mkPythonEnv {
                  python = devPkgs.python312;
                  customDeps = devCustomDeps;
                  inherit (devPkgs) lib;
                  withDev = true;
                  withDocs = true;
                })
                uv
                ffmpeg-full
                go-task
                act
                docker
                docker-compose
                grafana-loki
                tempo
                parallel
                pv
                websocat
                basedpyright
              ]
              ++ devPkgs.lib.optionals isLinux (cudaDepsFor devPkgs);

            LD_LIBRARY_PATH = devPkgs.lib.optionalString isLinux "/run/opengl-driver/lib:${
              devPkgs.lib.makeLibraryPath (
                cudaDepsFor devPkgs
                ++ (with devPkgs; [
                  portaudio
                  zlib
                  stdenv.cc.cc
                  openssl
                ])
              )
            }";

            PYANNOTE_METRICS_ENABLED = "0";

            shellHook = ''
              source .venv/bin/activate 2>/dev/null || true
              source .env 2>/dev/null || true
            '';
          };

          # Packages
          packages = {
            default = speaches;
            inherit
              speaches
              speaches-cpu
              speaches-coreml
              speaches-dev
              speaches-python312
              speaches-python313
              speaches-python314
              ;

            # Models
            inherit (models)
              kokoro-82m
              silero-vad
              whisper-base
              whisper-tiny-en
              wespeaker
              pocket-tts
              ;

            # Build a proper HuggingFace cache with all models
            model-cache = nix-hug.lib.${system}.buildCache {
              models = [
                models.kokoro-82m
                models.silero-vad
                models.whisper-base
                models.whisper-tiny-en
                models.wespeaker
              ];
            };

            # Documentation site (uses mkPythonEnv for full speaches importability)
            docs =
              let
                docsPython = mkPythonEnv {
                  python = devPkgs.python312;
                  customDeps = devCustomDeps;
                  inherit (devPkgs) lib;
                  withDocs = true;
                };
                docsSrc = lib.cleanSourceWith {
                  src = self;
                  filter =
                    path: type:
                    let
                      relPath = lib.removePrefix (toString self + "/") (toString path);
                    in
                    relPath == "mkdocs.yml" || lib.hasPrefix "docs" relPath || lib.hasPrefix "src" relPath;
                };
              in
              devPkgs.runCommand "speaches-docs" { nativeBuildInputs = [ docsPython ]; } ''
                cp -r ${docsSrc}/docs docs
                cp ${docsSrc}/mkdocs.yml mkdocs.yml
                cp -r ${docsSrc}/src src
                PYTHONPATH=src mkdocs build -d $out
              '';

            # End-to-end realtime test (uses mock LLM, tests full WS pipeline)
            e2e-test-realtime =
              let
                realtimeTestPython = defaultPkgs.python312.withPackages (
                  ps: with ps; [
                    httpx
                    websockets
                    uvicorn
                    fastapi
                  ]
                );
              in
              defaultPkgs.writeShellScriptBin "speaches-e2e-test-realtime" ''
                set -euo pipefail
                export HF_HUB_CACHE="${testModelCache}"
                export HF_HUB_OFFLINE=1
                SPEACHES_PORT=18000
                MOCK_LLM_PORT=18001
                CHAT_COMPLETION_BASE_URL="http://127.0.0.1:$MOCK_LLM_PORT/v1" \
                CHAT_COMPLETION_API_KEY="mock-key" \
                LOOPBACK_HOST_URL="http://127.0.0.1:$SPEACHES_PORT" \
                  ${speaches-cpu}/bin/speaches --host 127.0.0.1 --port $SPEACHES_PORT &
                ${waitForServer}
                ${realtimeTestPython}/bin/python ${./tests/e2e_realtime.py}
              '';

            e2e-test = mkE2eTestScript {
              name = "speaches-e2e-test";
              serverPackage = speaches-cpu;
            };
            e2e-test-cuda = mkE2eTestScript {
              name = "speaches-e2e-test-cuda";
              serverPackage = speaches;
            };
            e2e-test-python313 = mkE2eTestScript {
              name = "speaches-e2e-test-python313";
              serverPackage = mkSpeaches {
                pythonVersion = "python313";
                withCuda = false;
              };
            };
            e2e-test-python314 = mkE2eTestScript {
              name = "speaches-e2e-test-python314";
              serverPackage = mkSpeaches {
                pythonVersion = "python314";
                withCuda = false;
              };
            };
            e2e-test-cuda-python313 = mkE2eTestScript {
              name = "speaches-e2e-test-cuda-python313";
              serverPackage = speaches-python313;
            };
            e2e-test-cuda-python314 = mkE2eTestScript {
              name = "speaches-e2e-test-cuda-python314";
              serverPackage = speaches-python314;
            };

            # Kyutai Pocket TTS end-to-end test (TTS -> STT round-trip)
            e2e-pocket-tts =
              let
                pocketTestPython = defaultPkgs.python312.withPackages (ps: [ ps.httpx ]);
                serverPackage = speaches-cpu;
                modelCache = nix-hug.lib.${system}.buildCache {
                  models = [
                    models.silero-vad
                    models.whisper-base
                  ];
                };
              in
              defaultPkgs.writeShellScriptBin "speaches-e2e-pocket-tts" ''
                set -euo pipefail
                # Merge nix-hug cache (whisper, VAD) with user's HF cache so pocket-tts can download its model.
                MERGED_CACHE=$(mktemp -d)
                if [ -d "${modelCache}" ]; then
                  for d in "${modelCache}"/models--*; do
                    [ -d "$d" ] && ln -s "$d" "$MERGED_CACHE/$(basename "$d")"
                  done
                fi
                USER_HF_CACHE="''${HF_HUB_CACHE:-$HOME/.cache/huggingface/hub}"
                if [ -d "$USER_HF_CACHE" ]; then
                  for d in "$USER_HF_CACHE"/models--*; do
                    target="$MERGED_CACHE/$(basename "$d")"
                    [ -d "$d" ] && [ ! -e "$target" ] && ln -s "$d" "$target"
                  done
                fi
                export HF_HUB_CACHE="$MERGED_CACHE"
                SPEACHES_PORT=18300
                ${serverPackage}/bin/speaches --host 127.0.0.1 --port $SPEACHES_PORT &
                ${waitForServer}
                ${pocketTestPython}/bin/python ${./tests/e2e_pocket_tts.py} http://127.0.0.1:$SPEACHES_PORT
              '';

            # Kokoro PyTorch GPU TTS end-to-end test
            e2e-test-kokoro-pytorch =
              let
                pytorchTestPython = defaultPkgs.python313.withPackages (ps: [ ps.httpx ]);
                serverPackage = speaches-python313;
                modelCache = nix-hug.lib.${system}.buildCache {
                  models = [
                    models.kokoro-82m-pytorch
                    models.silero-vad
                  ];
                };
              in
              defaultPkgs.writeShellScriptBin "speaches-e2e-test-kokoro-pytorch" ''
                set -euo pipefail
                export HF_HUB_CACHE="${modelCache}"
                export HF_HUB_OFFLINE=1
                export CUDA_VISIBLE_DEVICES=''${CUDA_VISIBLE_DEVICES:-0}
                export LD_LIBRARY_PATH=/run/opengl-driver/lib''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
                SPEACHES_PORT=18200
                ${serverPackage}/bin/speaches --host 127.0.0.1 --port $SPEACHES_PORT &
                ${waitForServer}
                ${pytorchTestPython}/bin/python ${./tests/e2e_kokoro_pytorch.py} http://127.0.0.1:$SPEACHES_PORT
              '';
          };

          # Applications
          apps.default = {
            type = "app";
            program = "${speaches}/bin/speaches";
            meta = {
              description = "AI-powered speech processing application";
              maintainers = [ ];
            };
          };

          # NixOS VM tests (Linux-only, nixosTest requires a NixOS VM)
          # + build-only checks on darwin to catch transitive dep failures
          # + pytest unit tests on all platforms
          checks =
            let
              common = {
                pytest = pytestCheck;
                pytest-python313 = mkPytestCheck { pythonVersion = "python313"; };
                pytest-python314 = mkPytestCheck {
                  pythonVersion = "python314";
                  # Skip model-loading tests on 3.14: pyannote-audio unavailable,
                  # and model inference is too slow in the CPU-only sandbox.
                  # Full model tests are covered by python312/313 checks.
                  extraIgnore = [
                    "tests/model_manager_test.py"
                    "tests/test_vad_v6.py"
                    "tests/vad_test.py"
                    "tests/speech_test.py"
                    "tests/speech_embedding_test.py"
                    "tests/diarization_test.py"
                  ];
                };
              };
            in
            if isLinux then
              let
                e2e-python312 = mkE2eTest { pythonVersion = "python312"; };
              in
              common
              // {
                inherit e2e-python312;
                e2e = e2e-python312; # default alias
                e2e-python313 = mkE2eTest { pythonVersion = "python313"; };
                e2e-python314 = mkE2eTest { pythonVersion = "python314"; };
                # python315 excluded -- speaches-python315 is meta.broken
              }
            else
              # Darwin: no VM tests, but verify packages build across python versions.
              let
                cpuVariant =
                  pyv:
                  mkSpeaches {
                    pythonVersion = pyv;
                    withCuda = false;
                  };
              in
              common
              // {
                speaches-cpu = mkSpeaches { withCuda = false; };
                speaches-python313 = cpuVariant "python313";
                speaches-python314 = cpuVariant "python314";
              };

          formatter = defaultPkgs.nixfmt;

          lib = {
            inherit mkSpeaches;
          };
        };

      perSystemOutputs = forEachSystem perSystem;
      inherit (nixpkgs) lib;
    in
    {
      nixosModules.default =
        { pkgs, lib, ... }:
        {
          imports = [ ./nix/module.nix ];
          services.speaches.package = lib.mkDefault self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        };

      overlays.default = final: prev: {
        speaches = self.packages.${prev.stdenv.hostPlatform.system}.default;
        speaches-cpu = self.packages.${prev.stdenv.hostPlatform.system}.speaches-cpu;
      };

      devShells = lib.mapAttrs (_: v: v.devShells) perSystemOutputs;
      packages = lib.mapAttrs (_: v: v.packages) perSystemOutputs;
      apps = lib.mapAttrs (_: v: v.apps) perSystemOutputs;
      checks = lib.mapAttrs (_: v: v.checks) perSystemOutputs;
      formatter = lib.mapAttrs (_: v: v.formatter) perSystemOutputs;
      lib = lib.mapAttrs (_: v: v.lib) perSystemOutputs;
    };
}
