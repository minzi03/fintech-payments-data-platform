"use client";

import Link from "next/link";

import { ProblemAlert } from "@/components/problem-alert";
import { usePortalSession } from "@/features/auth/session-context";

export function SessionControls() {
  const {
    environments,
    error,
    isMutating,
    logout,
    selectEnvironment,
    selectedEnvironment,
    session,
    state,
  } = usePortalSession();

  if (state === "loading") {
    return (
      <span className="session-status" role="status">
        Checking session…
      </span>
    );
  }
  if (state !== "authenticated" || !session) {
    return (
      <div className="session-controls">
        {state === "error" && error ? <ProblemAlert error={error} /> : null}
        <Link className="button compact" href="/login">
          Sign in
        </Link>
      </div>
    );
  }

  return (
    <div className="session-controls">
      {error ? <ProblemAlert error={error} /> : null}
      <label className="environment-selector">
        <span className="sr-only">Authorized environment</span>
        <select
          aria-label="Authorized environment"
          disabled={isMutating}
          onChange={(event) => void selectEnvironment(event.target.value)}
          value={selectedEnvironment ?? ""}
        >
          <option disabled value="">
            Choose environment
          </option>
          {environments.map((environment) => (
            <option key={environment.environment_id} value={environment.environment_id}>
              {environment.display_name}
            </option>
          ))}
        </select>
      </label>
      <span className="principal-label" title={session.principal_reference}>
        Signed in
      </span>
      <button
        className="text-button"
        disabled={isMutating}
        onClick={() => void logout()}
        type="button"
      >
        Sign out
      </button>
    </div>
  );
}
