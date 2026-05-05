"""
IMSR-DB  |  Recommendation Engine
----------------------------------
Techniques:
  1. Content-Based Filtering   — TF-IDF + Cosine Similarity
  2. Mood-Based Mapping        — Structured question flow -> genre weights
  3. Collaborative Signals     — Watch history + ratings aggregation
  4. Hybrid Scoring            — Weighted blend of above signals

Data source: MySQL stream_db.movie table (loaded at startup)
"""

import pymysql
import pymysql.cursors
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ──────────────────────────────────────────────────────────────────────────────
# DB CONNECTION
# ──────────────────────────────────────────────────────────────────────────────

def _get_connection():
    return pymysql.connect(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="Karachi2006@",
        database="stream_db",
        cursorclass=pymysql.cursors.DictCursor
    )

# ──────────────────────────────────────────────────────────────────────────────
# LOAD MOVIES FROM MYSQL
# Uses only 5 total queries instead of 1 per movie (much faster at startup)
# ──────────────────────────────────────────────────────────────────────────────

def _load_movies_from_db():
    """
    Fetches all movies + related data from MySQL in 5 bulk queries.
    Returns a list of dicts ready for the recommendation engine.
    """
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:

            # 1 — Base movie data
            cursor.execute("""
                SELECT Movie_id AS id, title, description,
                       CAST(rating AS CHAR) AS rating,
                       year, runtime,
                       Poster  AS poster,
                       Trailer AS trailer
                FROM movie
                ORDER BY rating DESC
            """)
            rows = cursor.fetchall()

            # 2 — All genres
            cursor.execute("SELECT Movie_id, genre FROM movie_genre")
            genre_rows = cursor.fetchall()

            # 3 — All directors
            cursor.execute("SELECT Movie_id, director_name FROM movie_director")
            director_rows = cursor.fetchall()

            # 4 — All cast
            cursor.execute("SELECT Movie_id, actor_name FROM movie_cast")
            cast_rows = cursor.fetchall()

            # 5 — All keywords
            cursor.execute("SELECT Movie_id, keyword FROM movie_keywords")
            keyword_rows = cursor.fetchall()

    finally:
        conn.close()

    # Build lookup dicts keyed by Movie_id
    genres_map    = {}
    directors_map = {}
    cast_map      = {}
    keywords_map  = {}

    for r in genre_rows:
        genres_map.setdefault(r["Movie_id"], []).append(r["genre"])

    for r in director_rows:
        directors_map.setdefault(r["Movie_id"], []).append(r["director_name"])

    for r in cast_rows:
        cast_map.setdefault(r["Movie_id"], []).append(r["actor_name"])

    for r in keyword_rows:
        keywords_map.setdefault(r["Movie_id"], []).append(r["keyword"])

    # Assemble final movie dicts
    movies = []
    for row in rows:
        mid = row["id"]

        # Convert rating to float safely
        try:
            row["rating"] = float(row["rating"]) if row["rating"] else 0.0
        except:
            row["rating"] = 0.0

        # Poster / trailer fallbacks
        row["poster"]  = row["poster"]  or "/static/images/placeholder.svg"
        row["trailer"] = row["trailer"] or ""

        # Attach related data from lookup dicts
        row["genre"]    = ",".join(genres_map.get(mid, []))
        row["director"] = ", ".join(directors_map.get(mid, []))
        row["cast"]     = "|".join(cast_map.get(mid, []))
        row["keywords"] = " ".join(keywords_map.get(mid, []))

        movies.append(row)

    return movies


# Load once at startup
print("Loading movies from MySQL...", flush=True)
SEED_MOVIES = _load_movies_from_db()
print(f"{len(SEED_MOVIES)} movies loaded into recommendation engine.", flush=True)


# ──────────────────────────────────────────────────────────────────────────────
# MOOD SYSTEM
# ──────────────────────────────────────────────────────────────────────────────

MOOD_QUESTIONS = [
    {
        "id": "current_feeling",
        "text": "How are you feeling right now?",
        "sub": "Pick the one that resonates most",
        "options": [
            {"value": "happy",   "label": "Happy",      "icon": "★", "desc": "Light-hearted and good"},
            {"value": "sad",     "label": "Melancholic", "icon": "~", "desc": "Need something emotional"},
            {"value": "excited", "label": "Excited",     "icon": "!", "desc": "Ready for action"},
            {"value": "anxious", "label": "Tense",       "icon": "#", "desc": "Edge of my seat feeling"},
            {"value": "calm",    "label": "Peaceful",    "icon": "o", "desc": "Relaxed and reflective"},
            {"value": "bored",   "label": "Bored",       "icon": "-", "desc": "Need something fresh"},
        ]
    },
    {
        "id": "company",
        "text": "Who are you watching with?",
        "sub": "This shapes the vibe we pick",
        "options": [
            {"value": "alone",   "label": "Just Me",      "icon": "1", "desc": "Solo watch session"},
            {"value": "partner", "label": "Date Night",   "icon": "2", "desc": "Someone special"},
            {"value": "friends", "label": "Friend Group", "icon": "3", "desc": "Social viewing"},
            {"value": "family",  "label": "Family",       "icon": "4", "desc": "All ages together"},
        ]
    },
    {
        "id": "length_pref",
        "text": "How much time do you have?",
        "sub": "We will match runtime accordingly",
        "options": [
            {"value": "short",  "label": "Under 90 min", "icon": "<", "desc": "Quick watch"},
            {"value": "medium", "label": "90-120 min",   "icon": "=", "desc": "Standard film"},
            {"value": "long",   "label": "Epic 2h+",     "icon": ">", "desc": "Full experience"},
            {"value": "series", "label": "TV Series",    "icon": "S", "desc": "Multi-episode binge"},
        ]
    },
    {
        "id": "intensity",
        "text": "What intensity level suits you?",
        "sub": "Emotional weight of the story",
        "options": [
            {"value": "light",   "label": "Light & Fun", "icon": "L", "desc": "Easy, feel-good"},
            {"value": "medium",  "label": "Balanced",    "icon": "M", "desc": "Engaging but not heavy"},
            {"value": "heavy",   "label": "Deep & Dark", "icon": "H", "desc": "Complex, thought-provoking"},
            {"value": "intense", "label": "Intense",     "icon": "I", "desc": "Heart-pounding, gripping"},
        ]
    },
]

MOOD_GENRE_MAP = {
    "happy":   {"Comedy": .9, "Animation": .8, "Romance": .7, "Musical": .7, "Family": .6, "Adventure": .5},
    "sad":     {"Drama": .9, "Romance": .8, "Biography": .7, "History": .6, "Music": .5, "War": .4},
    "excited": {"Action": .9, "Adventure": .85, "Sci-Fi": .75, "Thriller": .7, "Crime": .6, "Sport": .5},
    "anxious": {"Thriller": .9, "Mystery": .85, "Crime": .8, "Horror": .7, "Psychological": .65},
    "calm":    {"Documentary": .9, "Drama": .8, "Biography": .75, "History": .7, "Nature": .6, "Music": .55},
    "bored":   {"Action": .8, "Comedy": .8, "Sci-Fi": .75, "Animation": .7, "Fantasy": .65, "Mystery": .6},
    "alone":   {"Horror": .8, "Thriller": .75, "Sci-Fi": .7, "Drama": .6, "Documentary": .55},
    "partner": {"Romance": .9, "Comedy": .7, "Drama": .65, "Thriller": .55},
    "friends": {"Comedy": .9, "Action": .8, "Horror": .7, "Animation": .6},
    "family":  {"Animation": .9, "Family": .9, "Adventure": .8, "Comedy": .7, "Fantasy": .65},
    "light":   {"Comedy": .8, "Animation": .8, "Family": .7, "Romance": .6},
    "medium":  {"Drama": .7, "Thriller": .65, "Action": .65, "Mystery": .6},
    "heavy":   {"Drama": .9, "Biography": .85, "War": .8, "History": .7, "Crime": .7},
    "intense": {"Thriller": .9, "Horror": .85, "Action": .8, "Crime": .75, "Sci-Fi": .6},
}

def compute_mood_genre_scores(answers: dict) -> dict:
    scores  = {}
    weights = {"current_feeling": 1.0, "company": 0.7, "intensity": 0.8, "length_pref": 0.0}
    for qid, val in answers.items():
        w         = weights.get(qid, 0.5)
        genre_map = MOOD_GENRE_MAP.get(val, {})
        for genre, score in genre_map.items():
            scores[genre] = scores.get(genre, 0) + score * w
    if scores:
        max_s  = max(scores.values())
        scores = {g: round(s / max_s, 3) for g, s in scores.items()}
    return scores


# ──────────────────────────────────────────────────────────────────────────────
# TF-IDF COSINE ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def _build_matrix(movies):
    df = pd.DataFrame(movies)
    df["features"] = (
        df["genre"].str.replace("|", " ", regex=False).str.replace(",", " ", regex=False) + " " +
        df.get("keywords", pd.Series([""] * len(df))).fillna("") + " " +
        df.get("director", pd.Series([""] * len(df))).fillna("")
    )
    tfidf  = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    matrix = tfidf.fit_transform(df["features"])
    sim    = cosine_similarity(matrix)
    return df, sim

_df, _sim = _build_matrix(SEED_MOVIES)


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC DATA FUNCTIONS  (called by app.py)
# ──────────────────────────────────────────────────────────────────────────────

def get_all_movies():
    return SEED_MOVIES

def get_movie_by_id(mid):
    return next((m for m in SEED_MOVIES if m["id"] == mid), None)

def get_trending(n=12):
    return sorted(SEED_MOVIES, key=lambda x: x["rating"], reverse=True)[:n]

def get_top_rated(n=10):
    return sorted(SEED_MOVIES, key=lambda x: x["rating"], reverse=True)[:n]

def get_genres():
    g = set()
    for m in SEED_MOVIES:
        for x in m["genre"].replace("|", ",").split(","):
            if x.strip():
                g.add(x.strip())
    return sorted(g)

def search_movies(query="", genre="", min_rating=0.0, sort="rating"):
    r = SEED_MOVIES[:]
    if query:
        q = query.lower()
        r = [m for m in r if
             q in m["title"].lower() or
             q in m.get("keywords", "").lower() or
             q in m.get("director", "").lower() or
             q in m.get("cast", "").lower() or
             q in m.get("description", "").lower()]
    if genre:
        r = [m for m in r if genre.lower() in m["genre"].lower()]
    if min_rating:
        r = [m for m in r if m["rating"] >= float(min_rating)]
    if sort == "title":
        r = sorted(r, key=lambda x: x["title"])
    elif sort == "year":
        r = sorted(r, key=lambda x: x.get("year", 0), reverse=True)
    else:
        r = sorted(r, key=lambda x: x["rating"], reverse=True)
    return r


# ──────────────────────────────────────────────────────────────────────────────
# RECOMMENDATION FUNCTIONS
# ──────────────────────────────────────────────────────────────────────────────

def content_recommend(movie_id, n=8):
    idx = next((i for i, m in enumerate(SEED_MOVIES) if m["id"] == movie_id), None)
    if idx is None:
        return SEED_MOVIES[:n]
    scores = sorted(enumerate(_sim[idx]), key=lambda x: x[1], reverse=True)
    scores = [s for s in scores if s[0] != idx][:n]
    return [SEED_MOVIES[i[0]] for i in scores]

def personalized_recommend(fav_ids, watched_ids=None, n=10):
    if not fav_ids and not watched_ids:
        return get_trending(n)
    seeds  = list(set((fav_ids or []) + (watched_ids or [])))
    scores = np.zeros(len(SEED_MOVIES))
    for sid in seeds:
        idx = next((i for i, m in enumerate(SEED_MOVIES) if m["id"] == sid), None)
        if idx is not None:
            scores += _sim[idx]
    exclude = set(seeds)
    ranked  = [(i, s) for i, s in enumerate(scores) if SEED_MOVIES[i]["id"] not in exclude]
    ranked  = sorted(ranked, key=lambda x: x[1], reverse=True)[:n]
    return [SEED_MOVIES[idx] for idx, _ in ranked] or get_trending(n)

def mood_recommend(answers: dict, n=12):
    genre_scores = compute_mood_genre_scores(answers)
    if not genre_scores:
        return get_trending(n)
    length_pref    = answers.get("length_pref", "")
    runtime_filter = None
    if length_pref == "short":    runtime_filter = lambda r: r < 95
    elif length_pref == "medium": runtime_filter = lambda r: 90 <= r <= 125
    elif length_pref == "long":   runtime_filter = lambda r: r > 120
    results = []
    for m in SEED_MOVIES:
        if runtime_filter and not runtime_filter(m.get("runtime", 120)):
            continue
        m_genres = [g.strip() for g in m["genre"].replace("|", ",").split(",")]
        score    = sum(genre_scores.get(g, 0) for g in m_genres)
        score   += m["rating"] * 0.05
        results.append((score, m))
    results.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in results[:n]]