import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { Camera, HandCoins, Truck, Star } from "lucide-react";
import hero from "@/assets/hero-scrap.jpg";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/AppShell";
import { useAuth } from "@/hooks/use-auth";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "scrappe — Sell your scrap from home" },
      { name: "description", content: "Upload a photo of your scrap, get price offers from local collectors, and get it picked up at your door." },
      { property: "og:title", content: "scrappe — Sell your scrap from home" },
      { property: "og:description", content: "Upload a photo of your scrap, get price offers from local collectors, and get it picked up at your door." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Landing,
});

const STEPS = [
  { icon: Camera, title: "Snap & post", text: "Photo, weight and a few details." },
  { icon: HandCoins, title: "Get offers", text: "Nearby collectors bid a price." },
  { icon: Truck, title: "Doorstep pickup", text: "Track them live till they arrive." },
  { icon: Star, title: "Rate", text: "Honest reviews keep everyone fair." },
];

function Landing() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  useEffect(() => {
    if (!loading && user) navigate({ to: "/home" });
  }, [user, loading, navigate]);

  return (
    <div className="min-h-screen">
      <header className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4">
        <Logo />
        <Button asChild variant="ghost"><Link to="/auth">Sign in</Link></Button>
      </header>
      <section className="mx-auto grid max-w-6xl gap-8 px-4 pb-10 pt-2 md:grid-cols-2 md:items-center md:pt-10">
        <div className="order-2 md:order-1">
          <p className="mb-3 inline-block rounded-full bg-secondary px-3 py-1 text-xs font-semibold text-secondary-foreground">
            Raddi, kabaad, e-waste — all of it
          </p>
          <h1 className="text-4xl font-extrabold leading-[1.05] md:text-6xl">
            Your scrap has a price. <span className="text-primary">Get paid at your door.</span>
          </h1>
          <p className="mt-4 max-w-md text-lg text-muted-foreground">
            Post a photo, let local collectors make offers, and pick the best one.
          </p>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row">
            <Button asChild size="lg" variant="accent"><Link to="/auth" search={{ type: "seller" }}>Sell my scrap</Link></Button>
            <Button asChild size="lg" variant="outline"><Link to="/auth" search={{ type: "collector" }}>I'm a collector</Link></Button>
          </div>
        </div>
        <div className="order-1 md:order-2">
          <img src={hero} alt="Bundled newspapers, cardboard and steel ready for pickup" width={1280} height={960}
            className="aspect-[4/3] w-full rounded-3xl object-cover shadow-soft" />
        </div>
      </section>
      <section className="bg-hero py-12 text-primary-foreground">
        <div className="mx-auto grid max-w-6xl grid-cols-2 gap-4 px-4 md:grid-cols-4">
          {STEPS.map((s, i) => (
            <div key={s.title} className="rounded-2xl bg-primary-foreground/10 p-4">
              <s.icon className="mb-3 size-6 text-accent" />
              <div className="text-xs opacity-70">Step {i + 1}</div>
              <h3 className="text-lg font-bold">{s.title}</h3>
              <p className="text-sm opacity-80">{s.text}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
