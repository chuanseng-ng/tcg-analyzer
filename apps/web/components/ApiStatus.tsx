"use client";

import { useEffect, useState } from "react";

import { getHealth } from "@/lib/api";

import styles from "./ApiStatus.module.css";

type State = "checking" | "reachable" | "unreachable";

const LABELS: Record<State, string> = {
  checking: "Checking the analysis API…",
  reachable: "Analysis API reachable",
  unreachable: "Analysis API unreachable",
};

/**
 * Connectivity indicator for the FastAPI service.
 *
 * This component must degrade rather than fail: the API being down is a normal
 * condition during development and a survivable one in production, so every
 * rejection is absorbed here and reported as text. It never throws, and it
 * never gates the rest of the page on a successful check.
 */
export function ApiStatus() {
  const [state, setState] = useState<State>("checking");
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    getHealth(controller.signal)
      .then((health) => {
        if (!active) return;
        setVersion(health.application_version);
        setState("reachable");
      })
      .catch(() => {
        // Deliberately swallowed — the state below is the whole error report.
        if (!active) return;
        setVersion(null);
        setState("unreachable");
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, []);

  return (
    <p className={styles.status} role="status" aria-live="polite">
      <span className={styles.dot} data-state={state} aria-hidden="true" />
      <span>{LABELS[state]}</span>
      {version === null ? null : <span className={styles.version}>version {version}</span>}
    </p>
  );
}
