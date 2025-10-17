{
  nixpkgs,
  system,
  mkOverlay,
  src,
}:
{
  pythonVersion ? "python312",
  withCuda ? (system == "x86_64-linux"),
  withDev ? false,
}:
let
  overlay = mkOverlay {
    inherit pythonVersion;
    cudaSupport = withCuda;
  };

  pkgs = import nixpkgs {
    inherit system;
    config = {
      allowUnfree = true;
      allowBroken = true;
      cudaSupport = withCuda;
      cudaCapabilities = [
        "8.9"
        "12.0"
      ]; # Ada Lovelace (RTX 4090) + Blackwell SM120
      cudaEnableForwardCompat = true;
    };
    overlays = [ overlay ];
  };

  python = pkgs.${pythonVersion};
  pythonPackages = pkgs."${pythonVersion}Packages";

  pythonEnv = python.withPackages (
    ps:
    let
      coreDeps = [
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
        pythonPackages.gradio
        ps.httpx
        ps.httpx-sse
        ps.httpx-ws
        pythonPackages.faster-whisper
      ]
      ++ pkgs.lib.optionals (pythonPackages ? kokoro_onnx) [
        pythonPackages.kokoro_onnx
        pythonPackages.aiortc
        pythonPackages.onnx_asr
        pythonPackages.onnx_diarization
      ];

      piperDeps = [
        pythonPackages.piper_phonemize
        pythonPackages.piper_tts
      ];

      devDeps = pkgs.lib.optionals withDev [
        ps.anyio
        ps.pytest-asyncio
        ps.pytest
        ps.pytest-mock
        ps.ruff
        pythonPackages.pytest_antilru
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
        pythonPackages.opentelemetry_instrumentation_asyncio
        pythonPackages.opentelemetry_instrumentation_openai
        pythonPackages.opentelemetry_instrumentation_openai_v2
      ];
    in
    coreDeps ++ piperDeps ++ devDeps ++ otelDeps
  );
in
pkgs.stdenv.mkDerivation rec {
  pname = "speaches";
  version = "0.1.0";

  inherit src;

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
  ++ pkgs.lib.optionals withCuda (
    with pkgs;
    [
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
  '';

  meta = with pkgs.lib; {
    description = "AI-powered speech processing application";
    homepage = "https://github.com/speaches-ai/speaches";
    license = licenses.mit;
    platforms = platforms.unix;
  };
}
