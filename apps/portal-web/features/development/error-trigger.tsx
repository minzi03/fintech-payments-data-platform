"use client";

import { useState } from "react";

export function ErrorTrigger() {
  const [shouldThrow, setShouldThrow] = useState(false);
  if (shouldThrow) {
    throw new Error("Development-only error-boundary probe");
  }
  return (
    <button className="button danger" type="button" onClick={() => setShouldThrow(true)}>
      Trigger safe UI error
    </button>
  );
}
