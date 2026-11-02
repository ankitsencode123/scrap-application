import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/_authenticated/profile")({
  head: () => ({ meta: [{ title: "Profile — scrappe" }, { name: "description", content: "Profile on scrappe." }, { property: "og:title", content: "Profile — scrappe" }, { property: "og:description", content: "Profile on scrappe." }] }),
  component: () => (
    <div className="rounded-3xl border border-dashed p-10 text-center">
      <h1 className="text-2xl font-extrabold">Profile</h1>
      <p className="mt-2 text-muted-foreground">Coming next.</p>
    </div>
  ),
});
