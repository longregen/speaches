# Custom package dependencies for speaches
{
  pkgs,
  pyPackages,
  system,
}:
rec {
  # test_bufsize fails in the Nix sandbox (fcntl F_SETPIPE_SZ blocked)
  wurlitzer = pyPackages.wurlitzer.overrideAttrs (old: {
    disabledTests = (old.disabledTests or [ ]) ++ [ "test_bufsize" ];
  });

  # Simplified espeakng-loader inline
  espeakng_loader = pyPackages.buildPythonPackage {
    pname = "espeakng_loader";
    version = "0.1.0";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "thewh1teagle";
      repo = "espeakng-loader";
      rev = "0ddc87adf77e5850d7eeb542ac8a87d421b64daa";
      hash = "sha256-nSEQ9rofFl6BTH18L5DzaQ1Ymw5H3d+wSEXUxp4o1DM=";
    };
    nativeBuildInputs = [ pyPackages.hatchling ];
    propagatedBuildInputs = [ pkgs.espeak-ng ];
    postPatch = ''
      substituteInPlace src/espeakng_loader/__init__.py \
        --replace-fail 'libespeak-ng' '${pkgs.espeak-ng}/lib/libespeak-ng' \
        --replace-fail "Path(__file__).parent / 'espeak-ng-data'" "Path('${pkgs.espeak-ng}/share/espeak-ng-data')"
    '';
  };

  kokoro_onnx = pyPackages.buildPythonPackage rec {
    pname = "kokoro_onnx";
    version = "0.4.9-git";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "thewh1teagle";
      repo = "kokoro-onnx";
      rev = "2bfb160cfae06709a6d7c3d436293972e0f1d12f";
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
    nativeCheckInputs = with pyPackages; [ pytestCheckHook ];
    # Network-dependent tests: mDNS needs multicast device, ICE/TURN need STUN/TURN servers
    disabledTestPaths = [
      "tests/test_mdns.py"
      "tests/test_ice_trickle.py"
    ];
    disabledTests = [
      # test_ice.py — require network for ICE negotiation / STUN / TURN
      "test_add_remote_candidate_mdns_bad"
      "test_add_remote_candidate_mdns_good"
      "test_connect"
      "test_connect_early_checks"
      "test_connect_early_checks_2"
      "test_connect_invalid_password"
      "test_connect_ipv6"
      "test_connect_reverse_order"
      "test_connect_role_conflict_both_controlled"
      "test_connect_role_conflict_both_controlling"
      "test_connect_to_ice_lite"
      "test_connect_two_components"
      "test_connect_two_components_vs_one_component"
      "test_connect_with_stun_server"
      "test_connect_with_stun_server_dns_lookup_error"
      "test_connect_with_stun_server_ipv6"
      "test_connect_with_stun_server_timeout"
      "test_connect_with_turn_server_tcp"
      "test_connect_with_turn_server_udp"
      "test_connect_with_turn_server_udp_auth_failed"
      "test_connect_with_turn_server_udp_timeout"
      "test_consent_expired"
      "test_consent_valid"
      "test_gather_candidates_relay_only_with_stun_server"
      "test_gather_candidates_relay_only_with_turn_server"
      "test_recv_connection_lost"
      "test_set_selected_pair"
      # test_turn.py — require TURN server network access
      "test_tcp_transport"
      "test_tls_transport"
      "test_udp_transport"
    ];
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
    nativeCheckInputs = with pyPackages; [ pytestCheckHook ];
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
    nativeCheckInputs = with pyPackages; [
      pytestCheckHook
      numpy
    ];
    # WebRTC connection/transport tests hang in sandbox (ICE candidate gathering needs network)
    disabledTestPaths = [
      "tests/test_ortc.py"
      "tests/test_rtcpeerconnection.py"
      "tests/test_rtcsctptransport.py"
      "tests/test_rtcdtlstransport.py"
      "tests/test_rtcicetransport.py"
      "tests/test_rtcrtpreceiver.py"
      "tests/test_rtcrtpsender.py"
      "tests/test_rtcrtptransceiver.py"
    ];
    # macOS sandbox doesn't allow binding to low-numbered ports
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
      dlinfo
    ];
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
    nativeBuildInputs = with pyPackages; [ pdm-backend ];
    propagatedBuildInputs = with pyPackages; [
      numpy
      onnxruntime
      huggingface-hub
    ];
    # All tests require torch (not a runtime dep) or network (model downloads from HuggingFace)
    doCheck = false;
  };

  isLinuxX86 = system == "x86_64-linux";

  # onnx-diarization and its dependencies
  einops = pyPackages.buildPythonPackage {
    pname = "einops";
    version = "0.8.2";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/2a/09/f8d8f8f31e4483c10a906437b4ce31bdf3d6d417b73fe33f1a8b59e34228/einops-0.8.2-py3-none-any.whl";
      hash = "sha256-VAWCAaxwh5ERgb/sSvYJG7WTgDYPBpJ2YBJWp2rwgZM=";
    };
  };

  kaldi_native_fbank = pyPackages.buildPythonPackage {
    pname = "kaldi_native_fbank";
    version = "1.22.3";
    format = "wheel";
    src =
      {
        x86_64-linux = {
          "3.14" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/2b/6a/374ec4e1cf13e672f5acd8272116c1885c2a7f84be491fc652415fc6e870/kaldi_native_fbank-1.22.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl";
            hash = "sha256-8cwrju7FKjOGjPWbuV1AszX6nP9+FaYgjg6bZ7f9cjY=";
          };
          "3.13" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/bc/1e/496c7ae814b2a7f8f47d423dc33aae2cdfb1edf898e2faaf5c5b39b90363/kaldi_native_fbank-1.22.3-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl";
            hash = "sha256-4/nGVR/1tq54XdFfgZw7K3Qy13v7eeqIBnSOLH2QC10=";
          };
          "3.12" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/84/90/01ef7331c52b1eaf9916f3f7a535155aac2e9e2ddad12a141613d92758c7/kaldi_native_fbank-1.22.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl";
            hash = "sha256-8W50Ny/p4gq7QYP5io4iiNXuTEjQTZS2FgMRFw4AdmE=";
          };
        };
        aarch64-linux = {
          "3.14" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/d6/4b/1f3f17a7b601124df88112a1d1fcb543c8d908d6674f752f7d3322991770/kaldi_native_fbank-1.22.3-cp314-cp314-manylinux2014_aarch64.manylinux_2_17_aarch64.whl";
            hash = "sha256-QftQb94VXZeu75XdbOzMOMLF3UQB+bj97Zusrxuv7zY=";
          };
          "3.13" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/9a/72/adb11d27c545aca1db442da744ee430a6aae377a33574bfd2ec159dcf673/kaldi_native_fbank-1.22.3-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.whl";
            hash = "sha256-90uFlIMoq0tMiFIvmKWfg91SlUQ7CEg+lFx94sNeXcw=";
          };
          "3.12" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/43/28/6f4fd8953c0b3f30de4526fd024095032abcdc25b6736c77a891687c604e/kaldi_native_fbank-1.22.3-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl";
            hash = "sha256-9aRLSoPPm/E9P3eFiSgGiwbT7CI4wn/y45OT+/d0nJ8=";
          };
        };
        x86_64-darwin = {
          "3.14" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/b9/7e/d47f64d5332b2527e6b65490888d99793eb3280bca735d0b69348eaeb6a3/kaldi_native_fbank-1.22.3-cp314-cp314-macosx_10_15_x86_64.whl";
            hash = "sha256-LvqDaM3UajLDeijEuqpQiwopSrHKKu/d0+l/Ys/rwns=";
          };
          "3.13" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/0d/df/4110f685067946c8b2e59ed76cebdf51c979ae999d90f65208a9d1966cba/kaldi_native_fbank-1.22.3-cp313-cp313-macosx_10_15_x86_64.whl";
            hash = "sha256-eMoWNoakqhaTGU0JiqebUXhF2FGqb9J9WxYsBeEBI2E=";
          };
          "3.12" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/c2/de/fbdbfcc75fad9d9a6f9a250bc986f1002902581eaa47a5948f53a7f11851/kaldi_native_fbank-1.22.3-cp312-cp312-macosx_10_15_x86_64.whl";
            hash = "sha256-f2NszeoovRh/k7BqHkuSdeQuQ6+UBbBoT8c56CkpnEs=";
          };
        };
        aarch64-darwin = {
          "3.14" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/78/9f/f98f72ba5a90a39675e82f2175dc5ec99a85892a88b9ccdd25f2dc916c82/kaldi_native_fbank-1.22.3-cp314-cp314-macosx_11_0_arm64.whl";
            hash = "sha256-j2CGBz7GWKI9Ivhlez7oxrpp1lvlcySnKEIJrHQktaw=";
          };
          "3.13" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/d8/74/ef21aabdd2f32539735e2ed4d3ea072112d4e3d30dfc2d17695f6d9df072/kaldi_native_fbank-1.22.3-cp313-cp313-macosx_11_0_arm64.whl";
            hash = "sha256-N2jqmZM6olCAy4IPk/e2EpaGM7mk+iO8inM34hN/P7s=";
          };
          "3.12" = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/77/64/e57ce185dda028b7b9af72cdfb16825bfa52183653945681e7cb8e7c2dfa/kaldi_native_fbank-1.22.3-cp312-cp312-macosx_11_0_arm64.whl";
            hash = "sha256-q9Mai/4dtip92wvu6E86Xem7VZ/N0rlsoPtynFUblBI=";
          };
        };
      }
      .${system}.${pyPackages.python.pythonVersion};
    propagatedBuildInputs = with pyPackages; [ numpy ];
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
  };

  # Piper TTS packages — built from source for cross-platform support

  piper_phonemize = pyPackages.buildPythonPackage {
    pname = "piper_phonemize";
    version = "1.2.0";
    format = "pyproject";
    src = pkgs.fetchFromGitHub {
      owner = "fedirz";
      repo = "piper-phonemize";
      rev = "8a88b53a8f3fc1080058d2ced3e14222f9ce2e87";
      hash = "sha256-6TTkVn+WJkVdlGCT4oAIGm+2Hvp9IbY+WZxNXSO/P0k=";
    };
    nativeBuildInputs = with pyPackages; [
      setuptools
      pybind11
    ];
    buildInputs = [
      pkgs.espeak-ng
      pkgs.onnxruntime
    ];
    postPatch = ''
      # Remove pre-built wheels from source to avoid double-install
      rm -rf dist/

      substituteInPlace setup.py \
        --replace-fail 'str(_ESPEAK_DIR / "include")' '"${pkgs.espeak-ng}/include"' \
        --replace-fail 'str(_ESPEAK_DIR / "lib")' '"${pkgs.espeak-ng}/lib"' \
        --replace-fail 'str(_ONNXRUNTIME_DIR / "include")' '"${pkgs.onnxruntime.dev}/include"' \
        --replace-fail 'str(_ONNXRUNTIME_DIR / "lib")' '"${pkgs.onnxruntime}/lib"'

      # setup.py expects espeak-ng-data in the package directory
      cp -r ${pkgs.espeak-ng}/share/espeak-ng-data piper_phonemize/

      # setup.py references libtashkeel_model.ort at source root
      cp etc/libtashkeel_model.ort .
    '';
    doCheck = false;
    dontCheckRuntimeDeps = true;
  };

  piper_tts = pyPackages.buildPythonPackage {
    pname = "piper_tts";
    version = "1.4.1";
    format = "wheel";
    src =
      {
        x86_64-linux = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/39/42/b44ae16ef80d86173518aafe2a493a826b46f9fe4fba1b82cd575117d5ac/piper_tts-1.4.1-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl";
          hash = "sha256-WqUzNkwVJI0pMrzDYusHQN580o3DQjPejfLuPG8q3wA=";
        };
        aarch64-linux = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/df/95/c4c4163cf0f636eec0ccb3adc63c70fb74334ff28e41e176f0ca0415496e/piper_tts-1.4.1-cp39-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.manylinux_2_28_aarch64.whl";
          hash = "sha256-PbyZC04oxoCkTibceogLPhBo4G/8He7MhpCSmJX/sAU=";
        };
        x86_64-darwin = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/2b/1c/e9a6695e19aa5c80b3ac4f70dd432fe3dcf99519458ad149f73af5b0fa44/piper_tts-1.4.1-cp39-abi3-macosx_10_9_x86_64.whl";
          hash = "sha256-dkZ986vgoN2NU+Tn12nOsWaXlucYiVQYIle+TPed2uA=";
        };
        aarch64-darwin = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/e4/56/633c64944a9ae13d5183989de1519e5eb30e5e6b668942d97ca03b04c53a/piper_tts-1.4.1-cp39-abi3-macosx_11_0_arm64.whl";
          hash = "sha256-qZ2TousoBapwWZlgafhEjIbOdwQgDsC/n5CZ8DVJTcc=";
        };
      }
      .${system};
    propagatedBuildInputs = with pyPackages; [ onnxruntime ];
    dontCheckRuntimeDeps = true;
  };

  # OpenTelemetry instrumentation packages
  opentelemetry_instrumentation_asyncio = pyPackages.buildPythonPackage {
    pname = "opentelemetry_instrumentation_asyncio";
    version = "0.61b0";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/58/8f/79913d7ebc2bd2be9a81f8ecbe0f7413c3bec55c83c89337b93c8de5417a/opentelemetry_instrumentation_asyncio-0.61b0-py3-none-any.whl";
      hash = "sha256-Qyc9W3SICwbFp2b3efpIClD8WgmnyBRopgRXt5Tj880=";
    };
    propagatedBuildInputs = with pyPackages; [
      opentelemetry-api
      opentelemetry-instrumentation
      wrapt
    ];
    dontCheckRuntimeDeps = true;
    pythonImportsCheck = [ ];
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
    # Tests import openai.resources.chat.completions.ChatCompletion which was removed in openai >=2.x
    doCheck = false;
    dontCheckRuntimeDeps = true;
    pythonImportsCheck = [ ];
  };
}
