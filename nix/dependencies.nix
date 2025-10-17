# Custom package dependencies for speaches
{
  pkgs,
  pyPackages,
  system,
}: rec {
  # Simplified espeakng-loader inline
  espeakng_loader = pyPackages.buildPythonPackage {
    pname = "espeakng_loader";
    version = "0.1.0";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "thewh1teagle";
      repo = "espeakng-loader";
      rev = "main";
      hash = "sha256-nSEQ9rofFl6BTH18L5DzaQ1Ymw5H3d+wSEXUxp4o1DM=";
    };
    nativeBuildInputs = [pyPackages.hatchling];
    propagatedBuildInputs = [pkgs.espeak-ng];
    postPatch = ''
      substituteInPlace src/espeakng_loader/__init__.py \
        --replace-fail 'libespeak-ng' '${pkgs.espeak-ng}/lib/libespeak-ng' \
        --replace-fail "Path(__file__).parent / 'espeak-ng-data'" "Path('${pkgs.espeak-ng}/share/espeak-ng-data')"
    '';
    nativeCheckInputs = [pyPackages.pytest];
  };

  kokoro_onnx = pyPackages.buildPythonPackage rec {
    pname = "kokoro_onnx";
    version = "0.4.9-git";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "thewh1teagle";
      repo = "kokoro-onnx";
      rev = "main";
      hash = "sha256-lTuCaDN+xi0gtnLfyAiShiLSS9ApAVU05BspezLq91A=";
    };
    nativeBuildInputs = with pyPackages; [
      hatchling
      hatch-vcs
    ];
    propagatedBuildInputs = with pyPackages;
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
    pythonImportsCheck = ["kokoro_onnx"];
  };

  # Utility packages
  pytest_antilru = pyPackages.buildPythonPackage {
    pname = "pytest_antilru";
    version = "2.0.0";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "pytest_antilru";
      version = "2.0.0";
      hash = "sha256-SM/zQmSLahzk5TmM8gOWaQXVRrPyvue7VdfLPsh6hfs=";
    };
    nativeBuildInputs = [pyPackages.poetry-core];
    propagatedBuildInputs = [pyPackages.pytest];
  };

  phonemizer_fork = pyPackages.buildPythonPackage {
    pname = "phonemizer-fork";
    version = "3.3.2";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/64/f1/0dcce21b0ae16a82df4b6583f8f3ad8e55b35f7e98b6bf536a4dd225fa08/phonemizer_fork-3.3.2-py3-none-any.whl";
      hash = "sha256-lzBcdvQYOzgl2uj0wDImX+eMmUbOWMR9S2IWE0kmS3Q=";
    };
    propagatedBuildInputs = with pyPackages; [
      joblib
      segments
      attrs
      (dlinfo.overridePythonAttrs (old: {
        doCheck = false;
      }))
    ];
    doCheck = false;
  };

  onnx_asr = pyPackages.buildPythonPackage rec {
    pname = "onnx_asr";
    version = "0.7.0";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "onnx_asr";
      version = "0.7.0";
      hash = "sha256-iWRsH4ik2MCdYxmvE9xvLD+FkG0Qg7AnqQsjtNOVMUI=";
    };
    nativeBuildInputs = with pyPackages; [pdm-backend];
    propagatedBuildInputs = with pyPackages; [
      numpy
      onnxruntime
      huggingface-hub
    ];
    pythonImportsCheck = ["onnx_asr"];
  };

  # onnx-diarization and its dependencies
  kaldi_native_fbank = pyPackages.buildPythonPackage {
    pname = "kaldi_native_fbank";
    version = "1.22.3";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/84/90/01ef7331c52b1eaf9916f3f7a535155aac2e9e2ddad12a141613d92758c7/kaldi_native_fbank-1.22.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl";
      hash = "sha256-8W50Ny/p4gq7QYP5io4iiNXuTEjQTZS2FgMRFw4AdmE=";
    };
    propagatedBuildInputs = with pyPackages; [numpy];
    doCheck = false;
  };

  onnx_dl = pyPackages.buildPythonPackage {
    pname = "onnx_dl";
    version = "0.1.0";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/3b/14/4ebec13075d24ba4f2d5ae1c2ad0bd62e56c90c5ef474d5357aa2c79f761/onnx_dl-0.1.0-py3-none-any.whl";
      hash = "sha256-QTYimCMcjT2qpw51FZUJ/bj2nH4CLR2P0y23pJLesf4=";
    };
    propagatedBuildInputs = with pyPackages; [onnxruntime];
    doCheck = false;
  };

  onnx_diarization = pyPackages.buildPythonPackage {
    pname = "onnx_diarization";
    version = "0.1.0";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/72/e7/15966d1f468f90c40d6f47966c1aa6661bbeb4a53c4590341935182e7c44/onnx_diarization-0.1.0-py3-none-any.whl";
      hash = "sha256-TmrUIKK8XJylGAIIAAh/30duhNHrmPf5VrS/Gl3XDyo=";
    };
    propagatedBuildInputs = with pyPackages;
      [
        scipy
        scikit-learn
        onnxruntime
        einops
        pyannote-core
      ]
      ++ [
        kaldi_native_fbank
        onnx_dl
      ];
    doCheck = false;
  };

  # Piper TTS packages (x86_64-linux only)
  isLinuxX86 = system == "x86_64-linux";

  piper_phonemize =
    if isLinuxX86
    then pyPackages.piper-phonemize
    else null;

  piper_tts = let
    espeak-ng' = pkgs.espeak-ng.override {
      asyncSupport = false;
      klattSupport = false;
      mbrolaSupport = false;
      pcaudiolibSupport = false;
      sonicSupport = false;
      speechPlayerSupport = false;
    };
  in
    if pkgs.stdenv.isLinux && isLinuxX86
    then
      pyPackages.buildPythonPackage rec {
        pname = "piper-tts";
        version = "1.3.0";
        pyproject = true;

        src = pkgs.fetchFromGitHub {
          owner = "OHF-Voice";
          repo = "piper1-gpl";
          tag = "v${version}";
          hash = "sha256-WDMIXsbUzJ5XnA/KUVUPQKZzkqrXagzAOrhFtLR4fGk=";
        };

        patches = [./piper-tts-cmake-system-libs.patch];

        build-system = with pyPackages; [
          cmake
          ninja
          scikit-build
          setuptools
        ];

        nativeBuildInputs = [pkgs.pkg-config];

        dontUseCmakeConfigure = true;

        env.CMAKE_ARGS = builtins.toString [
          (pkgs.lib.cmakeFeature "UCD_STATIC_LIB" "${espeak-ng'.ucd-tools}/libucd.a")
        ];

        buildInputs = [espeak-ng'];

        dependencies = [pyPackages.onnxruntime];

        postInstall = ''
          ln -s ${espeak-ng'}/share/espeak-ng-data $out/${pyPackages.python.sitePackages}/piper/
        '';

        pythonImportsCheck = ["piper"];
      }
    else null;

  # OpenTelemetry instrumentation packages
  opentelemetry_instrumentation_openai = pyPackages.buildPythonPackage {
    pname = "opentelemetry_instrumentation_openai";
    version = "0.37.1";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "opentelemetry_instrumentation_openai";
      version = "0.37.1";
      hash = "sha256-SoS5lXJMoE7TvOltnmI2/2M4EGGU74rkwZMFsFEeRpw=";
    };
    nativeBuildInputs = with pyPackages; [
      hatchling
      poetry-core
    ];
    propagatedBuildInputs = with pyPackages; [
      openai
      opentelemetry-api
      opentelemetry-instrumentation
      opentelemetry-semantic-conventions
      typing-extensions
      wrapt
    ];
    dontCheckRuntimeDeps = true;
  };

  opentelemetry_instrumentation_openai_v2 = pyPackages.buildPythonPackage {
    pname = "opentelemetry_instrumentation_openai_v2";
    version = "2.1b0";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "opentelemetry_instrumentation_openai_v2";
      version = "2.1b0";
      hash = "sha256-GEqV+Ewo9Xn7zXixtULTz3XmvR3Jw7jHvkeGoZzbrxM=";
    };
    nativeBuildInputs = with pyPackages; [
      hatchling
      poetry-core
    ];
    propagatedBuildInputs = with pyPackages; [
      openai
      opentelemetry-api
      opentelemetry-instrumentation
      opentelemetry-semantic-conventions
      httpx
      wrapt
    ];
    pythonImportsCheck = ["opentelemetry.instrumentation.openai_v2"];
  };
}
