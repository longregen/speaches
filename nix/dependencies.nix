# Custom package dependencies for speaches
{
  pkgs,
  pyPackages,
  system,
}:
rec {
  # Simplified espeakng-loader inline
  espeakng_loader = pyPackages.buildPythonPackage {
    pname = "espeakng_loader";
    version = "0.1.0";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "thewh1teagle";
      repo = "espeakng-loader";
      rev = "0ddc87adf77e5850d7eeb542ac8a87d421b64daa"; # main as of 2026-03-20; hash may need updating if it no longer matches
      hash = "sha256-nSEQ9rofFl6BTH18L5DzaQ1Ymw5H3d+wSEXUxp4o1DM=";
    };
    nativeBuildInputs = [ pyPackages.hatchling ];
    propagatedBuildInputs = [ pkgs.espeak-ng ];
    postPatch = ''
      substituteInPlace src/espeakng_loader/__init__.py \
        --replace-fail 'libespeak-ng' '${pkgs.espeak-ng}/lib/libespeak-ng' \
        --replace-fail "Path(__file__).parent / 'espeak-ng-data'" "Path('${pkgs.espeak-ng}/share/espeak-ng-data')"
    '';
    doCheck = false;
  };

  kokoro_onnx = pyPackages.buildPythonPackage rec {
    pname = "kokoro_onnx";
    version = "0.4.9-git";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "thewh1teagle";
      repo = "kokoro-onnx";
      rev = "2bfb160cfae06709a6d7c3d436293972e0f1d12f"; # main as of 2026-03-20; hash may need updating if it no longer matches
      hash = "sha256-UlPhijY9UHKcck30C0mQ5CcN3Zi/TzARjpzAsYkUqxc=";
    };
    nativeBuildInputs = with pyPackages; [
      hatchling
      hatch-vcs
    ];
    propagatedBuildInputs =
      with pyPackages;
      [
        numpy
        huggingface-hub
        onnxruntime
        colorlog
      ]
      ++ [
        espeakng_loader
        phonemizer_fork
      ];
    doCheck = false;
  };

  # Simplified aiortc-related packages
  aioice = pyPackages.buildPythonPackage {
    pname = "aioice";
    version = "0.10.2";
    format = "setuptools";
    src = pyPackages.fetchPypi {
      pname = "aioice";
      version = "0.10.2";
      hash = "sha256-vyNsaCnuM8jlQFNdMc1aBmtTHLVt4r6UxGvnbWixqAY=";
    };
    propagatedBuildInputs = with pyPackages; [
      dnspython
      ifaddr
    ];
    doCheck = false;
  };

  pylibsrtp = pyPackages.buildPythonPackage {
    pname = "pylibsrtp";
    version = "1.0.0";
    format = "setuptools";
    src = pyPackages.fetchPypi {
      pname = "pylibsrtp";
      version = "1.0.0";
      hash = "sha256-s53/B1smOo3tU3fySQxg0q9FLJ8GxNBhx6K2QGErNNQ=";
    };
    nativeBuildInputs = [ pyPackages.cffi ];
    buildInputs = [
      pkgs.srtp
      pkgs.openssl
    ];
    propagatedBuildInputs = [ pyPackages.cffi ];
    doCheck = false;
  };

  aiortc = pyPackages.buildPythonPackage rec {
    pname = "aiortc";
    version = "1.14.0";
    format = "setuptools";
    src = pyPackages.fetchPypi {
      pname = "aiortc";
      version = "1.14.0";
      hash = "sha256-rcimes4QoIVyHliOBqADWO2Or19rYvCpU1j/RWKN12I=";
    };
    propagatedBuildInputs =
      with pyPackages;
      [
        pyee
        pyopenssl
        cryptography
        av
        google-crc32c
      ]
      ++ [
        aioice
        pylibsrtp
      ];
    buildInputs = with pkgs; [
      ffmpeg-full
      libvpx
      libopus
      srtp
    ];
    doCheck = false;
  };

  # Override csvw to avoid frictionless -> moto -> sagemaker -> google-pasta
  # dependency chain which is broken on Python 3.14+.
  # nixpkgs' csvw incorrectly includes frictionless as a runtime dep (it is only a test dep).
  csvw = pyPackages.buildPythonPackage {
    pname = "csvw";
    version = "3.7.0";
    format = "setuptools";
    src = pyPackages.fetchPypi {
      pname = "csvw";
      version = "3.7.0";
      hash = "sha256-hptcdhSB5SwBqZ+0dJsnikuLDbTg+hllozo0QccDRls=";
    };
    nativeBuildInputs = [ pyPackages.setuptools ];
    propagatedBuildInputs = with pyPackages; [
      attrs
      isodate
      python-dateutil
      rfc3986
      uritemplate
      babel
      requests
      language-tags
      rdflib
      termcolor
      jsonschema
    ];
    dontCheckRuntimeDeps = true;
    doCheck = false;
  };

  # Override segments to use our csvw (without frictionless)
  segments = pyPackages.buildPythonPackage {
    pname = "segments";
    version = "2.4.0";
    format = "setuptools";
    src = pyPackages.fetchPypi {
      pname = "segments";
      version = "2.4.0";
      hash = "sha256-u6cfVSDd1UyKovTXZaYGGMaGIWLW5zVqSgl/IiMWb1s=";
    };
    nativeBuildInputs = [ pyPackages.setuptools ];
    propagatedBuildInputs = with pyPackages; [
      regex
      csvw
    ];
    dontCheckRuntimeDeps = true;
    doCheck = false;
  };

  phonemizer_fork = pyPackages.buildPythonPackage {
    pname = "phonemizer-fork";
    version = "3.3.2";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "phonemizer_fork";
      version = "3.3.2";
      hash = "sha256-EOFugn0EQ7CHBi4htV6AXACYnPE0Oy6B5zTK5fbAz2k=";
    };
    nativeBuildInputs = [ pyPackages.hatchling ];
    propagatedBuildInputs = with pyPackages; [
      joblib
      segments
      attrs
      typing-extensions
      (dlinfo.overridePythonAttrs (old: {
        doCheck = false;
      }))
    ];
    doCheck = false;
  };

  # onnx_asr has a custom hatch build hook that generates .onnx preprocessor models
  # at build time using torch + onnxscript. These are deterministic signal processing
  # graphs (FFT, mel filterbanks, resamplers), not trained weights.
  onnx_asr = pyPackages.buildPythonPackage {
    pname = "onnx_asr";
    version = "0.10.2";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "onnx_asr";
      version = "0.10.2";
      hash = "sha256-cDgZOc+C0CwSV1N+f1glw12xUAjMAqXEOk5ittWsuzQ=";
    };
    nativeBuildInputs = with pyPackages; [
      hatchling
      hatch-vcs
      # Build deps for the custom hatch hook that generates .onnx preprocessor models
      onnx
      onnxscript
      torch
      torchaudio
    ];
    propagatedBuildInputs = with pyPackages; [
      numpy
      onnxruntime
      huggingface-hub
    ];
    doCheck = false;
  };

  # onnx-diarization and its dependencies
  einops = pyPackages.buildPythonPackage {
    pname = "einops";
    version = "0.8.2";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "einops";
      version = "0.8.2";
      hash = "sha256-YJ2mZVcOXiZeJyg6qwnn8nmt6QxPAbz8oRHz0+E/KCc=";
    };
    nativeBuildInputs = [ pyPackages.hatchling ];
    doCheck = false;
  };

  kaldi_native_fbank =
    let
      kissfft-src = pkgs.fetchurl {
        url = "https://github.com/mborgerding/kissfft/archive/febd4caeed32e33ad8b2e0bb5ea77542c40f18ec.zip";
        hash = "sha256-SXED5mQWjr45WAt1etvmFvbPhaFlcq9YHKe8QtCrE/0=";
      };
    in
    pyPackages.buildPythonPackage {
      pname = "kaldi_native_fbank";
      version = "1.22.3";
      format = "setuptools";
      src = pkgs.fetchFromGitHub {
        owner = "csukuangfj";
        repo = "kaldi-native-fbank";
        rev = "v1.22.3";
        hash = "sha256-Wu4wM52T6NoQ1t5/iAyPtkEGnZki5P0jx0eYMFZMb5o=";
      };
      nativeBuildInputs = with pkgs; [
        cmake
        ninja
      ];
      buildInputs = [
        pyPackages.pybind11
      ];
      propagatedBuildInputs = with pyPackages; [ numpy ];
      postPatch = ''
        # Replace FetchContent-based pybind11 download with find_package
        cat > cmake/pybind11.cmake << 'PYBIND_EOF'
        find_package(pybind11 REQUIRED)
        PYBIND_EOF

        # Place pre-fetched kissfft source where cmake/kissfft.cmake expects it
        cp ${kissfft-src} $PWD/kissfft-febd4caeed32e33ad8b2e0bb5ea77542c40f18ec.zip
      '';
      dontUseCmakeConfigure = true;
      doCheck = false;
    };

  onnx_dl = pyPackages.buildPythonPackage {
    pname = "onnx_dl";
    version = "0.1.0";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "onnx_dl";
      version = "0.1.0";
      hash = "sha256-9wRHepJ8jod77OhA/DDh8lZIm+IlIyhdpumCqFN6lxs=";
    };
    nativeBuildInputs = [ pyPackages.uv-build ];
    postPatch = ''
      sed -i 's/requires = \["uv_build[^"]*"\]/requires = ["uv_build"]/' pyproject.toml
    '';
    propagatedBuildInputs = with pyPackages; [ onnxruntime ];
    doCheck = false;
  };

  pyannote_core = pyPackages.buildPythonPackage {
    pname = "pyannote_core";
    version = "6.0.1";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "pyannote_core";
      version = "6.0.1";
      hash = "sha256-S0raMnb2304HP6eRZmNuNZfQ3LWg/iYBSjR3hnzAM/s=";
    };
    nativeBuildInputs = with pyPackages; [
      hatchling
      hatch-vcs
    ];
    propagatedBuildInputs = with pyPackages; [
      numpy
      pandas
      sortedcontainers
    ];
    doCheck = false;
  };

  onnx_diarization = pyPackages.buildPythonPackage {
    pname = "onnx_diarization";
    version = "0.1.0";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "onnx_diarization";
      version = "0.1.0";
      hash = "sha256-CFEeNDfXr1vR9wNJ0BG/kpqFNaTUmAXBQvx9tmb5xOU=";
    };
    nativeBuildInputs = [ pyPackages.uv-build ];
    postPatch = ''
      sed -i 's/requires = \["uv_build[^"]*"\]/requires = ["uv_build"]/' pyproject.toml
    '';
    propagatedBuildInputs =
      with pyPackages;
      [
        scipy
        scikit-learn
        onnxruntime
      ]
      ++ [
        einops
        kaldi_native_fbank
        onnx_dl
        pyannote_core
      ];
    dontCheckRuntimeDeps = true;
    doCheck = false;
  };

  # Piper TTS packages (Linux only)
  # piper-tts v1.3.0+ (from OHF-Voice/piper1-gpl) embeds espeak-ng directly
  # and no longer depends on the old rhasspy/piper-phonemize library.
  # Previously, piper_phonemize v1.2.0 was fetched as pre-compiled wheels from
  # a personal GitHub repo (fedirz/piper-phonemize) -- the highest supply chain risk
  # in the dependency tree. That dependency has been eliminated entirely.

  # piper_phonemize is no longer needed by piper-tts v1.3.0+.
  # Kept as null for backward compatibility with flake.nix references.
  piper_phonemize = null;

  piper_tts =
    let
      isLinux = (system == "x86_64-linux" || system == "aarch64-linux");

      # Override espeak-ng with piper-specific feature flags disabled
      # (mirrors nixpkgs pkgs/by-name/pi/piper-tts/package.nix)
      espeak-ng' = pkgs.espeak-ng.override {
        asyncSupport = false;
        klattSupport = false;
        mbrolaSupport = false;
        pcaudiolibSupport = false;
        sonicSupport = false;
        speechPlayerSupport = false;
      };
    in
    if isLinux then
      pyPackages.buildPythonPackage {
        pname = "piper-tts";
        version = "1.4.1";
        pyproject = true;

        src = pkgs.fetchFromGitHub {
          owner = "OHF-Voice";
          repo = "piper1-gpl";
          tag = "v1.4.1";
          hash = "sha256-V/ESZMUT1PXxHNN7H2ckTBVOQRRf4c/L2GNtnkXvNpA=";
        };

        # Patch from nixpkgs (OHF-Voice/piper1-gpl#17, not yet merged upstream)
        # Allows building against system espeak-ng via pkg-config instead of
        # downloading and compiling espeak-ng from source during the build.
        patches = [
          ./piper-tts-cmake-system-libs.patch
        ];

        nativeBuildInputs = with pyPackages; [
          cmake
          ninja
          scikit-build
          setuptools
          pkgs.pkg-config
        ];

        dontUseCmakeConfigure = true;

        env.CMAKE_ARGS = builtins.toString [
          (pkgs.lib.cmakeFeature "UCD_STATIC_LIB" "${espeak-ng'.ucd-tools}/libucd.a")
        ];

        buildInputs = [
          espeak-ng'
        ];

        propagatedBuildInputs = with pyPackages; [
          onnxruntime
          pathvalidate
        ];

        postInstall = ''
          ln -s ${espeak-ng'}/share/espeak-ng-data $out/${pyPackages.python.sitePackages}/piper/
        '';

        doCheck = false;
      }
    else
      null;

  # OpenTelemetry instrumentation packages
  opentelemetry_instrumentation_asyncio = pyPackages.buildPythonPackage {
    pname = "opentelemetry-instrumentation-asyncio";
    version = "0.61b0";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "opentelemetry_instrumentation_asyncio";
      version = "0.61b0";
      hash = "sha256-Oxc7AJ8Qj8vG7k90gueui3ZRioemIK1efdJOTCYGbDw=";
    };
    nativeBuildInputs = with pyPackages; [
      hatchling
    ];
    propagatedBuildInputs = with pyPackages; [
      opentelemetry-api
      opentelemetry-instrumentation
      opentelemetry-semantic-conventions
      wrapt
    ];
    doCheck = false;
    dontCheckRuntimeDeps = true;
  };

  opentelemetry_instrumentation_httpx = pyPackages.buildPythonPackage {
    pname = "opentelemetry-instrumentation-httpx";
    version = "0.61b0";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "opentelemetry_instrumentation_httpx";
      version = "0.61b0";
      hash = "sha256-ZWnsCXlGxVUcKkJS90yYZmrd0b8EfB3ea070JnGf+N0=";
    };
    nativeBuildInputs = with pyPackages; [
      hatchling
    ];
    propagatedBuildInputs = with pyPackages; [
      httpx
      opentelemetry-api
      opentelemetry-instrumentation
      opentelemetry-semantic-conventions
      opentelemetry-util-http
      wrapt
    ];
    doCheck = false;
    dontCheckRuntimeDeps = true;
  };

}
