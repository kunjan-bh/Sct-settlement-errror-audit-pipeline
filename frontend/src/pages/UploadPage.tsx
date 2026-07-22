import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { FiUploadCloud, FiCheckCircle, FiFile } from "react-icons/fi";
import { batchesApi } from "../lib/api";

const PROCESSING_MESSAGES = [
  "Analyzing settlement records...",
  "Classifying aggregators...",
  "Grouping issues...",
  "Generating dashboard...",
  "Saving batch...",
];

type Stage = "idle" | "selected" | "processing" | "done" | "error";

export default function UploadPage() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);

  const [stage, setStage] = useState<Stage>("idle");
  const [dragActive, setDragActive] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [messageIndex, setMessageIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [batchId, setBatchId] = useState<number | null>(null);

  const handleFile = (f: File) => {
    if (!f.name.endsWith(".xlsx") && !f.name.endsWith(".xls")) {
      setError("Please upload a .xlsx or .xls file");
      return;
    }
    setError(null);
    setFile(f);
    setStage("selected");
  };

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }, []);

  const startProcessing = async () => {
    if (!file) return;
    setStage("processing");
    setError(null);

    // Rotate through the status messages while the real request is in
    // flight. If the backend responds before we've cycled through all of
    // them, that's fine -- we just stop rotating and show the result.
    let i = 0;
    const interval = setInterval(() => {
      i = Math.min(i + 1, PROCESSING_MESSAGES.length - 1);
      setMessageIndex(i);
    }, 900);

    try {
      const result = await batchesApi.upload(file);
      clearInterval(interval);
      setMessageIndex(PROCESSING_MESSAGES.length - 1);
      setBatchId(result.batch.id);
      setStage("done");
      setTimeout(() => navigate(`/dashboard/${result.batch.id}`), 1100);
    } catch (e) {
      clearInterval(interval);
      setError(e instanceof Error ? e.message : "Upload failed");
      setStage("error");
    }
  };

  return (
    <div className="max-w-2xl mx-auto px-8 py-16">
      <h1 className="text-2xl font-semibold text-neutral-900 mb-1">Start New Batch</h1>
      <p className="text-neutral-500 mb-8">Upload a SmartQR settlement Excel to process it.</p>

      <AnimatePresence mode="wait">
        {(stage === "idle" || stage === "selected" || stage === "error") && (
          <motion.div
            key="dropzone"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
          >
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
              className={`relative cursor-pointer rounded-2xl border-2 border-dashed p-14 text-center transition-all duration-300 ${
                dragActive
                  ? "border-neutral-900 bg-neutral-50"
                  : "border-neutral-300 hover:border-neutral-400"
              }`}
              style={
                dragActive
                  ? { boxShadow: "0 0 0 6px rgba(23,23,23,0.06), 0 0 40px rgba(23,23,23,0.08)" }
                  : undefined
              }
            >
              <input
                ref={inputRef}
                type="file"
                accept=".xlsx,.xls"
                className="hidden"
                onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
              />
              <motion.div
                animate={dragActive ? { y: [-2, 2, -2] } : { y: 0 }}
                transition={{ repeat: dragActive ? Infinity : 0, duration: 1.2 }}
                className="flex flex-col items-center gap-3"
              >
                {file ? (
                  <FiFile className="w-10 h-10 text-neutral-700" />
                ) : (
                  <FiUploadCloud className="w-10 h-10 text-neutral-400" />
                )}
                <p className="text-neutral-700 font-medium">
                  {file ? file.name : "Drag & drop settlement Excel here"}
                </p>
                {!file && <p className="text-neutral-400 text-sm">or click to browse — .xlsx / .xls</p>}
              </motion.div>
            </div>

            {error && <p className="text-red-600 text-sm mt-3">{error}</p>}

            {file && stage === "selected" && (
              <motion.button
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                onClick={startProcessing}
                className="mt-6 w-full bg-neutral-900 text-white rounded-lg py-3 font-medium hover:bg-neutral-800 transition-colors"
              >
                Process Batch
              </motion.button>
            )}
          </motion.div>
        )}

        {stage === "processing" && (
          <motion.div
            key="processing"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="rounded-2xl border border-neutral-200 p-14 flex flex-col items-center gap-6 overflow-hidden relative"
          >
            <ShimmerBar />
            <AnimatePresence mode="wait">
              <motion.p
                key={messageIndex}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.3 }}
                className="text-neutral-600 font-medium"
              >
                {PROCESSING_MESSAGES[messageIndex]}
              </motion.p>
            </AnimatePresence>
          </motion.div>
        )}

        {stage === "done" && (
          <motion.div
            key="done"
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            className="rounded-2xl border border-green-200 bg-green-50 p-14 flex flex-col items-center gap-3"
          >
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ type: "spring", stiffness: 300, damping: 15 }}
            >
              <FiCheckCircle className="w-12 h-12 text-green-600" />
            </motion.div>
            <p className="text-green-800 font-medium">Report Ready ✓</p>
            <p className="text-green-600 text-sm">Opening dashboard{batchId ? ` for batch #${batchId}` : ""}…</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** A slow-moving gradient sweep behind the processing messages -- the
 * "Claude thinking" feel your spec asked for, without pulling in a heavy
 * animation library beyond framer-motion (already installed). */
function ShimmerBar() {
  return (
    <motion.div
      className="absolute inset-0 opacity-40"
      style={{
        background:
          "linear-gradient(90deg, transparent, rgba(23,23,23,0.06), transparent)",
        backgroundSize: "200% 100%",
      }}
      animate={{ backgroundPosition: ["0% 0%", "200% 0%"] }}
      transition={{ repeat: Infinity, duration: 2.2, ease: "linear" }}
    />
  );
}
