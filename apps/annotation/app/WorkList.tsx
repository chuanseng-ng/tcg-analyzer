"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
  listImagesAwaitingAnnotation,
  type AnnotationWorkListResponse,
  type AnnotationImageSummary,
} from "@/lib/api";
import {
  classifyAnnotationFailure,
  FAILURE_MESSAGE,
  isWorthRetrying,
  type AnnotationFailure,
} from "@/lib/annotation-errors";

import styles from "./page.module.css";

export const PAGE_SIZE = 25;

type State =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly page: AnnotationWorkListResponse }
  | { readonly status: "failed"; readonly failure: AnnotationFailure };

function offsetFrom(params: URLSearchParams): number {
  const raw = Number.parseInt(params.get("offset") ?? "0", 10);
  return Number.isFinite(raw) && raw > 0 ? raw : 0;
}

/**
 * The images nobody has annotated yet.
 *
 * Which images those are is the service's answer, not this component's: an image
 * is waiting when it carries neither a defect marker nor a centering
 * measurement, and both tables are checked there. Nothing here filters, because
 * a second opinion about what "annotated" means is how the two would disagree.
 */
export function WorkList() {
  const router = useRouter();
  const params = useSearchParams();
  const offset = offsetFrom(new URLSearchParams(params.toString()));
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });

    listImagesAwaitingAnnotation({ limit: PAGE_SIZE, offset }, controller.signal)
      .then((page) => {
        setState({ status: "ready", page });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ status: "failed", failure: classifyAnnotationFailure(error) });
      });

    return () => {
      controller.abort();
    };
  }, [offset, attempt]);

  const goTo = useCallback(
    (next: number) => {
      router.push(next <= 0 ? "/" : `/?offset=${String(next)}`);
    },
    [router],
  );

  if (state.status === "loading") {
    return <p className={styles.pending}>Reading the corpus…</p>;
  }

  if (state.status === "failed") {
    return (
      <div className={styles.notice} role="alert">
        <p>{FAILURE_MESSAGE[state.failure]}</p>
        {isWorthRetrying(state.failure) ? (
          <button
            className={styles.secondary}
            type="button"
            onClick={() => {
              setAttempt((count) => count + 1);
            }}
          >
            Try again
          </button>
        ) : null}
      </div>
    );
  }

  const { images, total } = state.page;

  return (
    <>
      <div>
        <h1 className={styles.heading}>Images awaiting annotation</h1>
        <p className={styles.subheading}>
          {total === 0
            ? "Nothing is waiting."
            : `${String(total)} image${total === 1 ? "" : "s"} with no defect marker and no centering measurement.`}
        </p>
      </div>

      {images.length === 0 ? (
        <p className={styles.empty}>
          {total === 0
            ? "Every image in the corpus has been annotated. Ingest more with tcg-ingest-training-images."
            : "This page is past the end of the queue."}
        </p>
      ) : (
        <ul className={styles.list}>
          {images.map((image) => (
            <WorkItem key={image.id} image={image} />
          ))}
        </ul>
      )}

      <nav className={styles.paging} aria-label="Pages">
        <button
          className={styles.secondary}
          type="button"
          disabled={offset === 0}
          onClick={() => {
            goTo(offset - PAGE_SIZE);
          }}
        >
          Previous
        </button>
        <button
          className={styles.secondary}
          type="button"
          disabled={offset + images.length >= total}
          onClick={() => {
            goTo(offset + PAGE_SIZE);
          }}
        >
          Next
        </button>
      </nav>
    </>
  );
}

function WorkItem({ image }: { image: AnnotationImageSummary }) {
  return (
    <li className={styles.item}>
      <Link className={styles.itemLink} href={`/images/${image.id}`}>
        <span className={styles.itemSide}>{image.side.replace(/_/g, " ")}</span>
        <span className={styles.itemMeta}>
          {image.source.replace(/_/g, " ")} · ingested{" "}
          {new Date(image.created_at).toISOString().slice(0, 10)}
        </span>
        {/*
         * Said on every row rather than only on the exceptions. An annotator who
         * has to notice an *absent* badge to know they are looking at a raw
         * photograph will eventually not notice — and a coordinate taken against
         * a photograph is not comparable with one taken against an artifact.
         */}
        <span className={image.has_artifact ? styles.badgeArtifact : styles.badgeOriginal}>
          {image.has_artifact ? "normalized artifact" : "photograph only"}
        </span>
      </Link>
    </li>
  );
}
