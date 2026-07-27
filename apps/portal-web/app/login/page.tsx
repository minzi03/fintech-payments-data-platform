import type { Metadata } from "next";

export const metadata: Metadata = { title: "Sign in" };

type LoginPageProps = {
  searchParams: Promise<{ failed?: string; return_to?: string }>;
};

function boundedReturnPath(value: string | undefined): string {
  if (!value || value.length > 512) {
    return "/";
  }
  return value;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const parameters = await searchParams;
  return (
    <div className="centered-state auth-state">
      <p className="eyebrow">Server-governed authentication</p>
      <h1>Sign in to the Portal</h1>
      <p>
        Identity verification and authorization are completed by the Portal API. Provider tokens are
        never returned to browser application code.
      </p>
      {parameters.failed ? (
        <p className="problem-alert" role="alert">
          Sign-in could not be started. Try again or contact the platform maintainer.
        </p>
      ) : null}
      <form action="/auth/start" method="post">
        <input name="return_to" type="hidden" value={boundedReturnPath(parameters.return_to)} />
        <button className="button" type="submit">
          Continue with the configured identity provider
        </button>
      </form>
      <p className="auth-note">
        The provider is selected by the server. This page does not accept provider identifiers,
        roles, or environment authority from the browser.
      </p>
    </div>
  );
}
