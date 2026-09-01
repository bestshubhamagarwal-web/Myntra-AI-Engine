"use client";

import { FormEvent, useState } from "react";

import { Icon } from "@/components/Icon";
import { fetchHealth, setStoredApiKey } from "@/lib/api";
import { ApiError } from "@/lib/types";

export function AuthGate({
  onUnlocked,
}: {
  onUnlocked: () => void;
}) {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setStoredApiKey(secret.trim());
    try {
      await fetchHealth();
      onUnlocked();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Invalid shared secret.");
        return;
      }
      const detail = err instanceof ApiError ? err.message : "";
      setError(
        detail || "Query API unreachable. Start it with python -m src.cli serve.",
      );
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-level-0 p-8">
      <form
        onSubmit={onSubmit}
        className="card-surface w-full max-w-md rounded-lg p-8"
      >
        <div className="mb-6 flex items-center gap-3">
          <Icon name="lock" className="text-primary" />
          <div>
            <h1 className="font-headline-md text-headline-md text-on-surface">Discovery</h1>
            <p className="font-body-md text-body-md text-on-surface-variant">
              Enter the API shared secret to continue.
            </p>
          </div>
        </div>
        <label className="block font-label-md text-label-md text-on-surface-variant" htmlFor="secret">
          Shared secret
        </label>
        <input
          id="secret"
          type="password"
          autoComplete="off"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          className="focus-ring mt-2 w-full rounded-md border border-hairline bg-surface px-3 py-2 font-body-md text-body-md"
        />
        {error ? <p className="mt-3 font-body-md text-body-md text-error">{error}</p> : null}
        <button
          type="submit"
          className="mt-6 w-full rounded-md bg-primary py-2 font-label-md text-label-md text-on-primary hover:bg-primary-container"
        >
          Unlock
        </button>
      </form>
    </div>
  );
}
