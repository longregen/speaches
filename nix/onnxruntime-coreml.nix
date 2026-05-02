# Requires --impure: Apple frameworks not in nix store.
final: prev:
let
  inherit (prev) lib stdenv fetchFromGitHub;
  isDarwin = stdenv.isDarwin;

  # CoreML EP needs coremltools source for protobuf definitions.
  # onnxruntime 1.23.2 pins coremltools 7.1 in cmake/deps.txt.
  coremltools-src = fetchFromGitHub {
    owner = "apple";
    repo = "coremltools";
    tag = "7.1";
    hash = "sha256-kajQFHpl+4UK6fp+rM8TP0GiqIFYXPVFc2x1p19rBSw=";
  };

  # fp16 header-only library — needed by coremltools' MILBlob for float16 support.
  # Pinned to the revision in onnxruntime's cmake/deps.txt.
  # Patched: bump cmake_minimum_required (modern cmake rejects < 3.5)
  #          and disable tests/benchmarks (they pull in psimd/fxdiv/pthreadpool).
  fp16-src = prev.applyPatches {
    name = "fp16-src";
    src = prev.fetchzip {
      url = "https://github.com/Maratyszcza/FP16/archive/0a92994d729ff76a58f692d3028ca1b64b145d91.zip";
      hash = "sha256-m2d9bqZoGWzuUPGkd29MsrdscnJRtuIkLIMp3fMmtRY=";
    };
    postPatch = ''
      substituteInPlace CMakeLists.txt \
        --replace-fail \
          'CMAKE_MINIMUM_REQUIRED(VERSION 2.8.12 FATAL_ERROR)' \
          'CMAKE_MINIMUM_REQUIRED(VERSION 3.5 FATAL_ERROR)'

      # Disable tests, benchmarks, and the unconditional psimd subdirectory.
      # onnxruntime CoreML EP only uses fp16's headers (include/fp16.h).
      substituteInPlace CMakeLists.txt \
        --replace-fail 'OPTION(FP16_BUILD_TESTS "Build FP16 unit tests" ON)' \
                       'OPTION(FP16_BUILD_TESTS "Build FP16 unit tests" OFF)' \
        --replace-fail 'OPTION(FP16_BUILD_BENCHMARKS "Build FP16 micro-benchmarks" ON)' \
                       'OPTION(FP16_BUILD_BENCHMARKS "Build FP16 micro-benchmarks" OFF)'

      # Remove unconditional psimd ADD_SUBDIRECTORY block.
      # onnxruntime CoreML EP only uses fp16 headers, not psimd.
      sed -i '/# ---\[ Configure psimd/,/^ENDIF()/c\# psimd disabled — not needed for header-only usage' CMakeLists.txt
    '';
  };
in
lib.optionalAttrs isDarwin {
  onnxruntime = (
    prev.onnxruntime.overrideAttrs (old: {
      cmakeFlags = (builtins.filter (f: !(lib.hasInfix "ENABLE_LTO" f)) (old.cmakeFlags or [ ])) ++ [
        (lib.cmakeBool "onnxruntime_USE_COREML" true)
        # LTO fails with nix clang (CMAKE_CXX_COMPILER_AR-NOTFOUND)
        (lib.cmakeBool "onnxruntime_ENABLE_LTO" false)
        # Pre-fetched sources for CoreML EP deps
        (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_COREMLTOOLS" "${coremltools-src}")
        (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_FP16" "${fp16-src}")
      ];

      # CoreML EP links against system frameworks not in the nix store.
      # __noChroot disables the seatbelt sandbox so the linker can find them.
      __noChroot = true;
    })
  );

  # Also override the Python bindings to use our CoreML-enabled onnxruntime
  python3Packages = prev.python3Packages.overrideScope (
    _pfinal: pprev: {
      onnxruntime = pprev.onnxruntime.override {
        onnxruntime = final.onnxruntime;
      };
    }
  );
}
