/**
 * Minimal, framework-agnostic logging contract shared by the Web and Mobile clients.
 *
 * This module defines the SHAPE of a centralized logger (4 levels + structured
 * entries) but contains NO platform-specific transport: it never touches
 * `console`, the DOM, node or react-native. Each client injects its own `sink`
 * (the real transport lives in the Web/Mobile apps), keeping this package pure.
 *
 * Field names follow the project convention (Vietnamese without diacritics);
 * `capDo` values use the standard English level names per Requirement 30.3.
 */

/** The only four allowed log levels (R30.3). */
export type LogLevel = "ERROR" | "WARN" | "INFO" | "DEBUG";

/** A single structured log entry (R30.2): every required field is non-empty. */
export interface LogEntry {
  /** ISO-8601 timestamp including timezone offset. */
  thoiGian: string;
  capDo: LogLevel;
  /** Screen/module that emitted the entry. */
  nguon: string;
  thongDiep: string;
  /** Optional structured context (ids, operation name, ...). */
  duLieu?: Record<string, unknown>;
}

/** Where finished entries are sent. Injected by each client; never console here. */
export type LogSink = (entry: LogEntry) => void;

/** A scoped logger bound to a single `nguon` (source). */
export interface Logger {
  error(thongDiep: string, duLieu?: Record<string, unknown>): void;
  warn(thongDiep: string, duLieu?: Record<string, unknown>): void;
  info(thongDiep: string, duLieu?: Record<string, unknown>): void;
  debug(thongDiep: string, duLieu?: Record<string, unknown>): void;
}

/**
 * Build a scoped {@link Logger}. The caller supplies the source name and the sink
 * that performs the actual output, so this stays platform-agnostic.
 */
export function createLogger(nguon: string, sink: LogSink): Logger {
  const ghi = (
    capDo: LogLevel,
    thongDiep: string,
    duLieu?: Record<string, unknown>,
  ): void => {
    const entry: LogEntry = {
      thoiGian: new Date().toISOString(),
      capDo,
      nguon,
      thongDiep,
    };
    if (duLieu !== undefined) {
      entry.duLieu = duLieu;
    }
    sink(entry);
  };

  return {
    error: (thongDiep, duLieu) => ghi("ERROR", thongDiep, duLieu),
    warn: (thongDiep, duLieu) => ghi("WARN", thongDiep, duLieu),
    info: (thongDiep, duLieu) => ghi("INFO", thongDiep, duLieu),
    debug: (thongDiep, duLieu) => ghi("DEBUG", thongDiep, duLieu),
  };
}
