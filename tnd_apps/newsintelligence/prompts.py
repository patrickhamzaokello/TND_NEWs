"""
Centralized prompt definitions for the enrichment pipeline.
Keeping prompts here makes them easy to version, test, and optimize.
"""

# ── Article Enrichment Prompt ─────────────────────────────────────────────────
# Used by: ArticleAnalysisAgent
# Model:   Claude Haiku (fast + cheap for bulk processing)

ARTICLE_ANALYSIS_SYSTEM = """You are a news analysis AI for Uganda and East Africa.
Analyze articles objectively and return only valid JSON — no markdown, no preamble."""

ARTICLE_ANALYSIS_USER = """Analyze this Ugandan news article and return a JSON object:

{{
  "summary": "2-3 sentence neutral summary of the article",
  "sentiment": "positive|negative|neutral|mixed",
  "sentiment_score": <float -1.0 to 1.0>,
  "importance_score": <int 1-10, 10 = major national story>,
  "themes": ["list", "of", "themes"],
  "key_facts": ["fact 1", "fact 2", "fact 3"],
  "related_themes": ["broader story threads this connects to"],
  "entities": {{
    "people": ["Full Name", ...],
    "organizations": ["Org Name", ...],
    "locations": ["Place Name", ...]
  }},
  "audience_relevance": {{
    "business": <float 0.0-1.0>,
    "general_public": <float 0.0-1.0>,
    "government": <float 0.0-1.0>,
    "youth": <float 0.0-1.0>
  }},
  "follow_up_worthy": <true|false>,
  "controversy_flag": <true|false>,
  "is_breaking_candidate": <true|false>
}}

Themes should be chosen from:
governance, education, health, economy, entertainment, sports, crime, environment,
technology, politics, social, business, infrastructure, agriculture, tourism

Article title: {title}
Article content:
{content}"""


# ── Daily Digest Prompt ───────────────────────────────────────────────────────
# Used by: DailyDigestAgent
# Model:   Claude Sonnet (higher quality for the final synthesis)

DAILY_DIGEST_SYSTEM = """You are a senior news analyst for Uganda and East Africa.
You write sharp, insightful intelligence briefs for professionals.
Return only valid JSON — no markdown, no preamble."""

DAILY_DIGEST_USER = """Generate a daily intelligence brief for {digest_date}.

You have {article_count} analyzed articles from today.

Article summaries and metadata:
{articles_json}

Trending entities (7-day):
{trending_entities_json}

Return a JSON object:
{{
  "digest_text": "3-4 paragraph narrative digest written in professional tone. Cover the most important stories, patterns, and what they mean for Uganda.",
  "top_stories": [
    {{
      "article_id": <int>,
      "title": "<string>",
      "why_it_matters": "<1-2 sentences on significance>",
      "importance_score": <int 1-10>
    }}
  ],
  "trending_entities": [
    {{
      "entity": "<name>",
      "type": "person|organization|location",
      "mention_count": <int>,
      "sentiment_trend": "rising_positive|rising_negative|stable|declining"
    }}
  ],
  "sector_sentiment": {{
    "governance": <float -1.0 to 1.0>,
    "entertainment": <float -1.0 to 1.0>,
    "economy": <float -1.0 to 1.0>,
    "education": <float -1.0 to 1.0>,
    "health": <float -1.0 to 1.0>
  }},
  "story_threads": [
    {{
      "thread_name": "<string>",
      "description": "<1 sentence>",
      "article_ids": [<int>, ...]
    }}
  ],
  "under_radar_story": {{
    "article_id": <int>,
    "title": "<string>",
    "reason": "<why this deserves more attention>"
  }},
  "key_concern": "<One data point or development that should concern decision-makers>"
}}"""
