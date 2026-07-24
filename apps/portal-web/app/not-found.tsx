import Link from "next/link";

export default function NotFound() {
  return (
    <section className="surface centered-state">
      <p className="eyebrow">404</p>
      <h1>Workspace page not found</h1>
      <p>The requested Portal capability does not exist in this foundation release.</p>
      <Link className="button" href="/">
        Return home
      </Link>
    </section>
  );
}
