"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { portalApi } from "@/api/portal-api";
import { portalQueryKeys } from "@/api/query-keys";
import { usePortalSession } from "@/features/auth/session-context";

const publicNavigation = [
  { href: "/", label: "Home" },
  { href: "/system-status", label: "System Status" },
];

function isSafeApplicationPath(path: string): boolean {
  return path.startsWith("/") && !path.startsWith("//") && !path.includes("\\");
}

export function AuthorizedNavigation({ includeDeveloper }: { includeDeveloper: boolean }) {
  const { capabilities, selectedEnvironment, state } = usePortalSession();
  const navigationQuery = useQuery({
    queryKey: portalQueryKeys.navigation(selectedEnvironment ?? undefined),
    queryFn: () => portalApi.navigation(selectedEnvironment as string),
    enabled: state === "authenticated" && selectedEnvironment !== null,
  });
  const available = new Set(
    capabilities
      .filter((capability) => capability.state === "AVAILABLE")
      .map((capability) => capability.capability_id),
  );
  const governedNavigation =
    navigationQuery.data?.items
      .filter((item) => available.has(item.capability_id) && isSafeApplicationPath(item.path))
      .map((item) => ({ href: item.path, label: item.label })) ?? [];
  const links =
    state === "authenticated" && selectedEnvironment !== null
      ? governedNavigation
      : publicNavigation;
  const visibleLinks =
    includeDeveloper && state === "authenticated"
      ? [...links, { href: "/developer", label: "Developer" }]
      : links;

  return (
    <ul>
      {visibleLinks.map((link) => (
        <li key={link.href}>
          <Link href={link.href}>{link.label}</Link>
        </li>
      ))}
    </ul>
  );
}
