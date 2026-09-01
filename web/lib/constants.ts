export const FILTER_KEYS = [
  "date_from",
  "date_to",
  "source_type",
  "product_category",
  "gender_segment",
  "price_tier",
  "platform_used",
  "intent_mode",
  "theme_id",
  "friction_tag",
  "intent_tag",
  "q",
] as const;

export type FilterKey = (typeof FILTER_KEYS)[number];

export type FilterState = Partial<Record<FilterKey, string>>;

export const DRAWER_KEYS = ["document_id", "chunk_id"] as const;

export type DrawerState = {
  document_id?: string;
  chunk_id?: string;
};

export const SEGMENT_DIMENSIONS = [
  "product_category",
  "source_type",
  "gender_segment",
  "price_tier",
  "platform_used",
] as const;

export type SegmentDimension = (typeof SEGMENT_DIMENSIONS)[number];

export const SOURCE_TYPES = [
  "play_store",
  "app_store",
  "reddit",
  "youtube",
  "x",
  "quora",
  "forum",
  "instagram",
  "facebook",
  "myntra_qa",
  "myntra_review",
  "other",
] as const;

/** Connectors that exist in this repo. The rest are ToS / no-API out of scope. */
export const IMPLEMENTED_SOURCES = [
  "play_store",
  "app_store",
  "reddit",
  "youtube",
  "x",
] as const;

export const OUT_OF_SCOPE_SOURCES = SOURCE_TYPES.filter(
  (name) => !(IMPLEMENTED_SOURCES as readonly string[]).includes(name),
);

export const INTENT_MODES = ["bookmark", "stall", "unclear"] as const;

export const NAV_ITEMS = [
  { href: "/overview", label: "Overview", icon: "dashboard" },
  { href: "/themes", label: "Themes", icon: "dataset" },
  { href: "/evidence", label: "Evidence", icon: "fact_check" },
  { href: "/categories", label: "Categories", icon: "category" },
  { href: "/trends", label: "Trends", icon: "trending_up" },
  { href: "/segments", label: "Segments", icon: "groups" },
  { href: "/sources", label: "Sources", icon: "source" },
  { href: "/phrases", label: "Phrases", icon: "chat_bubble" },
  { href: "/reports", label: "Reports", icon: "description" },
] as const;

export const SOURCE_LABELS: Record<string, string> = {
  play_store: "Google Play",
  app_store: "App Store",
  reddit: "Reddit",
  youtube: "YouTube",
  x: "X",
  quora: "Quora",
  forum: "Forum",
  instagram: "Instagram",
  facebook: "Facebook",
  myntra_qa: "Myntra Q&A",
  myntra_review: "Myntra reviews",
  other: "Other",
};

export const SOURCE_ICONS: Record<string, string> = {
  play_store: "shop",
  app_store: "phone_iphone",
  reddit: "forum",
  youtube: "smart_display",
  x: "tag",
  quora: "help",
  forum: "groups",
  instagram: "photo_camera",
  facebook: "public",
  myntra_qa: "storefront",
  myntra_review: "rate_review",
  other: "source",
};

export const COPILOT_SUGGESTIONS = [
  {
    title: "Why add to wishlist?",
    hint: "Waiting for a price drop",
    question: "Why do users add fashion products to their wishlist?",
  },
  {
    title: "Why don't they buy?",
    hint: "Fit, price, delivery",
    question: "What prevents wishlisted products from eventually being purchased?",
  },
  {
    title: "What's still uncertain?",
    hint: "After they like an item",
    question: "What uncertainties remain after users have identified a product they like?",
  },
  {
    title: "Why postpone?",
    hint: "What makes them wait",
    question: "What causes users to postpone a purchase?",
  },
  {
    title: "How do they compare?",
    hint: "Shortlisted products",
    question: "How do users compare multiple shortlisted products?",
  },
  {
    title: "What do they check outside?",
    hint: "Reddit, YouTube, friends",
    question: "What information do users seek outside Myntra/AJIO before purchasing?",
  },
  {
    title: "Fit, price, reviews",
    hint: "What actually stalls them",
    question: "What role do fit, size, styling, price, reviews, occasion and social validation play?",
  },
  {
    title: "Bookmark vs intent",
    hint: "Save for later or buy soon",
    question: "When do users use the wishlist as genuine purchase intent versus simply as a bookmarking mechanism?",
  },
  {
    title: "Do segments differ?",
    hint: "Ethnic, footwear, and more",
    question: "How do these behaviors differ across user segments?",
  },
  {
    title: "Unmet needs",
    hint: "What keeps coming up",
    question: "What unmet needs emerge consistently across user conversations?",
  },
] as const;

export const COPILOT_PROMPTS = COPILOT_SUGGESTIONS.map((item) => item.question);

export const API_KEY_STORAGE = "discovery.apiKey";
export const SESSION_STORAGE = "discovery.copilotSession";
