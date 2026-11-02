
CREATE TABLE public.profiles (
  id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  full_name text NOT NULL DEFAULT '',
  phone text,
  city text,
  account_type text NOT NULL DEFAULT 'seller' CHECK (account_type IN ('seller','collector')),
  avatar_url text,
  created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE ON public.profiles TO authenticated;
GRANT ALL ON public.profiles TO service_role;
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "profiles readable by signed in" ON public.profiles FOR SELECT TO authenticated USING (true);
CREATE POLICY "own profile insert" ON public.profiles FOR INSERT TO authenticated WITH CHECK (id = auth.uid());
CREATE POLICY "own profile update" ON public.profiles FOR UPDATE TO authenticated USING (id = auth.uid());

CREATE OR REPLACE FUNCTION public.handle_new_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  INSERT INTO public.profiles (id, full_name, account_type)
  VALUES (NEW.id,
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', ''),
    CASE WHEN NEW.raw_user_meta_data->>'account_type' = 'collector' THEN 'collector' ELSE 'seller' END)
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END $$;
CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

CREATE TABLE public.listings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  seller_id uuid NOT NULL,
  collector_id uuid,
  title text NOT NULL,
  category text NOT NULL DEFAULT 'other',
  weight_kg numeric NOT NULL DEFAULT 0,
  expected_price numeric,
  final_price numeric,
  description text,
  photo_path text,
  address text,
  lat double precision,
  lng double precision,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','accepted','on_the_way','arrived','completed','cancelled')),
  created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON public.listings TO authenticated;
GRANT ALL ON public.listings TO service_role;
ALTER TABLE public.listings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "listings readable" ON public.listings FOR SELECT TO authenticated USING (true);
CREATE POLICY "seller inserts" ON public.listings FOR INSERT TO authenticated WITH CHECK (seller_id = auth.uid());
CREATE POLICY "seller or collector updates" ON public.listings FOR UPDATE TO authenticated USING (seller_id = auth.uid() OR collector_id = auth.uid());
CREATE POLICY "seller deletes" ON public.listings FOR DELETE TO authenticated USING (seller_id = auth.uid());

CREATE TABLE public.offers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  listing_id uuid NOT NULL REFERENCES public.listings(id) ON DELETE CASCADE,
  collector_id uuid NOT NULL,
  amount numeric NOT NULL,
  message text,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
  created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE ON public.offers TO authenticated;
GRANT ALL ON public.offers TO service_role;
ALTER TABLE public.offers ENABLE ROW LEVEL SECURITY;
CREATE POLICY "offers visible to parties" ON public.offers FOR SELECT TO authenticated USING (
  collector_id = auth.uid() OR EXISTS (SELECT 1 FROM public.listings l WHERE l.id = listing_id AND l.seller_id = auth.uid()));
CREATE POLICY "collector makes offer" ON public.offers FOR INSERT TO authenticated WITH CHECK (collector_id = auth.uid());
CREATE POLICY "seller updates offer" ON public.offers FOR UPDATE TO authenticated USING (
  EXISTS (SELECT 1 FROM public.listings l WHERE l.id = listing_id AND l.seller_id = auth.uid()));

CREATE TABLE public.messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  listing_id uuid NOT NULL REFERENCES public.listings(id) ON DELETE CASCADE,
  sender_id uuid NOT NULL,
  recipient_id uuid NOT NULL,
  body text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON public.messages TO authenticated;
GRANT ALL ON public.messages TO service_role;
ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY "message parties read" ON public.messages FOR SELECT TO authenticated USING (sender_id = auth.uid() OR recipient_id = auth.uid());
CREATE POLICY "sender writes" ON public.messages FOR INSERT TO authenticated WITH CHECK (sender_id = auth.uid());
ALTER PUBLICATION supabase_realtime ADD TABLE public.messages;

CREATE TABLE public.reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  listing_id uuid NOT NULL REFERENCES public.listings(id) ON DELETE CASCADE,
  reviewer_id uuid NOT NULL,
  reviewee_id uuid NOT NULL,
  rating int NOT NULL CHECK (rating BETWEEN 1 AND 5),
  comment text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (listing_id, reviewer_id)
);
GRANT SELECT, INSERT ON public.reviews TO authenticated;
GRANT ALL ON public.reviews TO service_role;
ALTER TABLE public.reviews ENABLE ROW LEVEL SECURITY;
CREATE POLICY "reviews readable" ON public.reviews FOR SELECT TO authenticated USING (true);
CREATE POLICY "reviewer writes" ON public.reviews FOR INSERT TO authenticated WITH CHECK (reviewer_id = auth.uid());

CREATE POLICY "scrap photos signed-in read" ON storage.objects FOR SELECT TO authenticated USING (bucket_id = 'scrap-photos');
CREATE POLICY "scrap photos own upload" ON storage.objects FOR INSERT TO authenticated WITH CHECK (bucket_id = 'scrap-photos' AND (storage.foldername(name))[1] = auth.uid()::text);
