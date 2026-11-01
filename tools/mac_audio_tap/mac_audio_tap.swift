// tools/mac_audio_tap/mac_audio_tap.swift
// Native macOS ScreenCaptureKit Digital Audio Tap for Whisper STT
// Captures system audio output digitally without routing through physical speakers.
// Vectorized downsampling to 16kHz mono 16-bit PCM via Apple's Accelerate framework (vDSP).

import Foundation
import ScreenCaptureKit
import CoreMedia
import AVFoundation
import Accelerate

class AccelerateAudioConverter {
    let targetRate: Double = 16000.0
    // 7-tap Hamming-windowed sinc anti-aliasing low-pass FIR filter (fc ~= 7.5kHz at 48kHz)
    private let antiAliasFilter48to16: [Float] = [
        0.036633, 0.122241, 0.227469, 0.273314, 0.227469, 0.122241, 0.036633
    ]

    private var scratchFloatInput: [Float] = []
    private var scratchMono: [Float] = []
    private var remainderMono: [Float] = []
    private var scratchFiltered: [Float] = []
    private var scratchResampled: [Float] = []
    private var scratchInt16: [Int16] = []
    private var scratchIndices: [Float] = []
    private var scratchScaledFloats: [Float] = []

    func convertRawAudio(
        dataPointer: UnsafePointer<Int8>,
        length: Int,
        inSampleRate: Double,
        channels: Int,
        isFloat: Bool,
        isNonInterleaved: Bool = false
    ) -> Data? {
        let bytesPerSample = isFloat ? 4 : 2
        let bytesPerFrame = bytesPerSample * channels
        guard bytesPerFrame > 0 else { return nil }
        let frameCount = length / bytesPerFrame
        guard frameCount > 0 else { return nil }

        let totalSamples = frameCount * channels
        if scratchFloatInput.count < totalSamples {
            scratchFloatInput = [Float](repeating: 0.0, count: totalSamples)
        }

        if isFloat {
            let floatPtr = UnsafeRawPointer(dataPointer).bindMemory(to: Float.self, capacity: totalSamples)
            scratchFloatInput.withUnsafeMutableBufferPointer { destPtr in
                destPtr.baseAddress!.initialize(from: floatPtr, count: totalSamples)
            }
        } else {
            let int16Ptr = UnsafeRawPointer(dataPointer).bindMemory(to: Int16.self, capacity: totalSamples)
            var scale: Float = 1.0 / 32768.0
            vDSP_vflt16(int16Ptr, 1, &scratchFloatInput, 1, vDSP_Length(totalSamples))
            vDSP_vsmul(scratchFloatInput, 1, &scale, &scratchFloatInput, 1, vDSP_Length(totalSamples))
        }

        let totalMonoInput = remainderMono.count + frameCount
        if scratchMono.count < totalMonoInput {
            scratchMono = [Float](repeating: 0.0, count: totalMonoInput)
        }
        if !remainderMono.isEmpty {
            for rIdx in 0..<remainderMono.count {
                scratchMono[rIdx] = remainderMono[rIdx]
            }
        }
        let monoOffset = remainderMono.count

        if channels == 1 {
            for i in 0..<frameCount {
                scratchMono[monoOffset + i] = scratchFloatInput[i]
            }
        } else if isNonInterleaved {
            // Planar buffer layout: channel c occupies [c * frameCount ..< (c + 1) * frameCount]
            let channelScale: Float = 1.0 / Float(channels)
            for i in 0..<frameCount {
                var sum: Float = 0.0
                for ch in 0..<channels {
                    sum += scratchFloatInput[ch * frameCount + i]
                }
                scratchMono[monoOffset + i] = sum * channelScale
            }
        } else if channels == 2 {
            for i in 0..<frameCount {
                scratchMono[monoOffset + i] = 0.5 * (scratchFloatInput[i * 2] + scratchFloatInput[i * 2 + 1])
            }
        } else {
            let channelScale: Float = 1.0 / Float(channels)
            for i in 0..<frameCount {
                var sum: Float = 0.0
                let base = i * channels
                for ch in 0..<channels {
                    sum += scratchFloatInput[base + ch]
                }
                scratchMono[monoOffset + i] = sum * channelScale
            }
        }

        let targetFrameCount: Int
        var scaleToInt16: Float = 32767.0

        if abs(inSampleRate - targetRate) < 1.0 {
            remainderMono.removeAll(keepingCapacity: true)
            targetFrameCount = totalMonoInput
            if scratchScaledFloats.count < targetFrameCount {
                scratchScaledFloats = [Float](repeating: 0.0, count: targetFrameCount)
            }
            scratchMono.withUnsafeBufferPointer { monoBuf in
                vDSP_vsmul(monoBuf.baseAddress!, 1, &scaleToInt16, &scratchScaledFloats, 1, vDSP_Length(targetFrameCount))
            }
        } else if abs(inSampleRate - 48000.0) < 10.0 {
            let decimationFactor = 3
            let filterLen = antiAliasFilter48to16.count
            let usableInput = totalMonoInput - (filterLen - 1)
            guard usableInput >= decimationFactor else {
                remainderMono = Array(scratchMono[0..<totalMonoInput])
                return nil
            }
            targetFrameCount = usableInput / decimationFactor
            let consumedInput = targetFrameCount * decimationFactor
            let remCount = totalMonoInput - consumedInput
            remainderMono = remCount > 0 ? Array(scratchMono[consumedInput..<totalMonoInput]) : []

            if scratchResampled.count < targetFrameCount {
                scratchResampled = [Float](repeating: 0.0, count: targetFrameCount)
            }
            if scratchScaledFloats.count < targetFrameCount {
                scratchScaledFloats = [Float](repeating: 0.0, count: targetFrameCount)
            }
            vDSP_desamp(
                scratchMono,
                vDSP_Stride(decimationFactor),
                antiAliasFilter48to16,
                &scratchResampled,
                vDSP_Length(targetFrameCount),
                vDSP_Length(filterLen)
            )
            scratchResampled.withUnsafeBufferPointer { resampBuf in
                vDSP_vsmul(resampBuf.baseAddress!, 1, &scaleToInt16, &scratchScaledFloats, 1, vDSP_Length(targetFrameCount))
            }
        } else {
            remainderMono.removeAll(keepingCapacity: true)
            let duration = Double(totalMonoInput) / inSampleRate
            targetFrameCount = Int(round(duration * targetRate))
            guard targetFrameCount > 0 else { return nil }

            if scratchResampled.count < targetFrameCount {
                scratchResampled = [Float](repeating: 0.0, count: targetFrameCount)
            }
            if scratchIndices.count < targetFrameCount {
                scratchIndices = [Float](repeating: 0.0, count: targetFrameCount)
            }
            if scratchScaledFloats.count < targetFrameCount {
                scratchScaledFloats = [Float](repeating: 0.0, count: targetFrameCount)
            }

            let step = Float(totalMonoInput - 1) / Float(max(1, targetFrameCount - 1))
            for i in 0..<targetFrameCount {
                scratchIndices[i] = Float(i) * step
            }
            vDSP_vgenp(scratchMono, 1, scratchIndices, 1, &scratchResampled, 1, vDSP_Length(targetFrameCount), vDSP_Length(totalMonoInput))
            scratchResampled.withUnsafeBufferPointer { resampBuf in
                vDSP_vsmul(resampBuf.baseAddress!, 1, &scaleToInt16, &scratchScaledFloats, 1, vDSP_Length(targetFrameCount))
            }
        }

        if scratchInt16.count < targetFrameCount {
            scratchInt16 = [Int16](repeating: 0, count: targetFrameCount)
        }

        vDSP_vfix16(scratchScaledFloats, 1, &scratchInt16, 1, vDSP_Length(targetFrameCount))

        let byteCount = targetFrameCount * MemoryLayout<Int16>.size
        return Data(bytes: scratchInt16, count: byteCount)
    }
}

@available(macOS 13.0, *)
class ScreenCaptureAudioTap: NSObject, SCStreamOutput, SCStreamDelegate {
    private var stream: SCStream?
    private let outputFormat: String
    private let sampleRate: Int
    private let stdoutHandle = FileHandle.standardOutput
    private let converter = AccelerateAudioConverter()
    private let writeQueue = DispatchQueue(label: "com.nee.mac_audio_tap.writer", qos: .userInteractive)
    private var totalSamplesEmitted: Int64 = 0
    private var realAudioSamplesEmitted: Int64 = 0
    private var heartbeatSilenceSamplesEmitted: Int64 = 0
    private var startTime: DispatchTime?
    private var heartbeatTimer: DispatchSourceTimer?
    private var isShuttingDown = false

    init(outputFormat: String = "pcm_s16le", sampleRate: Int = 16000) {
        self.outputFormat = outputFormat
        self.sampleRate = sampleRate
        super.init()
    }

    private func safeWriteToStdout(_ data: Data) -> Bool {
        return data.withUnsafeBytes { (rawBuf: UnsafeRawBufferPointer) -> Bool in
            guard let base = rawBuf.baseAddress, rawBuf.count > 0 else { return true }
            var written = 0
            while written < rawBuf.count {
                let n = write(STDOUT_FILENO, base.advanced(by: written), rawBuf.count - written)
                if n < 0 {
                    if errno == EINTR { continue }
                    if errno == EPIPE {
                        fputs("[SCK-TAP] Downstream pipe closed (EPIPE). Exiting cleanly.\n", stderr)
                        exit(0)
                    }
                    return false
                }
                written += n
            }
            return true
        }
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            fputs("[SCK-TAP] Error: No active display found for ScreenCaptureKit\n", stderr)
            exit(1)
        }

        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.channelCount = 2
        config.sampleRate = 48000

        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let newStream = SCStream(filter: filter, configuration: config, delegate: self)
        self.stream = newStream
        try newStream.addStreamOutput(self, type: .audio, sampleHandlerQueue: DispatchQueue(label: "com.nee.mac_audio_tap.sampleQueue", qos: .userInteractive))
        try await newStream.startCapture()

        if self.startTime == nil {
            self.startTime = DispatchTime.now()
        }
        self.startHeartbeat()

        fputs("[SCK-TAP] ScreenCaptureKit digital audio tap active (Apple Accelerate vDSP -> 16000Hz mono)\n", stderr)
    }

    private func startHeartbeat() {
        let timer = DispatchSource.makeTimerSource(queue: writeQueue)
        timer.schedule(deadline: .now() + .milliseconds(50), repeating: .milliseconds(50))
        timer.setEventHandler { [weak self] in
            guard let self = self, let start = self.startTime else { return }
            let now = DispatchTime.now()
            let elapsedNanos = now.uptimeNanoseconds - start.uptimeNanoseconds
            let elapsedSeconds = Double(elapsedNanos) / 1_000_000_000.0
            let targetSamples = Int64(elapsedSeconds * Double(self.sampleRate))
            let deficit = targetSamples - self.totalSamplesEmitted
            // If more than 50ms of audio (800 samples) behind monotonic clock, emit digital silence
            // Note: heartbeat silence is excluded from realAudioSamplesEmitted proof accounting.
            if deficit >= 800 {
                let zeroChunk = [Int16](repeating: 0, count: Int(deficit))
                zeroChunk.withUnsafeBufferPointer { ptr in
                    let data = Data(buffer: ptr)
                    _ = self.safeWriteToStdout(data)
                }
                self.heartbeatSilenceSamplesEmitted += deficit
                self.totalSamplesEmitted += deficit
            }
        }
        self.heartbeatTimer = timer
        timer.resume()
    }

    func emitInitialSilence(sampleCount: Int = 1600) {
        let initialSilence = [Int16](repeating: 0, count: sampleCount)
        initialSilence.withUnsafeBufferPointer { ptr in
            _ = safeWriteToStdout(Data(buffer: ptr))
        }
        heartbeatSilenceSamplesEmitted += Int64(sampleCount)
        totalSamplesEmitted += Int64(sampleCount)
    }

    func stop() async {
        self.isShuttingDown = true
        self.heartbeatTimer?.cancel()
        self.heartbeatTimer = nil
        if let s = self.stream {
            try? await s.stopCapture()
            self.stream = nil
            fputs("[SCK-TAP] Capture stopped cleanly\n", stderr)
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio else { return }
        guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }

        var length = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        let status = CMBlockBufferGetDataPointer(
            blockBuffer,
            atOffset: 0,
            lengthAtOffsetOut: nil,
            totalLengthOut: &length,
            dataPointerOut: &dataPointer
        )

        guard status == kCMBlockBufferNoErr, let dataPointer = dataPointer, length > 0 else { return }

        var inSampleRate: Double = 48000.0
        var channels: Int = 2
        var isFloat: Bool = true
        var isNonInterleaved: Bool = false

        if let formatDesc = CMSampleBufferGetFormatDescription(sampleBuffer),
           let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(formatDesc)?.pointee {
            if asbd.mSampleRate > 0 { inSampleRate = asbd.mSampleRate }
            if asbd.mChannelsPerFrame > 0 { channels = Int(asbd.mChannelsPerFrame) }
            isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
            isNonInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0
        }

        if let pcm16kData = self.converter.convertRawAudio(
            dataPointer: dataPointer,
            length: length,
            inSampleRate: inSampleRate,
            channels: channels,
            isFloat: isFloat,
            isNonInterleaved: isNonInterleaved
        ) {
            writeQueue.async {
                _ = self.safeWriteToStdout(pcm16kData)
                let nSamples = Int64(pcm16kData.count / MemoryLayout<Int16>.size)
                self.realAudioSamplesEmitted += nSamples
                self.totalSamplesEmitted += nSamples
            }
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fputs("[SCK-TAP] Stream stopped with error: \(error.localizedDescription). Scheduling recovery...\n", stderr)
        Task { [weak self] in
            guard let self = self else { return }
            await self.attemptStreamRecovery()
        }
    }

    private func attemptStreamRecovery() async {
        guard !isShuttingDown else { return }
        if let s = self.stream {
            try? await s.stopCapture()
            self.stream = nil
        }

        var retryCount = 0
        while !isShuttingDown {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            guard !isShuttingDown else { return }
            retryCount += 1
            fputs("[SCK-TAP] Attempting stream recovery (attempt \(retryCount))...\n", stderr)
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
                guard let display = content.displays.first else {
                    fputs("[SCK-TAP] Recovery waiting: No active display found yet\n", stderr)
                    continue
                }
                let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
                let config = SCStreamConfiguration()
                config.capturesAudio = true
                config.excludesCurrentProcessAudio = true
                config.channelCount = 2
                config.sampleRate = 48000
                config.width = 2
                config.height = 2
                config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

                let newStream = SCStream(filter: filter, configuration: config, delegate: self)
                self.stream = newStream
                try newStream.addStreamOutput(self, type: .audio, sampleHandlerQueue: DispatchQueue(label: "com.nee.mac_audio_tap.sampleQueue", qos: .userInteractive))
                try await newStream.startCapture()
                fputs("[SCK-TAP] Stream recovery successful (attempt \(retryCount))! Digital audio capture resumed.\n", stderr)
                return
            } catch {
                fputs("[SCK-TAP] Recovery attempt \(retryCount) failed: \(error.localizedDescription)\n", stderr)
            }
        }
    }
}

@main
struct MacAudioTapMain {
    static func main() {
        // Ignore SIGPIPE to avoid crashing when downstream FFmpeg pipe terminates
        signal(SIGPIPE, SIG_IGN)

        let allowedFlags: Set<String> = ["--probe", "--source-hash", "--version"]
        let cliArgs = Array(CommandLine.arguments.dropFirst())
        let unknownFlags = cliArgs.filter { !allowedFlags.contains($0) }

        if !unknownFlags.isEmpty {
            fputs("Usage: mac_audio_tap [--probe | --source-hash | --version]\n", stderr)
            fputs("Unrecognized arguments: \(unknownFlags.joined(separator: ", "))\n", stderr)
            exit(2)
        }

        if cliArgs.contains("--version") {
            print("mac_audio_tap version \(EMBEDDED_VERSION)")
            exit(0)
        }

        if cliArgs.contains("--source-hash") {
            print(EMBEDDED_SOURCE_SHA256)
            exit(0)
        }

        if cliArgs.contains("--probe") {
            if #available(macOS 13.0, *) {
                // Enforce 800ms internal watchdog timeout to guarantee termination if XPC stalls
                DispatchQueue.global().asyncAfter(deadline: .now() + .milliseconds(800)) {
                    fputs("[SCK-PROBE] Timeout waiting for SCShareableContent\n", stderr)
                    exit(1)
                }
                Task {
                    do {
                        _ = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
                        exit(0)
                    } catch {
                        fputs("[SCK-PROBE] Failed: \(error.localizedDescription)\n", stderr)
                        exit(1)
                    }
                }
                RunLoop.main.run()
            } else {
                fputs("[SCK-PROBE] ScreenCaptureKit requires macOS 13.0 or later\n", stderr)
                exit(1)
            }
        }

        // Default capture mode when invoked with no arguments
        if #available(macOS 13.0, *) {
            let tap = ScreenCaptureAudioTap()
            tap.emitInitialSilence(sampleCount: 1600)
            Task {
                do {
                    try await tap.start()
                } catch {
                    fputs("[SCK-TAP] Initialization failed: \(error.localizedDescription)\n", stderr)
                    exit(1)
                }
            }
            RunLoop.main.run()
        } else {
            fputs("[SCK-TAP] ScreenCaptureKit requires macOS 13.0 or later\n", stderr)
            exit(1)
        }
    }
}

