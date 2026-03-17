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
      rev = "main";
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
      rev = "main";
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
    nativeBuildInputs = [ pyPackages.poetry-core ];
    propagatedBuildInputs = [ pyPackages.pytest ];
    doCheck = false;
  };

  # Override csvw to avoid frictionless -> moto -> sagemaker -> google-pasta
  # dependency chain which is broken on Python 3.14+.
  # nixpkgs' csvw incorrectly includes frictionless as a runtime dep (it is only a test dep).
  csvw = pyPackages.buildPythonPackage {
    pname = "csvw";
    version = "3.7.0";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/80/cb/19e8e582fc164db200c18078bdbdcc60c012cb83c7f02ea8e876bc0b1adf/csvw-3.7.0-py2.py3-none-any.whl";
      hash = "sha256-IbiNtQo16UDUtc3Y86gIRJOtfxuxZX7XMjqtl3NZlA4=";
    };
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
    version = "2.3.0";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/11/18/cb614939ccd46d336013cab705f1e11540ec9c68b08ecbb854ab893fc480/segments-2.3.0-py2.py3-none-any.whl";
      hash = "sha256-MKVlZ4cHFDDNIkIuBHE7KpvqvhqX0uvzf3FqVvkFd6M=";
    };
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
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/64/f1/0dcce21b0ae16a82df4b6583f8f3ad8e55b35f7e98b6bf536a4dd225fa08/phonemizer_fork-3.3.2-py3-none-any.whl";
      hash = "sha256-lzBcdvQYOzgl2uj0wDImX+eMmUbOWMR9S2IWE0kmS3Q=";
    };
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

  onnx_asr = pyPackages.buildPythonPackage {
    pname = "onnx_asr";
    version = "0.10.2";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/a6/58/951afd8d9c3ec3f67c2b6915bea25a2ce6c2fd9b482c7449bc3f21f9cdcb/onnx_asr-0.10.2-py3-none-any.whl";
      hash = "sha256-uzaw60e6SWtw+w6FaGNITcUZ0npJfu4ULcolGaUJCZU=";
    };
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
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/2a/09/f8d8f8f31e4483c10a906437b4ce31bdf3d6d417b73fe33f1a8b59e34228/einops-0.8.2-py3-none-any.whl";
      hash = "sha256-VAWCAaxwh5ERgb/sSvYJG7WTgDYPBpJ2YBJWp2rwgZM=";
    };
    doCheck = false;
  };

  kaldi_native_fbank =
    let
      pythonVersion = pyPackages.python.pythonVersion;
      wheelSrc = {
        "3.12" = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/84/90/01ef7331c52b1eaf9916f3f7a535155aac2e9e2ddad12a141613d92758c7/kaldi_native_fbank-1.22.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl";
          hash = "sha256-8W50Ny/p4gq7QYP5io4iiNXuTEjQTZS2FgMRFw4AdmE=";
        };
        "3.13" = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/bc/1e/496c7ae814b2a7f8f47d423dc33aae2cdfb1edf898e2faaf5c5b39b90363/kaldi_native_fbank-1.22.3-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl";
          hash = "sha256-4/nGVR/1tq54XdFfgZw7K3Qy13v7eeqIBnSOLH2QC10=";
        };
        "3.14" = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/2b/6a/374ec4e1cf13e672f5acd8272116c1885c2a7f84be491fc652415fc6e870/kaldi_native_fbank-1.22.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl";
          hash = "sha256-8cwrju7FKjOGjPWbuV1AszX6nP9+FaYgjg6bZ7f9cjY=";
        };
      }.${pythonVersion} or (throw "kaldi_native_fbank: unsupported Python ${pythonVersion}");
    in
    pyPackages.buildPythonPackage {
      pname = "kaldi_native_fbank";
      version = "1.22.3";
      format = "wheel";
      src = wheelSrc;
      propagatedBuildInputs = with pyPackages; [ numpy ];
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
    propagatedBuildInputs = with pyPackages; [ onnxruntime ];
    doCheck = false;
  };

  pyannote_core = pyPackages.buildPythonPackage {
    pname = "pyannote_core";
    version = "6.0.1";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/ea/57/ecf62344b9b81debd0ca95ed987135e93d1b039507f8174f52d1d19d8c6b/pyannote_core-6.0.1-py3-none-any.whl";
      hash = "sha256-kkVQ1uz2sFrRO/P2b1nCn8dAzxxipvyoYKwuZpCCA+U=";
    };
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
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/72/e7/15966d1f468f90c40d6f47966c1aa6661bbeb4a53c4590341935182e7c44/onnx_diarization-0.1.0-py3-none-any.whl";
      hash = "sha256-TmrUIKK8XJylGAIIAAh/30duhNHrmPf5VrS/Gl3XDyo=";
    };
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

  # Piper TTS packages (x86_64-linux only)
  isLinuxX86 = system == "x86_64-linux";

  piper_phonemize =
    if isLinuxX86 then
      pyPackages.buildPythonPackage {
        pname = "piper_phonemize";
        version = "1.2.0";
        format = "wheel";
        src = pkgs.fetchurl {
          url = "https://github.com/fedirz/piper-phonemize/raw/refs/heads/master/dist/piper_phonemize-1.2.0-cp312-cp312-manylinux_2_28_x86_64.whl";
          hash = "sha256-E7/QdVBXIELF5t2NQAdr8kEBqTCvDHoSZUJyFydSJbM=";
        };
        doCheck = false;
      }
    else
      null;

  piper_tts =
    if pkgs.stdenv.isLinux && isLinuxX86 then
      pyPackages.buildPythonPackage {
        pname = "piper_tts";
        version = "1.4.1";
        format = "wheel";
        src = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/39/42/b44ae16ef80d86173518aafe2a493a826b46f9fe4fba1b82cd575117d5ac/piper_tts-1.4.1-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl";
          hash = "sha256-WqUzNkwVJI0pMrzDYusHQN580o3DQjPejfLuPG8q3wA=";
        };
        propagatedBuildInputs = [ piper_phonemize pyPackages.onnxruntime ];
        doCheck = false;
      }
    else
      null;

  # OpenTelemetry instrumentation packages
  opentelemetry_instrumentation_asyncio = pyPackages.buildPythonPackage {
    pname = "opentelemetry-instrumentation-asyncio";
    version = "0.55b0";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/82/71/64ed9dc18c278fd153a09af240c46dbbcf13244b76c256c9c6798c2faf1d/opentelemetry_instrumentation_asyncio-0.55b0-py3-none-any.whl";
      hash = "sha256-Mnj/iWSHfOFjiLuvZGV6pt+j5ewHF1WD7XINN89KVpE=";
    };
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
    version = "0.55b0";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/79/06/766bca20f82c9b47010fb838f753069141b32f1c84a5fa4c1abc97b63add/opentelemetry_instrumentation_httpx-0.55b0-py3-none-any.whl";
      hash = "sha256-EjqiS2GR/1xLgaFZpZqR+zKiG9NoglEQ0zuO3kcjhZc=";
    };
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

  opentelemetry_instrumentation_openai = pyPackages.buildPythonPackage {
    pname = "opentelemetry_instrumentation_openai";
    version = "0.53.0";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "opentelemetry_instrumentation_openai";
      version = "0.53.0";
      hash = "sha256-wM2D0iPRODCa88xfU8nG0iE2N0v6AOj2bf8xzTIu9Uc=";
    };
    nativeBuildInputs = with pyPackages; [
      hatchling
      poetry-core
    ];
    propagatedBuildInputs = with pyPackages; [
      opentelemetry-api
      opentelemetry-instrumentation
      opentelemetry-semantic-conventions
      typing-extensions
      wrapt
    ];
    doCheck = false;
    dontCheckRuntimeDeps = true;
    pythonImportsCheck = [ ];
  };

  opentelemetry_instrumentation_openai_v2 = pyPackages.buildPythonPackage {
    pname = "opentelemetry_instrumentation_openai_v2";
    version = "2.3b0";
    format = "pyproject";
    src = pyPackages.fetchPypi {
      pname = "opentelemetry_instrumentation_openai_v2";
      version = "2.3b0";
      hash = "sha256-XenXDMlTbuof5I6gFuDF8lc1+poTcJB2pksgZX+ttro=";
    };
    nativeBuildInputs = with pyPackages; [
      hatchling
      poetry-core
    ];
    propagatedBuildInputs = with pyPackages; [
      opentelemetry-api
      opentelemetry-instrumentation
      opentelemetry-semantic-conventions
      httpx
      wrapt
    ];
    doCheck = false;
    dontCheckRuntimeDeps = true;
    pythonImportsCheck = [ ];
  };
}
