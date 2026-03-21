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
    # No test suite in repository
    doCheck = false;
    pythonImportsCheck = [ "espeakng_loader" ];
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
    # No test suite in repository
    doCheck = false;
    pythonImportsCheck = [ "kokoro_onnx" ];
  };

  # Simplified aiortc-related packages
  aioice = pyPackages.buildPythonPackage {
    pname = "aioice";
    version = "0.10.2";
    format = "setuptools";
    src = pkgs.fetchFromGitHub {
      owner = "aiortc";
      repo = "aioice";
      tag = "0.10.2";
      hash = "sha256-UEXkTxcpe6mlA2FmMSfDmtcEYE9zwuitpi2Eh188xZc=";
    };
    propagatedBuildInputs = with pyPackages; [
      dnspython
      ifaddr
    ];
    nativeCheckInputs = [ pyPackages.pytestCheckHook ];
    disabledTestPaths = [
      "tests/test_ice.py"
      "tests/test_mdns.py"
      "tests/test_turn.py"
      "tests/test_ice_trickle.py"
    ];
  };

  pylibsrtp = pyPackages.buildPythonPackage {
    pname = "pylibsrtp";
    version = "1.0.0";
    format = "setuptools";
    src = pkgs.fetchFromGitHub {
      owner = "aiortc";
      repo = "pylibsrtp";
      tag = "1.0.0";
      hash = "sha256-Q8EyGAJKkq14sqSEMWLB8arKvj/wuALK/XwOZ27F1nQ=";
    };
    nativeBuildInputs = [ pyPackages.cffi ];
    buildInputs = [
      pkgs.srtp
      pkgs.openssl
    ];
    propagatedBuildInputs = [ pyPackages.cffi ];
    nativeCheckInputs = [ pyPackages.pytestCheckHook ];
  };

  aiortc = pyPackages.buildPythonPackage rec {
    pname = "aiortc";
    version = "1.14.0";
    format = "setuptools";
    src = pkgs.fetchFromGitHub {
      owner = "aiortc";
      repo = "aiortc";
      tag = "1.14.0";
      hash = "sha256-ZgxSaiKkJrA5XvUT1zq8kwqB8mOvn46vLWXHyJSsHbM=";
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
    nativeCheckInputs = with pyPackages; [
      numpy
      pytestCheckHook
    ];
    disabledTestPaths = [
      "tests/test_ortc.py"
      "tests/test_rtcicetransport.py"
      "tests/test_rtcpeerconnection.py"
      "tests/test_contrib_signaling.py"
    ];
  };

  # Override csvw to avoid frictionless -> moto -> sagemaker -> google-pasta
  # dependency chain which is broken on Python 3.14+.
  # nixpkgs' csvw incorrectly includes frictionless as a runtime dep (it is only a test dep).
  csvw = pyPackages.buildPythonPackage {
    pname = "csvw";
    version = "3.7.0";
    format = "setuptools";
    src = pkgs.fetchFromGitHub {
      owner = "cldf";
      repo = "csvw";
      tag = "v3.7.0";
      hash = "sha256-HftvI4xJy/MX0WTIFNyZqNqIJIlHsWhhURpeQ1XqrT0=";
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
    nativeCheckInputs = with pyPackages; [
      pytestCheckHook
      pytest-cov-stub
      pytest-mock
      requests-mock
    ];
    disabledTests = [ "test_write_file_exists" ];
    disabledTestPaths = [ "tests/test_conformance.py" ];
    dontCheckRuntimeDeps = true;
  };

  # Override segments to use our csvw (without frictionless)
  segments = pyPackages.buildPythonPackage {
    pname = "segments";
    version = "2.4.0";
    format = "setuptools";
    src = pkgs.fetchFromGitHub {
      owner = "cldf";
      repo = "segments";
      tag = "v2.4.0";
      hash = "sha256-XhJH87Bb9wGNPpPymRjgPYLv2zr4hGAyIAbTMk0uCU0=";
    };
    nativeBuildInputs = [ pyPackages.setuptools ];
    propagatedBuildInputs = with pyPackages; [
      regex
      csvw
    ];
    nativeCheckInputs = with pyPackages; [
      pytestCheckHook
      pytest-cov-stub
      pytest-mock
    ];
    dontCheckRuntimeDeps = true;
  };

  phonemizer_fork = pyPackages.buildPythonPackage {
    pname = "phonemizer-fork";
    version = "3.3.2-git";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "thewh1teagle";
      repo = "phonemizer-fork";
      rev = "2d74b9863f48f98557f3605fdb434c928629861d"; # dev branch; no tags published
      hash = "sha256-0exVEQgi/+L9V0h+K9lUaWICtmILRnb//izGyjOVID0=";
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
    nativeCheckInputs = with pyPackages; [
      pytestCheckHook
      pytest-cov
    ];
    checkInputs = [ pkgs.espeak-ng ];
    preCheck = ''
      export PATH="${pkgs.espeak-ng}/bin:$PATH"
      export ESPEAK_DATA_PATH="${pkgs.espeak-ng}/share/espeak-ng-data"
    '';
    disabledTestPaths = [
      # festival backend is not available in Nix
      "test/test_festival.py"
    ];
    disabledTests = [
      # tests that specifically test the festival backend
      "test_festival"
    ];
  };

  # onnx_asr has a custom hatch build hook that generates .onnx preprocessor models
  # at build time using torch + onnxscript. These are deterministic signal processing
  # graphs (FFT, mel filterbanks, resamplers), not trained weights.
  onnx_asr = pyPackages.buildPythonPackage {
    pname = "onnx_asr";
    version = "0.10.2";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "istupakov";
      repo = "onnx-asr";
      tag = "v0.10.2";
      hash = "sha256-KumdelY9oNMAEBSGVdvbBH6SYi93n2cA/eEqaE8MmIU=";
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
    nativeCheckInputs = with pyPackages; [
      pytestCheckHook
      pytest-cov
    ];
    disabledTestPaths = [
      # Requires downloading models from HuggingFace
      "tests/onnx_asr/test_recognize.py"
      "tests/onnx_asr/test_cli.py"
      "tests/onnx_asr/test_load_model_errors.py"
      # Requires pre-built ONNX preprocessor models
      "tests/preprocessors"
    ];
  };

  # onnx-diarization and its dependencies
  einops = pyPackages.buildPythonPackage {
    pname = "einops";
    version = "0.8.2";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "arogozhnikov";
      repo = "einops";
      tag = "v0.8.2";
      hash = "sha256-d5Vbtkw/MChS2j2IC6j97wfVoKWZT9mU4OeXyEjm6ys=";
    };
    nativeBuildInputs = [ pyPackages.hatchling ];
    env.EINOPS_TEST_BACKENDS = "numpy";
    nativeCheckInputs = with pyPackages; [
      numpy
      parameterized
      pytestCheckHook
    ];
    disabledTestPaths = [
      # notebook samples depend on large packages or accelerator access
      "scripts/"
    ];
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
      # No Python test suite; C++ tests require separate cmake build
      doCheck = false;
      pythonImportsCheck = [ "kaldi_native_fbank" ];
    };

  onnx_dl = pyPackages.buildPythonPackage {
    pname = "onnx_dl";
    version = "0.1.0-git";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "fedirz";
      repo = "onnx-dl";
      rev = "9ef51fc5e980"; # init commit; no tags published
      hash = "sha256-tWkxIFLhTFhfNsQkkvfXWdPD1f75wxX9sBFWvV6PGX8=";
    };
    nativeBuildInputs = [ pyPackages.uv-build ];
    postPatch = ''
      sed -i 's/requires = \["uv_build[^"]*"\]/requires = ["uv_build"]/' pyproject.toml
    '';
    propagatedBuildInputs = with pyPackages; [ onnxruntime ];
    # Tests require pre-cached HuggingFace models
    doCheck = false;
    pythonImportsCheck = [ "onnx_dl" ];
  };

  pyannote_core = pyPackages.buildPythonPackage {
    pname = "pyannote_core";
    version = "6.0.1";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "pyannote";
      repo = "pyannote-core";
      tag = "6.0.1";
      hash = "sha256-r5NkOAzrQGcb6LPi4/DA0uT9R0ELiYuwQkbT1l6R8Mw=";
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
    nativeCheckInputs = [ pyPackages.pytestCheckHook ];
    pythonImportsCheck = [ "pyannote.core" ];
  };

  onnx_diarization = pyPackages.buildPythonPackage {
    pname = "onnx_diarization";
    version = "0.1.0-git";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "fedirz";
      repo = "onnx-diarization";
      rev = "9662cf34bb16"; # init commit; no tags published
      hash = "sha256-3tkgVEqVyOHFYKssJPkSS51eUx9Tw2a2p3jVI2pkQOI=";
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
    # All tests require pre-cached models or network access
    doCheck = false;
    pythonImportsCheck = [ "onnx_diarization" ];
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

        nativeCheckInputs = [ pyPackages.pytestCheckHook ];
        preCheck = ''
          export PATH="${espeak-ng'}/bin:$PATH"
          export ESPEAK_DATA_PATH="${espeak-ng'}/share/espeak-ng-data"
        '';
        disabledTestPaths = [
          # Requires libtashkeel ONNX model download
          "tests/test_tashkeel.py"
        ];
      }
    else
      null;

  # OpenTelemetry instrumentation packages (from monorepo)
  otelContribSrc = pkgs.fetchFromGitHub {
    owner = "open-telemetry";
    repo = "opentelemetry-python-contrib";
    tag = "v0.61b0";
    hash = "sha256-DT13gcYPNYXBPnf622WsA16C+7sabJfOshDquHn06Ok=";
  };

  opentelemetry_instrumentation_asyncio = pyPackages.buildPythonPackage {
    pname = "opentelemetry-instrumentation-asyncio";
    version = "0.61b0";
    format = "pyproject";
    src = "${otelContribSrc}/instrumentation/opentelemetry-instrumentation-asyncio";
    nativeBuildInputs = with pyPackages; [
      hatchling
    ];
    propagatedBuildInputs = with pyPackages; [
      opentelemetry-api
      opentelemetry-instrumentation
      opentelemetry-semantic-conventions
      wrapt
    ];
    # Tests require opentelemetry-test-utils which is not in nixpkgs
    doCheck = false;
    pythonImportsCheck = [ "opentelemetry.instrumentation.asyncio" ];
    dontCheckRuntimeDeps = true;
  };

  opentelemetry_instrumentation_httpx = pyPackages.buildPythonPackage {
    pname = "opentelemetry-instrumentation-httpx";
    version = "0.61b0";
    format = "pyproject";
    src = "${otelContribSrc}/instrumentation/opentelemetry-instrumentation-httpx";
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
    # Tests require opentelemetry-test-utils and respx which are not in nixpkgs
    doCheck = false;
    pythonImportsCheck = [ "opentelemetry.instrumentation.httpx" ];
    dontCheckRuntimeDeps = true;
  };

}
