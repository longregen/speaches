{
  pythonVersion ? "python312",
  cudaSupport ? true,
}:
final: prev:
let
  system = prev.stdenv.hostPlatform.system;
in
{
  ctranslate2 =
    if cudaSupport && system == "x86_64-linux" then
      prev.ctranslate2.override {
        stdenv = prev.gcc14Stdenv;
        withCUDA = true;
        withCuDNN = true;
        cudaPackages = prev.cudaPackages_12;
      }
    else
      prev.ctranslate2;

  silero-encoder-v5 = prev.fetchurl {
    url = "https://github.com/SYSTRAN/faster-whisper/raw/v1.1.0/faster_whisper/assets/silero_encoder_v5.onnx";
    hash = "sha256-Dp/I9WQHaT0oP5kEX7lcK5D9yjQzzl+D4sEh+05hUHU=";
  };
  silero-decoder-v5 = prev.fetchurl {
    url = "https://github.com/SYSTRAN/faster-whisper/raw/v1.1.0/faster_whisper/assets/silero_decoder_v5.onnx";
    hash = "sha256-jCA0T1CYRqB8zYWCfohXAX+uZ/q9WGib7Br3nh1Igwc=";
  };

  # packageOverrides for proper fixed-point propagation — transitive deps
  # (e.g. segments -> csvw -> google-pasta) pick up our overrides
  "${pythonVersion}" = prev.${pythonVersion}.override {
    packageOverrides =
      pySelf: pySuper:
      # Cross-platform fixes (macOS sandbox / test failures)
      {
        # google-pasta tests fail on macOS due to AST-related issues
        google-pasta = pySuper.google-pasta.overridePythonAttrs { doCheck = false; };
        # dlinfo tests fail on macOS (tries to access /usr/lib/*.dylib outside sandbox)
        dlinfo = pySuper.dlinfo.overridePythonAttrs { doCheck = false; };
        # opentelemetry-exporter-otlp-proto-grpc tests fail on macOS due to grpcio issues
        opentelemetry-exporter-otlp-proto-grpc =
          pySuper.opentelemetry-exporter-otlp-proto-grpc.overridePythonAttrs
            {
              doCheck = false;
              dontCheckRuntimeDeps = true;
            };
      }
      # Python 3.14 fixes — replace packages disabled in nixpkgs
      // prev.lib.optionalAttrs (pySuper.python.pythonAtLeast "3.14") {
        # google-pasta — disabled in nixpkgs for Python >= 3.14 (unmaintained since 2020)
        "google-pasta" = pySelf.buildPythonPackage {
          pname = "google-pasta";
          version = "0.2.0";
          format = "wheel";
          src = prev.fetchurl {
            url = "https://files.pythonhosted.org/packages/a3/de/c648ef6835192e6e2cc03f40b19eeda4382c49b5bafb43d88b931c4c74ac/google_pasta-0.2.0-py3-none-any.whl";
            hash = "sha256-sySCeUo2a1NmoyySqakgGxB4IYiZNaArPlH2tDLqhO0=";
          };
          propagatedBuildInputs = [ pySelf.six ];
        };

        # aws-sam-translator — disabled in nixpkgs for Python >= 3.14
        "aws-sam-translator" = pySelf.buildPythonPackage {
          pname = "aws-sam-translator";
          version = "1.107.0";
          format = "wheel";
          src = prev.fetchurl {
            url = "https://files.pythonhosted.org/packages/03/01/dc57ec8a481b6f2b20cf25ff383803b7f4e5e78655e8b64b4b8328541b71/aws_sam_translator-1.107.0-py3-none-any.whl";
            hash = "sha256-lbKgOof7Ydmp6eQxoY5iIcR4CzJ5Lu1LI55y73Nm1js=";
          };
          propagatedBuildInputs = with pySelf; [
            boto3
            jsonschema
            typing-extensions
            pydantic
          ];
        };

        # opentelemetry-instrumentation-httpx — 72/146 tests fail on 3.14, runtime deps
        # check fails (missing opentelemetry-semantic-conventions)
        "opentelemetry-instrumentation-httpx" =
          pySuper.opentelemetry-instrumentation-httpx.overridePythonAttrs
            (_: {
              doCheck = false;
              dontCheckRuntimeDeps = true;
              pythonImportsCheck = [ ];
            });
      };
  };

  # Flat merge for gradio/faster-whisper overrides and custom deps — these don't need
  # fixed-point propagation since no other nixpkgs package depends on them
  "${pythonVersion}Packages" =
    let
      pyPackages = final.${pythonVersion}.pkgs;
      customDeps = import ./dependencies.nix {
        pkgs = final;
        inherit pyPackages system;
      };
    in
    pyPackages
    // {
      gradio = pyPackages.gradio.overridePythonAttrs (old: {
        dontCheckRuntimeDeps = true;
        # test_pipelines.py imports ImageToTextPipeline removed in transformers 5.x
        disabledTestPaths = (old.disabledTestPaths or [ ]) ++ [ "test/test_pipelines.py" ];
        # gradio tests fail on macOS due to port binding issues in sandbox
        doCheck = false;
      });

      faster-whisper = pyPackages.faster-whisper.overrideAttrs (old: {
        propagatedBuildInputs = old.propagatedBuildInputs ++ [ final.ctranslate2 ];
        postInstall = (old.postInstall or "") + ''
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
}
