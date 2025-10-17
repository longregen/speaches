{
  pkgs,
  mkSpeaches,
  testModelCache,
  system,
}:
{
  pythonVersion,
  fullTest ? true,
}:
let
  testPackage = mkSpeaches {
    inherit pythonVersion;
    withCuda = false;
  };

  otelCollectorConfig = pkgs.writeText "otel-collector-config.yaml" ''
    receivers:
      otlp:
        protocols:
          grpc:
            endpoint: 0.0.0.0:4317
    exporters:
      debug:
        verbosity: basic
    service:
      pipelines:
        traces:
          receivers: [otlp]
          exporters: [debug]
        metrics:
          receivers: [otlp]
          exporters: [debug]
        logs:
          receivers: [otlp]
          exporters: [debug]
  '';
in
pkgs.testers.nixosTest {
  name = "speaches-e2e-test-${pythonVersion}";
  enableOCR = false;

  nodes.machine =
    {
      config,
      pkgs,
      ...
    }:
    {
      imports = [ ./module.nix ];

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
          OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:4317";
        };
      };

      systemd.services.otel-collector = {
        description = "OpenTelemetry Collector";
        after = [ "network.target" ];
        wantedBy = [ "multi-user.target" ];
        serviceConfig = {
          ExecStart = "${pkgs.opentelemetry-collector-contrib}/bin/otelcol-contrib --config ${otelCollectorConfig}";
          Restart = "on-failure";
        };
      };

      environment.systemPackages =
        with pkgs;
        [
          curl
          jq
          file
        ]
        ++ (
          if fullTest then
            [
              sox
              ffmpeg-full
            ]
          else
            [ ]
        );

      virtualisation = {
        memorySize = 4096;
        cores = 2;
      };
    };

  testScript =
    if fullTest then
      ''
        import json
        import time

        machine.start()
        machine.wait_for_unit("speaches.service")
        machine.wait_for_open_port(18000)

        time.sleep(5)

        print("Testing health endpoint...")
        machine.succeed("curl -f http://127.0.0.1:18000/health")

        print("Testing model listing...")
        models_output = machine.succeed("curl -s http://127.0.0.1:18000/v1/models")
        models_data = json.loads(models_output)
        assert "data" in models_data

        machine.succeed("mkdir -p /tmp/test_results")

        original_text = "People assume that time is a strict progression of cause to effect. But actually, from a nonlinear, non-subjective viewpoint, it is more like a big ball of wibbly wobbly, timey wimey stuff."

        print("Testing TTS with Kokoro model...")
        tts_result = machine.succeed(f"""
          curl -f -X POST "http://127.0.0.1:18000/v1/audio/speech" \
            -H "Content-Type: application/json" \
            -d '{{"model": "tts-1", "input": "{original_text}", "voice": "af_bella", "response_format": "mp3"}}' \
            -o /tmp/test_results/test_output.mp3
        """)

        machine.succeed("test -f /tmp/test_results/test_output.mp3")
        machine.succeed("test -s /tmp/test_results/test_output.mp3")

        print("Testing STT with Whisper base model...")
        stt_result = machine.succeed("""
          curl -f -X POST "http://127.0.0.1:18000/v1/audio/transcriptions" \
            -F "file=@/tmp/test_results/test_output.mp3" \
            -F "model=Systran/faster-whisper-base" \
            -o /tmp/test_results/transcription.json
        """)

        machine.succeed("test -f /tmp/test_results/transcription.json")
        machine.succeed("test -s /tmp/test_results/transcription.json")
        transcription_output = machine.succeed("cat /tmp/test_results/transcription.json")
        print(f"Transcription response: {transcription_output}")
        transcription_data = json.loads(transcription_output)
        assert "text" in transcription_data

        transcribed_text = transcription_data['text']
        print("TTS->STT pipeline test passed with ${pythonVersion}!")
        print(f"Original text: {original_text}")
        print(f"Transcribed text: {transcribed_text}")

        assert len(transcribed_text) > 0
        assert any(word in transcribed_text.lower() for word in ["people", "assume", "viewpoint", "cause"])

        print("Testing OpenTelemetry integration...")
        machine.wait_for_unit("otel-collector.service")
        machine.wait_for_open_port(4317)

        # The previous API requests should have generated telemetry.
        # Wait for the batch exporter to flush.
        time.sleep(10)

        otel_logs = machine.succeed("journalctl -u otel-collector.service --no-pager")
        assert "Traces" in otel_logs and "resource spans" in otel_logs, f"No traces found in collector logs:\n{otel_logs[-500:]}"
        print("OpenTelemetry traces received by collector!")

        print("All tests passed successfully!")
      ''
    else
      ''
        machine.start()
        machine.wait_for_unit("speaches.service")
        machine.wait_for_open_port(18000)
        print("Testing ${pythonVersion} package...")
        machine.succeed("curl -f http://127.0.0.1:18000/health")
        print("${pythonVersion} e2e test passed!")
      '';
}
