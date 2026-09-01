# Problem Statement

**Project:** AI-Powered Discovery Engine for Myntra Wishlist Behavior

---

## 1. Objective

Build an **AI-native research system** — not a manual review-reading exercise — that ingests unstructured public conversation data about Myntra and online fashion shopping, and converts it into structured, quantified, queryable insight about **wishlist-to-purchase behavior**.

### User-facing surfaces

| Surface | Name | Purpose |
| --- | --- | --- |
| A | **Insight Copilot** (RAG chatbot) | Lets the PM / Growth team ask open-ended questions in natural language and get grounded, citation-backed answers from the review/comment corpus. |
| B | **Analytics Dashboard** | Visualizes the corpus at scale — volume, categories, themes, sentiment, keyword/phrase frequency, trends over time, and auto-generated reports. |

### What the engine must do (beyond summarization)

The Discovery Engine must go beyond summarization or generic sentiment tagging. It must:

- Identify recurring **behavioral patterns** and **friction points** specific to Myntra users
- Cluster them into named **opportunity areas** (themes)
- **Quantify** each theme (share of mentions, sentiment, source diversity, trend direction)
- Allow **segment-level comparison** (e.g. by category, gender, price tier, platform, region if inferable)
- Support **drill-down** from a high-level stat down to the literal user quotes behind it

### Explicit framing

- The underlying **user problem** (why Myntra wishlisted items don't convert) is **not assumed**. It must be **discovered through evidence**.
- No monetary-incentive-related themes need be filtered out at this stage. Discovery should surface every real friction point; feasibility under a no-monetary-incentive constraint is only applied later when prioritizing which opportunity areas to act on.

---

## 2. Research questions the engine must answer

All questions are scoped to **Myntra users**. Every answer must be backed by:

1. Representative verbatim-adjacent **evidence**
2. An estimated **frequency / share of voice**
3. A **confidence / data-density** indicator (how many independent sources support the claim)

### Behavioral / motivational

| ID | Question |
| --- | --- |
| **Q1** | Why do users add fashion products to their Myntra wishlist in the first place? (bookmarking vs. saving-for-later vs. price-watch vs. mood board vs. indecision parking) |
| **Q2** | At what point does a Myntra wishlist item "die" — i.e., what causes users to postpone or abandon the purchase after wishlisting? |
| **Q3** | What residual uncertainties remain **after** a user has already picked a Myntra product they like enough to wishlist? (fit, quality, return risk, authenticity, styling doubt, "is this really worth it") |
| **Q4** | How do users compare multiple shortlisted / wishlisted Myntra products against each other before deciding? |
| **Q5** | What information do users seek **outside Myntra** before purchasing? (Reddit threads, YouTube try-on hauls, influencer reviews, size-chart cross-referencing, brand's own site, resale/authenticity checks, competitor apps for price/size comparison) |
| **Q6** | What role do the following play in the stall between wishlist and purchase on Myntra: fit/size confidence, styling/occasion fit, price sensitivity, review credibility, social validation/FOMO, return/exchange policy trust? |
| **Q7** | When is the Myntra wishlist used as genuine **near-term purchase intent** versus a **passive bookmarking / inspiration** tool with no purchase timeline? |
| **Q8** | How do these behaviors differ across segments on Myntra (category — ethnic vs. western vs. footwear vs. accessories; gender; price tier — budget vs. premium; platform — app vs. web; occasion-driven vs. everyday)? |
| **Q9** | What unmet needs recur across many independent Myntra users/sources (i.e., are **structural**, not anecdotal)? |

---

## 3. Data sources to ingest

**Primary focus: Myntra.** Competitor / platform mentions (AJIO, Nykaa Fashion, Flipkart Fashion, Meesho) are retained only where they appear inside a Myntra-relevant conversation (e.g. a user comparing Myntra to AJIO before buying). They are **not** scraped as a parallel corpus in their own right.

### Source list

- Myntra reviews on the **Apple App Store**
- Myntra reviews on the **Google Play Store**
- **Reddit** (`r/IndianFashionAddicts`, `r/FashionReps`, `r/femalefashionadvice`, `r/malefashionadvice`, r/india shopping threads, city-specific subreddits), filtered/searched for Myntra-specific mentions
- Fashion/shopping forums and communities, and **Quora** threads on "is Myntra reliable / is Myntra sizing accurate / Myntra returns"
- Social media conversations (**X/Twitter** complaint threads, **Instagram** comment sections on Myntra's own posts, relevant public **Facebook** groups)
- **YouTube** comments on: Myntra haul videos, "Myntra vs X" comparison videos, Myntra try-on videos, Myntra size-guide videos, unboxing videos
- Myntra's own on-platform **product Q&A** sections and review text (especially size/fit questions, "does this run small" type Q&A)
- Any other publicly accessible conversation specifically about Myntra shopping friction, indecision, or wishlist/cart behavior

### Privacy constraints

- Only **publicly available, non-personal, aggregate-level** data is used
- No scraping of private / authenticated content
- No PII retention
- Reviewer usernames are **hashed / dropped** before entering the analysis layer

---

## 4. System architecture

### Ingestion layer

- Scrapers / connectors per source:
  - Apify / Playwright / PRAW for Reddit
  - Google-Play-Scraper & App Store scraper libs for Myntra's app pages
  - YouTube Data API for comments
  - X API / snscrape where permitted
- Orchestration via **n8n** or a Zapier/Make workflow; scheduled daily / weekly incremental pulls
- Raw storage: object store / raw table with `source`, `url`, `timestamp`, `platform`, `category_guess`, `raw_text`

### Normalization & enrichment layer

- Language detection + translation normalization (**Hinglish** / regional language handling, since a large share of Myntra reviews are Hinglish)
- Deduplication, spam/bot filtering, boilerplate removal
- Metadata tagging: source type, platform, inferred product category (dress / kurta / shoes / accessories / etc.), inferred gender segment, inferred price tier, review date, star rating (where available)
- PII scrub

### AI analysis layer

Where Claude / LLM does the heavy lifting.

- Chunking + embedding of normalized text into a vector store (e.g. pgvector / Chroma / Pinecone)
- LLM-based structured extraction pass (Claude / GPT) per document:
  - `intent_tag` — why wishlisted, if inferable
  - `friction_tag` — why not purchased, if inferable
  - `entities` — category, brand, occasion, size/fit mention, price mention, competitor mention
  - `sentiment` — granular: trust, delight, frustration, doubt
  - `verbatim_quote` — short, attributable evidence span
- Theme clustering: embedding-based clustering (HDBSCAN / k-means on embeddings) + LLM labeling of each cluster into a human-readable **opportunity area** name
- Quantification layer — for each theme compute:
  - Share of voice (% of corpus mentioning it)
  - Source diversity (# distinct sources / platforms)
  - Sentiment skew
  - Trend over time (rising / flat / declining mention volume)
  - Segment cut (which category / segment it concentrates in)
- Output stored as structured rows in an analytics DB (**Postgres**) that both the dashboard and the RAG chatbot read from

### Surface A: RAG chatbot ("Insight Copilot")

- Retrieval over the vector store (semantic search on raw quotes + structured tags as metadata filters, e.g. `category=footwear AND friction_tag=fit_uncertainty`)
- LLM (Claude) generates grounded answers to PM questions (Section 2) with inline citations back to source snippets and counts
- Must support **comparative / quantitative** questions, not just retrieval  
  Example: *"compare footwear vs. ethnic-wear wishlist drop-off reasons on Myntra"* → pulls structured aggregates, not just raw text
- Must state confidence / evidence volume, and must **decline or caveat** when evidence is thin

### Surface B: Analytics dashboard

Must include, at minimum:

| View | Description |
| --- | --- |
| **Corpus overview** | Total reviews/comments scraped, by source, by date, refreshed on schedule |
| **Category breakdown** | Volume and sentiment by product category (dresses, ethnic wear, footwear, accessories, etc.) |
| **Theme / opportunity-area explorer** | Ranked list of discovered themes with share of voice, sentiment, trend sparkline, and "drill into quotes" action |
| **Word / phrase frequency** | Word cloud + top n-gram frequency table per category and per theme, filterable by sentiment |
| **Sentiment trend** | Over time, overall and per theme / category |
| **Segment comparison** | Cross-tab of themes × segments (category, price tier, platform, gender-if-inferable) |
| **Source / platform breakdown** | Which sources contribute which themes (e.g. sizing complaints concentrate in Play Store reviews; styling indecision concentrates in Reddit / YouTube) |
| **Automated reporting** | Scheduled (e.g. weekly) auto-generated summary report (LLM-written narrative + charts) emailed / exported as PDF, highlighting new / rising themes since last period |
| **Raw evidence table** | Searchable / filterable table of scraped items with tags, for audit and qualitative spot-checking |

---

## 5. Analytical rigor requirements

- Every opportunity area must be reported with a **quantified estimate** (share of voice / mention count), not just "many users said…". Note explicitly in the report / dashboard when a source's data is unavailable or unreliable for a given estimate, rather than interpolating a number.
- Opportunity areas must be prioritized using a simple, transparent scoring framework, e.g.:

  ```
  Impact Score = (Share of Voice) × (Sentiment Severity) × (Segment Breadth) × (Data Confidence)
  ```

- Distinguish **correlation from causation** in the writeup: the engine surfaces "what users say/do," not proof of causal drop-off drivers. Flag where a theme is a **hypothesis** needing product-analytics triangulation (e.g. Myntra funnel data, session replays) before being treated as validated.
- Explicitly separate **bookmarking behavior (Q7)** from **stalled purchase intent** wherever the data allows, since these imply very different downstream solutions.

---

## 6. Deliverables

1. Working **data ingestion pipeline** (or documented workflow if using n8n / Zapier) covering at least 4–5 of the listed Myntra-relevant source types
2. Populated **raw + structured analytics database**
3. **RAG chatbot** capable of answering all questions in Section 2 with cited, quantified answers
4. **Analytics dashboard** with all views listed in Section 4, Surface B
5. A ranked, quantified list of candidate **"opportunity areas"** (the discovered Myntra wishlist-to-purchase user problems) with supporting evidence

---

## 7. Out of scope

- Designing or committing to an actual **product solution**
- **Personalization / recommendation** model training
- **Production-grade scaling** of scrapers (a working prototype / sample corpus across sources is sufficient to validate the approach and produce directionally reliable, evidence-backed opportunity areas)
