/**
 * PCM Audio Processor Worklet
 * Converts Float32 audio samples to Int16 PCM for streaming to server
 */
class PCMProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.bufferSize = 2048; // Accumulate samples before sending
        this.buffer = new Float32Array(this.bufferSize);
        this.bufferIndex = 0;
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (!input || !input[0]) return true;

        const channelData = input[0]; // Mono channel

        for (let i = 0; i < channelData.length; i++) {
            this.buffer[this.bufferIndex++] = channelData[i];

            if (this.bufferIndex >= this.bufferSize) {
                // Convert Float32 to Int16 PCM
                const pcmData = new Int16Array(this.bufferSize);
                for (let j = 0; j < this.bufferSize; j++) {
                    // Clamp to [-1, 1] and scale to Int16 range
                    const s = Math.max(-1, Math.min(1, this.buffer[j]));
                    pcmData[j] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }

                // Send PCM data to main thread
                this.port.postMessage({
                    pcmData: pcmData.buffer
                }, [pcmData.buffer]);

                // Reset buffer
                this.buffer = new Float32Array(this.bufferSize);
                this.bufferIndex = 0;
            }
        }

        return true; // Keep processor alive
    }
}

registerProcessor('pcm-processor', PCMProcessor);
