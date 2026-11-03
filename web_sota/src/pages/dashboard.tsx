import {
  Activity,
  Columns2,
  Cpu,
  Eye,
  EyeOff,
  FileText,
  Loader2,
  Printer,
  RefreshCw,
  RotateCcw,
  Rows3,
  Scan,
  ScanLine,
  Server,
  Sparkles,
  Upload,
  X,
  XCircle,
  Zap,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { LlmOnboarding } from "@/components/llm/LlmOnboarding";
import { OcrResultPanel } from "@/components/ocr/OcrResultPanel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useOcrTextStore, useScanStore } from "@/store";

const DEFAULT_BACKEND_KEY = "ocr_default_backend";

interface ScannerInfo {
  device_id: string;
  name: string;
  type: string;
}

interface BackendInfo {
  name: string;
  available: boolean;
}

interface HealthInfo {
  status: string;
  server: string;
  version: string;
  uptime_seconds: number;
  tool_count: number;
}

export function Dashboard() {
  const navigate = useNavigate();
  const { ocrText, setOcrText, ocrJobId, setOcrJobId, ocrStatus, setOcrStatus, ocrResult, setOcrResult } =
    useOcrTextStore();
  const { lastScan, setLastScan, setLastOcrJobId } = useScanStore();

  const [scanners, setScanners] = useState<ScannerInfo[]>([]);
  const [backends, setBackends] = useState<BackendInfo[]>([]);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [selectedScanner, setSelectedScanner] = useState("");
  const [selectedBackend, setSelectedBackend] = useState(() => localStorage.getItem(DEFAULT_BACKEND_KEY) || "auto");
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [droppedFile, setDroppedFile] = useState<File | null>(null);
  const [droppedFilePreview, setDroppedFilePreview] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [watcherActive, setWatcherActive] = useState(false);
  const [watcherScans, setWatcherScans] = useState(0);
  const [preservePhotocopyScan, setPreservePhotocopyScan] = useState(true);

  // User-facing layout & mode enhancements
  const [splitView, setSplitView] = useState<boolean>(true);
  const [zoomLevel, setZoomLevel] = useState<number>(100);
  const [ocrMode, setOcrMode] = useState<"markdown" | "text">("markdown");
  const [mathMode, setMathMode] = useState<boolean>(false);
  const [lastResultData, setLastResultData] = useState<any>(ocrResult || null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Generate object preview URL for uploaded image files
  useEffect(() => {
    if (droppedFile && droppedFile.type.startsWith("image/")) {
      const url = URL.createObjectURL(droppedFile);
      setDroppedFilePreview(url);
      return () => URL.revokeObjectURL(url);
    }
    setDroppedFilePreview(null);
  }, [droppedFile]);

  const activeImageUrl = droppedFilePreview || lastScan.imageUrl;

  // Auto-detect best available backend
  const effectiveBackend = useMemo(() => {
    if (selectedBackend === "auto") {
      // Prefer indra-ocr if available, then unlimited-ocr, else auto
      if (backends.some((b) => b.name === "indra-ocr" && b.available)) return "indra-ocr";
      if (backends.some((b) => b.name === "unlimited-ocr" && b.available)) return "unlimited-ocr";
      return "auto";
    }
    return selectedBackend;
  }, [selectedBackend, backends]);

  const fetchScanners = useCallback(async () => {
    try {
      const res = await fetch("/api/scanners");
      const data = await res.json().catch(() => {
        console.error("dashboard: failed to parse scanners response");
        return {};
      });
      const list = data.scanners || [];
      setScanners(list);
      if (data.error) setError(data.error);
      if (data.default_scanner && !selectedScanner) {
        setSelectedScanner(data.default_scanner);
      } else if (list.length > 0 && !selectedScanner) {
        setSelectedScanner(list[0].device_id);
      }
    } catch {
      setError("Failed to reach backend — is it running?");
    }
  }, [selectedScanner]);

  const fetchBackends = useCallback(async () => {
    try {
      const res = await fetch("/api/backends");
      if (!res.ok) {
        setError(`Backend API error: ${res.status}`);
        return;
      }
      const data = await res.json();
      setBackends(data.backends || []);
    } catch {
      setError("Failed to reach backend — is it running?");
    }
  }, []);

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch("/api/health");
      if (res.ok) {
        const data = await res.json();
        setHealth(data);
      }
    } catch {
      /* noop */
    }
  }, []);

  const fetchWatcherStatus = useCallback(async () => {
    try {
      const res = await fetch("/api/scanner/watch/status");
      if (res.ok) {
        const data = await res.json();
        setWatcherActive(data.running);
        setWatcherScans(data.scans_triggered || 0);
      }
    } catch {
      /* noop */
    }
  }, []);

  const toggleWatcher = useCallback(async () => {
    if (watcherActive) {
      await fetch("/api/scanner/watch/stop", { method: "POST" });
      setWatcherActive(false);
    } else {
      const deviceId = selectedScanner || (scanners.length > 0 ? scanners[0].device_id : "");
      if (!deviceId) return;
      await fetch("/api/scanner/watch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: deviceId, mode: "preview", backend: effectiveBackend }),
      });
      setWatcherActive(true);
    }
  }, [watcherActive, selectedScanner, scanners, effectiveBackend]);

  useEffect(() => {
    fetchScanners();
    fetchBackends();
    fetchHealth();
    fetchWatcherStatus();
    const interval = setInterval(fetchHealth, 30000);
    const watcherInterval = setInterval(fetchWatcherStatus, 5000);
    return () => {
      clearInterval(interval);
      clearInterval(watcherInterval);
    };
  }, [fetchScanners, fetchBackends, fetchHealth, fetchWatcherStatus]);

  // Poll job status
  const pollJob = useCallback(
    (jobId: string) => {
      stopPoll();
      pollRef.current = setInterval(async () => {
        try {
          const res = await fetch(`/api/job/${jobId}`);
          if (!res.ok) {
            stopPoll();
            setOcrStatus("failed");
            setError("Job not found");
            return;
          }
          const data = await res.json();
          if (data.status === "completed") {
            stopPoll();
            setOcrStatus("completed");
            setScanning(false);
            const text = data.result?.markdown || data.result?.text || data.text || "";
            if (text) setOcrText(text);
            if (data.result) {
              setLastResultData(data.result);
              setOcrResult(data.result);
            }
            setStatusMsg("OCR complete");
          } else if (data.status === "failed") {
            stopPoll();
            setOcrStatus("failed");
            setScanning(false);
            setError(data.error || "OCR failed");
          }
        } catch {
          stopPoll();
          setOcrStatus("failed");
          setScanning(false);
          setError("Network error polling job");
        }
      }, 1500);
    },
    [setOcrStatus, setOcrText, setOcrResult],
  );

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => () => stopPoll(), []);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) {
      setDroppedFile(file);
      setError(null);
      setStatusMsg(null);
    }
  };

  const handleFilePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setDroppedFile(file);
      setError(null);
      setStatusMsg(null);
    }
  };

  const handleClearDroppedFile = (e: React.MouseEvent) => {
    e.stopPropagation();
    setDroppedFile(null);
    setDroppedFilePreview(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleQuickScanOcr = async () => {
    setError(null);
    setStatusMsg(null);
    setScanning(true);
    setOcrStatus("processing");
    setOcrText("");

    try {
      if (droppedFile) {
        // Upload file
        const formData = new FormData();
        formData.append("file", droppedFile);
        formData.append("ocr_mode", ocrMode);
        formData.append("backend", effectiveBackend);
        formData.append("math", String(mathMode));

        const res = await fetch("/api/upload", { method: "POST", body: formData });
        if (!res.ok) throw new Error("Upload failed");
        const data = await res.json();
        const jobId = data.job_id;
        if (!jobId) throw new Error("No job ID returned");
        setOcrJobId(jobId);
        setLastOcrJobId(jobId);
        setStatusMsg("Processing uploaded file with " + effectiveBackend + "...");
        pollJob(jobId);
      } else if (selectedScanner && scanners.length > 0) {
        // Scan then OCR
        const scanForm = new FormData();
        scanForm.append("device_id", selectedScanner);
        scanForm.append("dpi", "300");
        scanForm.append("color_mode", "Color");
        scanForm.append("paper_size", "A4");

        const scanRes = await fetch("/api/scan", { method: "POST", body: scanForm });
        if (!scanRes.ok) throw new Error("Scan failed");
        const scanData = await scanRes.json();
        if (!scanData.success) throw new Error(scanData.message || "Scan failed");

        const filename = scanData.image_info?.filename || scanData.filename;
        setLastScan({ imageUrl: scanData.image_path, filename });

        const ocrForm = new FormData();
        ocrForm.append("filename", filename);
        ocrForm.append("ocr_mode", ocrMode);
        ocrForm.append("backend", effectiveBackend);
        ocrForm.append("math", String(mathMode));

        const ocrRes = await fetch("/api/ocr_scanned", { method: "POST", body: ocrForm });
        if (!ocrRes.ok) throw new Error("OCR request failed");
        const ocrData = await ocrRes.json();
        const jobId = ocrData.job_id;
        if (!jobId) throw new Error("No OCR job ID returned");
        setOcrJobId(jobId);
        setLastOcrJobId(jobId);
        setStatusMsg("Scanning + OCR in progress with " + effectiveBackend + "...");
        pollJob(jobId);
      } else {
        setScanning(false);
        setOcrStatus("idle");
        setError("No scanner available and no file selected. Drop a file or connect a scanner.");
      }
    } catch (err: unknown) {
      setScanning(false);
      setOcrStatus("failed");
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  const handleQuickScanRaw = async () => {
    if (!selectedScanner || scanners.length === 0) {
      setError("No scanner selected or available. Please connect a scanner.");
      return;
    }
    setError(null);
    setStatusMsg("Scanning raw document...");
    setScanning(true);

    try {
      const scanForm = new FormData();
      scanForm.append("device_id", selectedScanner);
      scanForm.append("dpi", "300");
      scanForm.append("color_mode", "Color");
      scanForm.append("paper_size", "A4");

      const scanRes = await fetch("/api/scan", { method: "POST", body: scanForm });
      if (!scanRes.ok) throw new Error("Raw scan failed");
      const scanData = await scanRes.json();
      if (!scanData.success) throw new Error(scanData.message || "Scan failed");

      const filename = scanData.image_info?.filename || scanData.filename;
      setLastScan({ imageUrl: scanData.image_path, filename });
      setScanning(false);
      setStatusMsg("Raw scan complete");
      navigate("/scan-viewer");
    } catch (err: unknown) {
      setScanning(false);
      setError(err instanceof Error ? err.message : "Raw scan failed");
    }
  };

  const handlePhotocopy = async () => {
    if (!selectedScanner || scanners.length === 0) {
      setError("No scanner selected or available. Please connect a scanner.");
      return;
    }
    setError(null);
    setStatusMsg("Photocopying: scanning document...");
    setScanning(true);

    try {
      const scanForm = new FormData();
      scanForm.append("device_id", selectedScanner);
      scanForm.append("dpi", "300");
      scanForm.append("color_mode", "Color");
      scanForm.append("paper_size", "A4");

      const scanRes = await fetch("/api/scan", { method: "POST", body: scanForm });
      if (!scanRes.ok) throw new Error("Photocopy scan failed");
      const scanData = await scanRes.json();
      if (!scanData.success) throw new Error(scanData.message || "Photocopy scan failed");

      const filename = scanData.image_info?.filename || scanData.filename;
      const imageUrl = scanData.image_path;

      if (preservePhotocopyScan) {
        setLastScan({ imageUrl, filename });
      }

      setStatusMsg("Photocopy: sending to printer...");
      const printRes = await fetch("/api/photocopy/print", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename, copies: 1 }),
      });

      if (!printRes.ok) {
        window.open(imageUrl, "_blank");
        setStatusMsg("Photocopy: opened print preview in new tab");
      } else {
        const printData = await printRes.json();
        if (printData.success) {
          setStatusMsg("Photocopy sent to default printer");
        } else {
          window.open(imageUrl, "_blank");
          setStatusMsg("Photocopy: opened print preview in browser");
        }
      }
      setScanning(false);
    } catch (err: unknown) {
      setScanning(false);
      setError(err instanceof Error ? err.message : "Photocopy failed");
    }
  };

  const handleCopy = async () => {
    if (!ocrText) return;
    try {
      await navigator.clipboard.writeText(ocrText);
    } catch {
      /* noop */
    }
  };

  const handleDownloadTxt = () => {
    if (!ocrText) return;
    const blob = new Blob([ocrText], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ocr-result-${Date.now()}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleDownloadMd = () => {
    if (!ocrText) return;
    const blob = new Blob([ocrText], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ocr-result-${Date.now()}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const backendOk = backends.some((b) => b.available);

  return (
    <div data-testid="dashboard" className="space-y-6">
      {/* Top KPI row */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card className="border-slate-800 bg-slate-950/50 backdrop-blur-sm" data-testid="kpi-server">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium text-slate-200">Server</CardTitle>
            <Server className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-lg font-bold text-white truncate">{health?.server || "ocr-mcp"}</div>
            <p className="text-xs text-slate-400">{health?.version ? `v${health.version}` : "connecting..."}</p>
          </CardContent>
        </Card>

        <Card className="border-slate-800 bg-slate-950/50 backdrop-blur-sm" data-testid="kpi-tools">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium text-slate-200">Tools</CardTitle>
            <Cpu className="h-4 w-4 text-purple-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-white">{health?.tool_count ?? "—"}</div>
            <p className="text-xs text-slate-400">MCP tools registered</p>
          </CardContent>
        </Card>

        <Card className="border-slate-800 bg-slate-950/50 backdrop-blur-sm" data-testid="kpi-backends">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium text-slate-200">Backends</CardTitle>
            <Activity className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-white">
              {backends.filter((b) => b.available).length}/{backends.length}
            </div>
            <p className="text-xs text-slate-400">Available / total</p>
          </CardContent>
        </Card>

        <Card className="border-slate-800 bg-slate-950/50 backdrop-blur-sm" data-testid="kpi-jobs">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium text-slate-200">Status</CardTitle>
            <div className="flex items-center gap-2">
              <span
                data-testid="backend-dot"
                className={`relative flex h-2.5 w-2.5 ${backendOk ? "bg-green-500" : "bg-red-500"} rounded-full`}
              >
                <span
                  className={`absolute inline-flex h-full w-full animate-ping rounded-full ${backendOk ? "bg-green-400" : "bg-red-400"} opacity-75`}
                />
              </span>
              <span className="text-xs text-slate-300">{backendOk ? "Online" : "Offline"}</span>
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-bold text-white">{ocrJobId ? "Active" : "Idle"}</div>
            <p className="text-xs text-slate-400">{ocrJobId ? ocrStatus : "Waiting for input"}</p>
          </CardContent>
        </Card>
      </div>

      {/* AI setup banner (fleet LLM onboarding; renders only when unconfigured) */}
      <LlmOnboarding mode="banner" />

      {/* Action Bar & Controls */}
      <Card className="border-slate-800 bg-slate-950/60 backdrop-blur-md shadow-lg">
        <CardContent className="p-4 space-y-3">
          {/* Quick Engine Pills */}
          <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-800/80">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Quick Engine:</span>
              <div className="flex flex-wrap items-center gap-1.5">
                {[
                  { id: "indra-ocr", label: "Indra OCR", desc: "1M CPU ONNX (v1.1)", icon: Zap, color: "emerald" },
                  { id: "auto", label: "Auto", desc: "Best available", icon: Sparkles, color: "blue" },
                  { id: "tesseract", label: "Tesseract", desc: "CPU standard", icon: FileText, color: "slate" },
                  { id: "mistral-ocr", label: "Mistral VL", desc: "Cloud / Layout", icon: Cpu, color: "purple" },
                ].map((engine) => {
                  const isSelected = selectedBackend === engine.id;
                  const isAvail = engine.id === "auto" || backends.some((b) => b.name === engine.id && b.available);
                  const Icon = engine.icon;

                  return (
                    <button
                      key={engine.id}
                      onClick={() => {
                        setSelectedBackend(engine.id);
                        localStorage.setItem(DEFAULT_BACKEND_KEY, engine.id);
                      }}
                      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium border transition-all ${
                        isSelected
                          ? "bg-blue-600 border-blue-500 text-white shadow-md ring-1 ring-blue-400/40"
                          : "bg-slate-900/80 border-slate-800 text-slate-300 hover:bg-slate-850 hover:border-slate-700"
                      }`}
                      title={`${engine.label}: ${engine.desc} ${isAvail ? "(Ready)" : "(Not installed)"}`}
                    >
                      <Icon className={`w-3.5 h-3.5 ${isSelected ? "text-white" : "text-slate-400"}`} />
                      <span>{engine.label}</span>
                      <span className={`w-1.5 h-1.5 rounded-full ${isAvail ? "bg-emerald-400" : "bg-amber-500"}`} />
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Mode & Features Switches */}
            <div className="flex flex-wrap items-center gap-3">
              {/* Markdown vs Plain Text */}
              <div className="flex items-center bg-slate-900 border border-slate-800 rounded-lg p-0.5 text-xs">
                <button
                  onClick={() => setOcrMode("markdown")}
                  className={`px-2.5 py-1 rounded-md font-medium transition-all ${
                    ocrMode === "markdown" ? "bg-slate-800 text-white shadow-sm" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  Markdown
                </button>
                <button
                  onClick={() => setOcrMode("text")}
                  className={`px-2.5 py-1 rounded-md font-medium transition-all ${
                    ocrMode === "text" ? "bg-slate-800 text-white shadow-sm" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  Plain Text
                </button>
              </div>

              {/* Math Mode Toggle */}
              <label
                className={`inline-flex items-center gap-1.5 text-xs font-medium cursor-pointer px-2.5 py-1 rounded-lg border transition-all ${
                  mathMode
                    ? "bg-amber-950/40 border-amber-700/60 text-amber-300 shadow-sm"
                    : "bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700 hover:text-slate-300"
                }`}
                title="Enable math formula recognition ($...$ / LaTeX)"
              >
                <input
                  type="checkbox"
                  checked={mathMode}
                  onChange={(e) => setMathMode(e.target.checked)}
                  className="rounded bg-slate-950 border-slate-700 text-amber-500 focus:ring-amber-500 h-3.5 w-3.5"
                />
                <span>Math Mode ($)</span>
              </label>

              {/* Split View Toggle (when document is present) */}
              {activeImageUrl && (
                <button
                  onClick={() => setSplitView((v) => !v)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-slate-800 bg-slate-900 text-xs text-slate-300 hover:bg-slate-850 hover:text-white transition-all"
                  title="Toggle Split-Screen vs Stacked Layout"
                >
                  {splitView ? (
                    <Rows3 className="w-3.5 h-3.5 text-blue-400" />
                  ) : (
                    <Columns2 className="w-3.5 h-3.5 text-blue-400" />
                  )}
                  <span>{splitView ? "Stacked View" : "Split View"}</span>
                </button>
              )}
            </div>
          </div>

          {/* Primary Action Controls */}
          <div className="flex flex-wrap items-end gap-3 pt-1">
            {/* Scanner selector */}
            <div className="min-w-[180px] flex-1">
              <label className="text-xs font-medium text-slate-400 mb-1 block">Hardware Scanner</label>
              <select
                value={selectedScanner}
                onChange={(e) => setSelectedScanner(e.target.value)}
                className="w-full rounded-md border-0 py-2 pl-3 pr-8 bg-slate-900 text-slate-100 ring-1 ring-inset ring-slate-800 focus:ring-2 focus:ring-blue-600 text-xs"
                title="Select scanner"
                data-testid="scanner-select"
              >
                {scanners.length === 0 && <option value="">No scanner detected</option>}
                {scanners.map((s) => (
                  <option key={s.device_id} value={s.device_id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Backend full dropdown */}
            <div className="min-w-[180px] flex-1">
              <label className="text-xs font-medium text-slate-400 mb-1 block">Backend Engine</label>
              <select
                value={selectedBackend}
                onChange={(e) => {
                  setSelectedBackend(e.target.value);
                  localStorage.setItem(DEFAULT_BACKEND_KEY, e.target.value);
                }}
                className="w-full rounded-md border-0 py-2 pl-3 pr-8 bg-slate-900 text-slate-100 ring-1 ring-inset ring-slate-800 focus:ring-2 focus:ring-purple-600 text-xs"
                title="Select OCR backend"
                data-testid="backend-select"
              >
                <option value="auto">Auto (best available)</option>
                {backends.map((b) => (
                  <option key={b.name} value={b.name}>
                    {b.name}
                    {b.available ? "" : " (offline)"}
                  </option>
                ))}
              </select>
            </div>

            {/* Quick Scan button */}
            <button
              onClick={handleQuickScanOcr}
              disabled={scanning}
              data-testid="quick-scan-ocr"
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-5 py-2 text-xs sm:text-sm font-semibold transition-all shadow-md hover:shadow-blue-600/20"
            >
              {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <ScanLine className="h-4 w-4" />}
              {scanning ? "Processing..." : droppedFile ? "OCR Document" : "Quick Scan & OCR"}
            </button>

            {/* Quick Scan (Raw) button */}
            <button
              onClick={handleQuickScanRaw}
              disabled={scanning || scanners.length === 0}
              data-testid="quick-scan-raw"
              title="Perform raw scan and open in Raw Scan Viewer"
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-2 text-xs sm:text-sm font-semibold transition-all shadow-md"
            >
              <Scan className="h-4 w-4" />
              Raw Scan
            </button>

            {/* Photocopy button */}
            <button
              onClick={handlePhotocopy}
              disabled={scanning || scanners.length === 0}
              data-testid="photocopy-btn"
              title="Photocopy: Scan page via WIA and print immediately"
              className="inline-flex items-center gap-2 rounded-lg bg-amber-600 hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-2 text-xs sm:text-sm font-semibold transition-all shadow-md"
            >
              <Printer className="h-4 w-4" />
              Photocopy
            </button>

            {/* Preserve photocopy toggle */}
            <label
              className="inline-flex items-center gap-1.5 text-[11px] font-medium text-slate-400 cursor-pointer select-none border border-slate-800 bg-slate-900/60 px-2.5 py-2 rounded-md"
              title="Save photocopy image in Depot scans directory"
            >
              <input
                type="checkbox"
                checked={preservePhotocopyScan}
                onChange={(e) => setPreservePhotocopyScan(e.target.checked)}
                className="rounded bg-slate-950 border-slate-700 text-amber-500 focus:ring-amber-500 h-3.5 w-3.5"
              />
              <span>Keep in Depot</span>
            </label>

            {/* Auto-scan watcher toggle */}
            <button
              onClick={toggleWatcher}
              disabled={scanners.length === 0}
              data-testid="auto-scan-toggle"
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold transition-colors ${
                watcherActive
                  ? "border-emerald-600 bg-emerald-600/20 text-emerald-400 hover:bg-emerald-600/30"
                  : "border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700"
              } disabled:opacity-30 disabled:cursor-not-allowed`}
              title={watcherActive ? `Auto-scan active (${watcherScans} triggered)` : "Auto-scan: watch for documents"}
            >
              {watcherActive ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
              {watcherActive ? `Watching (${watcherScans})` : "Auto-Scan"}
            </button>
          </div>
        </CardContent>
      </Card>

      {/* Processing indicator */}
      {scanning && (
        <div className="flex items-center gap-3 rounded-lg border border-blue-800 bg-blue-950/30 p-4 shadow-lg animate-pulse">
          <Loader2 className="h-5 w-5 animate-spin text-blue-400 shrink-0" />
          <div className="flex flex-col">
            <span className="text-sm font-semibold text-blue-200">{statusMsg || "Processing OCR..."}</span>
            <span className="text-xs text-blue-400">Running on engine: {effectiveBackend}</span>
          </div>
        </div>
      )}

      {/* Error Message */}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-800 bg-red-950/25 p-3.5">
          <XCircle className="h-4 w-4 text-red-400 shrink-0" />
          <span className="text-xs sm:text-sm text-red-300">{error}</span>
        </div>
      )}

      {/* Document Workspace (Split or Stacked View) */}
      {activeImageUrl ? (
        <div className={`grid gap-6 ${splitView ? "lg:grid-cols-2" : "grid-cols-1"}`}>
          {/* Document Preview Pane */}
          <Card className="border-slate-800 bg-slate-900/60 backdrop-blur-md overflow-hidden flex flex-col shadow-xl">
            <CardHeader className="flex flex-row items-center justify-between p-3.5 border-b border-slate-800 bg-slate-950/40">
              <div className="flex items-center gap-2">
                <Scan className="w-4 h-4 text-emerald-400" />
                <span className="text-xs sm:text-sm font-semibold text-slate-100 truncate max-w-[200px]">
                  {droppedFile ? droppedFile.name : lastScan.filename || "Scanned Document"}
                </span>
                {droppedFile && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
                    {(droppedFile.size / 1024).toFixed(0)} KB
                  </span>
                )}
              </div>

              {/* Image Toolbar */}
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => setZoomLevel((z) => Math.max(50, z - 25))}
                  className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200"
                  title="Zoom Out"
                >
                  <ZoomOut className="w-3.5 h-3.5" />
                </button>
                <span className="text-[11px] font-mono text-slate-400 px-1">{zoomLevel}%</span>
                <button
                  onClick={() => setZoomLevel((z) => Math.min(250, z + 25))}
                  className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200"
                  title="Zoom In"
                >
                  <ZoomIn className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setZoomLevel(100)}
                  className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200"
                  title="Reset Zoom"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                </button>
                {droppedFile && (
                  <button
                    onClick={handleClearDroppedFile}
                    className="p-1 rounded hover:bg-slate-800 text-red-400 hover:text-red-300 ml-1"
                    title="Remove file"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </CardHeader>

            <CardContent className="p-0 flex-1 flex items-center justify-center bg-slate-950/80 min-h-[380px] max-h-[600px] overflow-auto">
              <img
                src={activeImageUrl}
                alt="Document preview"
                style={{ width: `${zoomLevel}%` }}
                className="max-h-[580px] object-contain transition-all duration-200"
              />
            </CardContent>
          </Card>

          {/* OCR Result Pane */}
          <div className="flex flex-col">
            <OcrResultPanel
              text={ocrText}
              onChangeText={setOcrText}
              resultData={lastResultData}
              jobId={ocrJobId}
              status={ocrStatus}
              onCopyText={handleCopy}
              onDownloadTxt={handleDownloadTxt}
              onDownloadMd={handleDownloadMd}
            />
          </div>
        </div>
      ) : (
        /* Empty State & Upload Drop Zone */
        <div className="space-y-6">
          <button
            type="button"
            data-testid="drop-zone"
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`flex w-full flex-col items-center justify-center rounded-2xl border-2 border-dashed p-12 cursor-pointer transition-all ${
              dragOver
                ? "border-blue-500 bg-blue-500/10 shadow-lg shadow-blue-500/10"
                : "border-slate-800 bg-slate-950/50 hover:border-slate-600 hover:bg-slate-900/30"
            }`}
          >
            <input ref={fileInputRef} type="file" accept="image/*,.pdf" className="hidden" onChange={handleFilePick} />
            <div className="p-4 rounded-full bg-slate-900 border border-slate-800 mb-4 shadow-inner">
              <Upload className="h-8 w-8 text-blue-400" />
            </div>
            <p className="text-base font-semibold text-slate-200">
              {droppedFile ? droppedFile.name : "Drop an image or PDF document here, or click to browse"}
            </p>
            <p className="text-xs text-slate-400 mt-1.5 max-w-md text-center">
              Supports PNG, JPG, TIFF, and multi-page PDFs. Fast on-device parsing via Indra OCR 1.1 or auto-selected
              engine.
            </p>
          </button>

          {/* If OCR text was previously preserved, show result panel */}
          {ocrText && (
            <OcrResultPanel
              text={ocrText}
              onChangeText={setOcrText}
              resultData={lastResultData}
              jobId={ocrJobId}
              status={ocrStatus}
              onCopyText={handleCopy}
              onDownloadTxt={handleDownloadTxt}
              onDownloadMd={handleDownloadMd}
            />
          )}
        </div>
      )}

      {/* Recent Activity Strip */}
      {ocrJobId && (
        <div className="flex items-center justify-between p-3 rounded-lg border border-slate-800 bg-slate-950/40 text-xs text-slate-400">
          <div className="flex items-center gap-2">
            <RefreshCw className="h-3.5 w-3.5 text-blue-400" />
            <span>Latest Job:</span>
            <span className="font-mono text-slate-300 font-semibold">{ocrJobId}</span>
          </div>
          <button
            onClick={() => navigate("/status")}
            className="text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1"
          >
            <span>View activity logs</span>
            <span>&rarr;</span>
          </button>
        </div>
      )}
    </div>
  );
}
