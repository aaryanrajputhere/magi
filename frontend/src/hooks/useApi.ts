import { useEffect, useState } from "react";
import { getJson } from "../api/client";

type Resource<T> = { path: string | null } & (
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "error"; message: string }
);

export function useApi<T>(path: string | null) {
  const [resource, setResource] = useState<Resource<T>>({
    path,
    state: "loading",
  });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 10000);
    setResource({ path, state: "loading" });
    getJson<T>(path, controller.signal)
      .then((data) => {
        if (!cancelled) setResource({ path, state: "ready", data });
      })
      .catch((error: unknown) => {
        if (!cancelled)
          setResource({
            path,
            state: "error",
            message: controller.signal.aborted
              ? "The request timed out. Please retry."
              : error instanceof Error
                ? error.message
                : "Could not load sources.",
          });
      })
      .finally(() => window.clearTimeout(timer));
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [path, attempt]);
  const current: Resource<T> =
    resource.path === path ? resource : { path, state: "loading" };
  return { resource: current, retry: () => setAttempt((value) => value + 1) };
}
