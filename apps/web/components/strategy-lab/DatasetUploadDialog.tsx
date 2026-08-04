"use client";

import { useState } from "react";

import { uploadCandleDataset, validateCandleCsv, type CsvValidationReport, type StrategyLabDataset } from "@/lib/api/strategyLabOffline";

type Props = {
  onClose: () => void;
  onCreated: (dataset: StrategyLabDataset) => void;
};

export default function DatasetUploadDialog({ onClose, onCreated }: Props) {
  const [csvText, setCsvText] = useState("");
  const [fileName, setFileName] = useState("");
  const [report, setReport] = useState<CsvValidationReport | null>(null);
  const [metadata, setMetadata] = useState({ asset: "", exchange: "", interval: "", name: "" });
  const [status, setStatus] = useState<"idle" | "validating" | "saving" | "error">("idle");
  const [error, setError] = useState("");

  const chooseFile = async (file: File | undefined) => {
    if (!file) return;
    setStatus("validating");
    setError("");
    setFileName(file.name);
    const text = await file.text();
    setCsvText(text);
    try {
      const result = await validateCandleCsv(text);
      setReport(result);
      setMetadata((current) => ({ ...current, name: current.name || file.name.replace(/\.csv$/i, "") }));
      setStatus("idle");
    } catch (reason) {
      setStatus("error");
      setError(reason instanceof Error ? reason.message : "CSV validation failed");
    }
  };

  const save = async () => {
    if (!report?.valid || !metadata.asset || !metadata.exchange || !metadata.interval || !metadata.name) return;
    setStatus("saving");
    setError("");
    try {
      const intervalReport = await validateCandleCsv(csvText, metadata.interval);
      setReport(intervalReport);
      if (!intervalReport.valid) {
        setStatus("error");
        return;
      }
      const created = await uploadCandleDataset({ csv_text: csvText, ...metadata });
      onCreated(created);
    } catch (reason) {
      setStatus("error");
      setError(reason instanceof Error ? reason.message : "Dataset could not be saved");
    }
  };

  const refreshIntervalQuality = async () => {
    if (!csvText || !metadata.interval) return;
    try { setReport(await validateCandleCsv(csvText, metadata.interval)); } catch { /* Save reports the actionable API error. */ }
  };

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" role="dialog" aria-modal="true" aria-labelledby="dataset-upload-title">
    <div className="max-h-[90vh] w-full max-w-3xl overflow-auto border border-[#416357] bg-[#0c1714] p-5 shadow-2xl">
      <div className="flex items-start justify-between gap-4">
        <div><p className="font-mono text-[10px] uppercase text-[#6fa78f]">Immutable offline evidence</p><h2 id="dataset-upload-title" className="mt-1 font-serif text-2xl">Upload Candle CSV</h2></div>
        <button className="lab-button" type="button" onClick={onClose}>Close</button>
      </div>
      <label className="mt-5 block border border-dashed border-[#416357] bg-[#08110f] p-5 text-center font-mono text-xs text-[#aebdb7]">
        <span>{status === "validating" ? "VALIDATING…" : fileName || "Choose an OHLCV CSV"}</span>
        <input className="sr-only" type="file" accept=".csv,text/csv" onChange={(event) => void chooseFile(event.target.files?.[0])} />
      </label>
      <p className="mt-2 font-mono text-[10px] text-[#82978e]">Required columns: timestamp, open, high, low, close, volume</p>
      {report && <>
        <div className="mt-4 grid grid-cols-2 gap-px bg-[#294139] sm:grid-cols-3">
          <QualityDatum label="Total candles" value={report.candle_count.toLocaleString()} />
          <QualityDatum label="Missing candles" value={String(report.missing_candles)} />
          <QualityDatum label="Duplicate timestamps" value={String(report.duplicate_timestamps)} bad={report.duplicate_timestamps > 0} />
          <QualityDatum label="Invalid rows" value={String(report.invalid_rows)} bad={report.invalid_rows > 0} />
          <QualityDatum label="First timestamp" value={report.first_timestamp?.replace("T", " ").slice(0, 19) ?? "—"} />
          <QualityDatum label="Last timestamp" value={report.last_timestamp?.replace("T", " ").slice(0, 19) ?? "—"} />
        </div>
        {report.errors.length > 0 && <div role="alert" className="mt-3 border-l-2 border-[#d95d54] pl-3 font-mono text-[10px] leading-5 text-[#ef8b82]">{report.errors.map((message) => <p key={message}>{message}</p>)}</div>}
        {report.valid && <div className="mt-5">
          <p className="mb-3 font-mono text-[10px] uppercase text-[#6fa78f]">Validation passed · identify this dataset</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {(["asset", "exchange", "interval", "name"] as const).map((field) => <label key={field} className="font-mono text-xs capitalize text-[#aebdb7]">{field === "name" ? "Dataset Name" : field}<input className="lab-input mt-1" value={metadata[field]} placeholder={field === "interval" ? "15m, 1h, 1d…" : undefined} onBlur={field === "interval" ? () => void refreshIntervalQuality() : undefined} onChange={(event) => setMetadata((current) => ({ ...current, [field]: event.target.value }))} /></label>)}
          </div>
          <button className="lab-button mt-4 w-full" disabled={status === "saving" || Object.values(metadata).some((value) => !value.trim())} type="button" onClick={() => void save()}>{status === "saving" ? "Saving immutable dataset…" : "Normalize and save dataset"}</button>
        </div>}
      </>}
      {error && <p role="alert" className="mt-3 font-mono text-xs text-[#ef8b82]">{error}</p>}
    </div>
  </div>;
}

function QualityDatum({ label, value, bad = false }: { label: string; value: string; bad?: boolean }) {
  return <div className="min-w-0 bg-[#08110f] p-3"><p className="font-mono text-[9px] uppercase text-[#82978e]">{label}</p><p className={`mt-1 truncate font-mono text-xs ${bad ? "text-[#ef6b5f]" : "text-[#e6eee9]"}`} title={value}>{value}</p></div>;
}
