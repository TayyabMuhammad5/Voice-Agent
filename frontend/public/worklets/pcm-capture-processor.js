/**
 * PCM Capture AudioWorklet Processor
 *
 * Runs on the audio rendering thread. Collects raw float32 PCM samples
 * from the microphone and posts them to the main thread in configurable
 * buffer sizes (~250ms chunks at 48kHz = 12000 samples).
 *
 * Why AudioWorklet instead of MediaRecorder?
 * - MediaRecorder produces WebM/Opus containers where only the first
 *   chunk has the EBML header. Individual echoed chunks can't be decoded
 *   by decodeAudioData() — they're not self-contained audio files.
 * - Raw PCM samples can be echoed back and played directly via
 *   AudioContext with zero decode overhead, giving us true real-time echo.
 * - In Phase 2, we switch to MediaRecorder for Deepgram (which needs
 *   webm/opus), but the echo path will be replaced by real STT→LLM→TTS.
 */

class PCMCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    // Buffer size in samples. At 48kHz, 4096 samples ≈ 85ms.
    this.bufferSize = options?.processorOptions?.bufferSize || 4096;
    this.buffer = new Int16Array(this.bufferSize);
    this.writeIndex = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;

    // Take channel 0 (mono)
    const channelData = input[0];
    if (!channelData) return true;

    for (let i = 0; i < channelData.length; i++) {
      // Convert float32 to int16
      let sample = Math.max(-1, Math.min(1, channelData[i]));
      this.buffer[this.writeIndex++] = sample < 0 ? sample * 32768 : sample * 32767;

      if (this.writeIndex >= this.bufferSize) {
        // Copy the buffer and post it to the main thread
        this.port.postMessage({
          type: "pcm",
          samples: this.buffer.slice(),
        });
        this.writeIndex = 0;
      }
    }

    return true; // keep processor alive
  }
}

registerProcessor("pcm-capture-processor", PCMCaptureProcessor);
