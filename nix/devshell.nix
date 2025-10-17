{ pkgs, system }:
pkgs.mkShell {
  nativeBuildInputs =
    with pkgs;
    [
      (python312.withPackages (
        ps:
        with ps;
        [
          fastapi
          huggingface-hub
          numpy
          pydantic
          pydantic-settings
          python-multipart
          sounddevice
          soundfile
          uvicorn
          openai
          aiostream
          cachetools
          httpx
          httpx-sse
          httpx-ws
          faster-whisper
          anyio
          pytest-asyncio
          pytest
          pytest-mock
          ruff
        ]
        ++ (
          with pkgs.python312Packages;
          [
            gradio
            kokoro_onnx
            aiortc
            onnx_asr
            espeakng_loader
            pytest_antilru
            opentelemetry_instrumentation_openai
            opentelemetry_instrumentation_openai_v2
          ]
          ++ lib.optionals stdenv.isLinux [
            piper_tts
          ]
        )
      ))
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
    ++ pkgs.lib.optionals (system == "x86_64-linux") (
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

  LD_LIBRARY_PATH =
    pkgs.lib.optionalString (system == "x86_64-linux")
      "/run/opengl-driver/lib:${
        pkgs.lib.makeLibraryPath (
          with pkgs;
          [
            cudaPackages_12.cudnn
            cudaPackages_12.libcublas
            cudaPackages_12.libcurand
            cudaPackages_12.libcufft
            cudaPackages_12.cuda_cudart
            cudaPackages_12.cuda_nvrtc
            portaudio
            zlib
            stdenv.cc.cc
            openssl
          ]
        )
      }";

  shellHook = ''
    source .venv/bin/activate 2>/dev/null || true
    source .env 2>/dev/null || true
  '';
}
