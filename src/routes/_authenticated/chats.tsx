import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/_authenticated/chats")({
  head: () => ({ meta: [{ title: "Chats — scrappe" }, { name: "description", content: "Chats on scrappe." }, { property: "og:title", content: "Chats — scrappe" }, { property: "og:description", content: "Chats on scrappe." }] }),
  component: () => (
    <div className="rounded-3xl border border-dashed p-10 text-center">
      <h1 className="text-2xl font-extrabold">Chats</h1>
      <p className="mt-2 text-muted-foreground">Coming next.</p>
    </div>
  ),
});
