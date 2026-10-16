import { useEffect, useRef, useState } from "react";

/**
 * Where a barcode is read: a hand scanner types the code and presses
 * Enter, so the box takes typed text; a phone opens its camera where the
 * browser can read barcodes itself (Chrome on Android), and the code it
 * sees is handed over the same way. One handler, whichever read it.
 */
interface Detected { rawValue: string }
interface Detector { detect(source: HTMLVideoElement): Promise<Detected[]> }
declare const BarcodeDetector: (new (options?: { formats?: string[] }) => Detector) | undefined;

export function ScanBox({ label, hint, onScan, busy }: {
  label: string; hint?: string; onScan: (code: string) => void | Promise<void>; busy?: boolean;
}) {
  const [typed, setTyped] = useState("");
  const [camera, setCamera] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const canCamera = typeof BarcodeDetector !== "undefined" && typeof navigator !== "undefined" && Boolean(navigator.mediaDevices);

  const submit = () => {
    const code = typed.trim();
    if (!code) return;
    setTyped("");
    void onScan(code);
  };

  useEffect(() => {
    if (!camera || !video.current || typeof BarcodeDetector === "undefined") return;
    const detector = new BarcodeDetector({ formats: ["code_128", "ean_13", "qr_code"] });
    let stream: MediaStream | null = null;
    let stopped = false;
    const element = video.current;
    const look = async () => {
      while (!stopped) {
        try {
          const found = await detector.detect(element);
          if (found.length) {
            stopped = true;
            setCamera(false);
            void onScan(found[0]!.rawValue);
            return;
          }
        } catch {
          // A frame the detector could not read: look again.
        }
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
    };
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } }).then((got) => {
      stream = got;
      element.srcObject = got;
      void element.play().then(look);
    }).catch(() => {
      setProblem("The camera could not be opened; type the code instead.");
      setCamera(false);
    });
    return () => {
      stopped = true;
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, [camera, onScan]);

  return (
    <div className="scan-box">
      <label>
        {label}
        <input type="text" value={typed} autoFocus inputMode="text" autoComplete="off" disabled={busy}
          placeholder={hint ?? "Scan, or type and press Enter"} aria-label={label}
          onChange={(event) => setTyped(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); submit(); } }} />
      </label>
      <button type="button" className="btn" disabled={busy || !typed.trim()} onClick={submit}>Read</button>
      {canCamera && (
        <button type="button" className="btn" aria-pressed={camera} onClick={() => setCamera((on) => !on)}>
          {camera ? "Close camera" : "Camera"}
        </button>
      )}
      {camera && <video ref={video} className="scan-video" muted playsInline aria-label="Camera" />}
      {problem && <p className="form-error" role="alert">{problem}</p>}
    </div>
  );
}
