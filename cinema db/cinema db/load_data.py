"""
tables defined:
  - movie            (Movie_id, title, description, rating, year, runtime, Poster, Trailer)
  - movie_genre      (Movie_id, genre)
  - movie_director   (Movie_id, director_name)
  - movie_cast       (Movie_id, actor_name)
  - movie_keywords   (Movie_id, keyword)
"""

import pandas as pd
import requests
import pymysql
import time
import sys

TMDB_API_KEY = "9f48e5562b3c47a27bde25a7dca7a838" # API Key 
CSV_FILE   = "IMDB-Movie-Dataa.csv" # CSV File of the dataset from Kaggle
NUM_MOVIES = 200

# Connecting to MySQL using pymysql

print("\n Connecting to MySQL...", flush=True)
try:
    db = pymysql.connect(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="Karachi2006@",
        database="stream_db"
    )
    cursor = db.cursor()
    print("Connected to stream_db!\n", flush=True)
except pymysql.Error as e:
    print(f"MySQL connection failed: {e}", flush=True)
    sys.exit(1)

# Loading & clean CSV with Pandas

print("Loading CSV...", flush=True)
try:
    df = pd.read_csv(CSV_FILE)
except FileNotFoundError:
    print(f"'{CSV_FILE}' not found. Put it in the same folder as this script.", flush=True)
    sys.exit(1)

print(f"   Found {len(df)} rows | Columns: {list(df.columns)}\n", flush=True)

# Clean
df = df.dropna(subset=["Title", "Genre", "Description", "Rating"])
df["Rating"]            = pd.to_numeric(df["Rating"], errors="coerce").fillna(7.0)
df["Runtime (Minutes)"] = pd.to_numeric(df["Runtime (Minutes)"], errors="coerce").fillna(120).astype(int)
df["Year"]              = pd.to_numeric(df["Year"], errors="coerce").fillna(2000).astype(int)

# Top NUM_MOVIES by rating
df = df.sort_values("Rating", ascending=False).head(NUM_MOVIES).reset_index(drop=True)
print(f"{len(df)} movies selected after cleaning\n", flush=True)

# TMDB API helpers

TMDB_BASE = "https://api.themoviedb.org/3"

def search_tmdb(title, year):
    """Returns (tmdb_id, poster_url) or (None, '')"""
    try:
        # Try with year first
        r = requests.get(f"{TMDB_BASE}/search/movie",
                         params={"api_key": TMDB_API_KEY, "query": title, "year": year},
                         timeout=10)
        results = r.json().get("results", [])

        # If nothing, try without year (some years differ by 1)
        if not results:
            r = requests.get(f"{TMDB_BASE}/search/movie",
                             params={"api_key": TMDB_API_KEY, "query": title},
                             timeout=10)
            results = r.json().get("results", [])

        if results:
            m           = results[0]
            poster_path = m.get("poster_path", "")
            poster_url  = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ""
            return m["id"], poster_url

    except Exception as e:
        print(f"TMDB search error: {e}", flush=True)

    return None, ""


def get_trailer(tmdb_id):
    """Returns YouTube video key or ''"""
    try:
        r  = requests.get(f"{TMDB_BASE}/movie/{tmdb_id}/videos",
                              params={"api_key": TMDB_API_KEY}, timeout=10)
        videos = r.json().get("results", [])

        # Official trailer first
        for v in videos:
            if v.get("type") == "Trailer" and v.get("site") == "YouTube" and v.get("official"):
                return v["key"]
        # Any trailer
        for v in videos:
            if v.get("type") == "Trailer" and v.get("site") == "YouTube":
                return v["key"]
        # Teaser fallback
        for v in videos:
            if v.get("type") == "Teaser" and v.get("site") == "YouTube":
                return v["key"]

    except Exception as e:
        print(f"Trailer fetch error: {e}", flush=True)

    return ""


def get_keywords(tmdb_id):
    """Returns list of keyword strings from TMDB"""
    try:
        r   = requests.get(f"{TMDB_BASE}/movie/{tmdb_id}/keywords",
                           params={"api_key": TMDB_API_KEY}, timeout=10)
        kws = r.json().get("keywords", [])
        return [k["name"] for k in kws[:8]]   # max 8 keywords per movie
    except:
        return []


# Main insert loop

print("Starting insert loop...\n", flush=True)
print(f" ~{NUM_MOVIES * 3} TMDB API calls total", flush=True)
print(f" Estimated time: ~{NUM_MOVIES * 0.8 / 60:.1f} minutes\n", flush=True)
print("-" * 65, flush=True)

success = 0
failed  = 0

for i, row in df.iterrows():
    title    = str(row["Title"]).strip()
    year     = int(row["Year"])
    rating   = float(row["Rating"])
    runtime  = int(row["Runtime (Minutes)"])
    desc     = str(row.get("Description", "")).strip()
    genre    = str(row.get("Genre", "")).strip()
    director = str(row.get("Director", "")).strip()
    actors   = str(row.get("Actors", "")).strip()

    print(f"[{i+1:3}/{NUM_MOVIES}] {title} ({year})", flush=True)

    #  TMDB calls 
    tmdb_id, poster_url = search_tmdb(title, year)

    if tmdb_id:
        trailer_key = get_trailer(tmdb_id)
        keywords    = get_keywords(tmdb_id)
        time.sleep(0.26)
    else:
        trailer_key = ""
        keywords    = []
        print(f"Not found on TMDB", flush=True)

    if not poster_url:
        poster_url = "/static/images/placeholder.svg"

    print(f"         poster={'Yes' if 'tmdb.org' in poster_url else 'Oops'} | "
          f"trailer={'Yes ' + trailer_key if trailer_key else 'Oops'} | "
          f"keywords={len(keywords)}", flush=True)

    # INSERT into movie 
    try:
        cursor.execute("""
            INSERT IGNORE INTO movie
                (title, description, rating, year, runtime, Poster, Trailer)
            VALUES
                (%s,%s, %s,%s,%s,%s,%s)
        """, (title, desc, rating, year, runtime, poster_url, trailer_key))
        db.commit()

        # Get the auto-generated Movie_id
        movie_id = cursor.lastrowid

        # If INSERT IGNORE skipped duplicate, fetch existing id
        if movie_id == 0:
            cursor.execute(
                "SELECT Movie_id FROM movie WHERE title=%s AND year=%s",
                (title, year)
            )
            result   = cursor.fetchone()
            movie_id = result[0] if result else None

        if not movie_id:
            print(f"Could not get Movie_id... skipping related tables", flush=True)
            failed += 1
            continue

        #INSERT into movie_genre 
        for g in genre.split(","):
            g = g.strip()
            if g:
                cursor.execute(
                    "INSERT IGNORE INTO movie_genre (Movie_id, genre) VALUES (%s, %s)",
                    (movie_id, g)
                )

        # INSERT into movie_director 
        for d in director.split(","):
            d = d.strip()
            if d and d.lower() != "nan":
                cursor.execute(
                    "INSERT IGNORE INTO movie_director (Movie_id, director_name) VALUES (%s, %s)",
                    (movie_id, d)
                )

        # INSERT into movie_cast
        for actor in actors.split(","):
            actor = actor.strip()
            if actor and actor.lower() != "nan":
                cursor.execute(
                    "INSERT IGNORE INTO movie_cast (Movie_id, actor_name) VALUES (%s, %s)",
                    (movie_id, actor)
                )

        # INSERT into movie_keywords 
        for kw in keywords:
            kw = kw.strip()
            if kw:
                cursor.execute(
                    "INSERT IGNORE INTO movie_keywords (Movie_id, keyword) VALUES (%s, %s)",
                    (movie_id, kw)
                )

        db.commit()
        success += 1

    except pymysql.Error as e:
        print(f"DB error: {e}", flush=True)
        db.rollback()
        failed += 1

    time.sleep(0.26)



print("\n" + "=" * 65, flush=True)
print(f"COMPLETE!", flush=True)
print(f" Movies inserted successfully : {success}", flush=True)
print(f"Movies failed : {failed}", flush=True)
print("=" * 65, flush=True)

cursor.close()
db.close()