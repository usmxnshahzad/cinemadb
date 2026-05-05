"""
IMSR-DB  |  Flask Application
Intelligent Movie Streaming & Recommendation Database
"""
import os, json, re, requests
import pymysql
import pymysql.cursors
from functools import wraps
from datetime import datetime
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, g)
from werkzeug.security import generate_password_hash, check_password_hash
from recommendation import (
    get_all_movies, get_movie_by_id, search_movies,
    get_trending, get_top_rated, get_genres,
    content_recommend, personalized_recommend, mood_recommend,
    MOOD_QUESTIONS, compute_mood_genre_scores, SEED_MOVIES, _build_matrix
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "imsr-db-secret-2024-xK9m")

# TMDB API key 
TMDB_API_KEY = "9f48e5562b3c47a27bde25a7dca7a838"

# ────────────────────────
# DB
# ────────────────────────
def get_db():
    if 'db' not in g:
        g.db = pymysql.connect(
            host="127.0.0.1",
            port=3306,
            user="root",
            password="Karachi2006@",
            database="stream_db",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False
        )
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# ─────────────────────────────────────────────────────────────────────────────
# TMDB helpers
# ─────────────────────────────────────────────────────────────────────────────
def _tmdb_fetch(title, year=None):
    """Returns (poster_url, trailer_key) from TMDB. Falls back to ('', '')."""
    try:
        params = {"api_key": TMDB_API_KEY, "query": title}
        if year:
            params["year"] = year
        r       = requests.get("https://api.themoviedb.org/3/search/movie", params=params, timeout=8)
        results = r.json().get("results", [])
        if not results and year:
            params.pop("year")
            results = requests.get("https://api.themoviedb.org/3/search/movie", params=params, timeout=8).json().get("results", [])
        if not results:
            return "", ""
        tmdb_id     = results[0]["id"]
        poster_path = results[0].get("poster_path", "")
        poster_url  = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ""
        vids        = requests.get(
            f"https://api.themoviedb.org/3/movie/{tmdb_id}/videos",
            params={"api_key": TMDB_API_KEY}, timeout=8
        ).json().get("results", [])
        trailer_key = ""
        for v in vids:
            if v.get("type") == "Trailer" and v.get("site") == "YouTube":
                trailer_key = v["key"]
                break
        return poster_url, trailer_key
    except Exception:
        return "", ""

# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.url))
        return f(*a, **kw)
    return wrapped

def admin_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if "user_id" not in session or session.get("role") != "admin":
            return redirect(url_for("home"))
        return f(*a, **kw)
    return wrapped

def current_user():
    if "user_id" not in session:
        return None
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("""
            SELECT u.User_id AS id, u.email,
                   ud.username, ud.password, ud.role, ud.avatar, ud.bio
            FROM users u
            JOIN user_details ud ON u.email = ud.email
            WHERE u.User_id = %s
        """, (session["user_id"],))
        return cursor.fetchone()

def fav_ids(uid):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("SELECT Movie_id FROM favourites WHERE User_id=%s", (uid,))
        return [r["Movie_id"] for r in cursor.fetchall()]

def watchlist_ids(uid):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("SELECT Movie_id FROM watchlist WHERE User_id=%s", (uid,))
        return [r["Movie_id"] for r in cursor.fetchall()]

def history_ids(uid):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT Movie_id FROM watch_history WHERE User_id=%s ORDER BY Watched_at DESC",
            (uid,)
        )
        return [r["Movie_id"] for r in cursor.fetchall()]

def fmt_date(dt):
    """Safely format a datetime object or string to YYYY-MM-DD."""
    if dt is None:
        return "—"
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    return str(dt)[:10]

# ─────────────────────────────────────────────────────────────────────────────
# CHATBOT ENGINE
# ─────────────────────────────────────────────────────────────────────────────
CHAT_RESPONSES = {
    r"\b(hi|hello|hey|greetings)\b":
        "Hello! I am CineBot, your personal movie guide. Ask me about films, genres, directors, or get personalised recommendations!",
    r"\b(recommend|suggest|what should i watch)\b":
        "I can help with that! Tell me your mood — are you feeling like Action, Drama, Comedy, Sci-Fi, or Thriller? Or use the Mood Finder in the navbar for a full personalised experience.",
    r"\b(top rated|best movies|highest rated)\b":
        "Our top-rated films right now: The Dark Knight (9.0), Inception (8.8), Interstellar (8.6). Want details on any?",
    r"\b(action)\b":
        "Great choice! For action I recommend: The Dark Knight, Mad Max: Fury Road, The Avengers, and Iron Man. All available in our library!",
    r"\b(drama)\b":
        "For drama: Whiplash, La La Land, The Pursuit of Happyness, and 12 Years a Slave are outstanding.",
    r"\b(sci.fi|science fiction)\b":
        "Sci-Fi picks: Inception, Interstellar, Arrival, District 9, and Ex Machina. Each offers a unique vision of the future.",
    r"\b(comedy|funny|laugh)\b":
        "Need a laugh? Try The Grand Budapest Hotel, Hot Fuzz, Superbad, or The Hangover.",
    r"\b(thriller|suspense)\b":
        "Thriller fans will love: Inception, Gone Girl, Prisoners, Shutter Island, and Nightcrawler.",
    r"\b(horror|scary|frightening)\b":
        "For horror: Hereditary is deeply unsettling, and Get Out blends social commentary with terror.",
    r"\b(romance|romantic|love)\b":
        "Romantic picks: La La Land, About Time, (500) Days of Summer, and Brooklyn.",
    r"\b(nolan|christopher nolan)\b":
        "Christopher Nolan directed Inception, The Dark Knight, Interstellar, The Prestige — all in our library.",
    r"\b(tarantino|quentin)\b":
        "Quentin Tarantino's Django Unchained, Inglourious Basterds, and The Hateful Eight are all in our library.",
    r"\b(oscar|academy award|award)\b":
        "Oscar highlights: Spotlight, 12 Years a Slave, The King's Speech, and Argo each won Best Picture.",
    r"\b(mood|feeling)\b":
        "Use the Mood Finder button in the navbar! It asks you 4 quick questions and maps them to perfect genres.",
    r"\b(how|work|algorithm|ai|recommendation)\b":
        "Our recommendation engine uses TF-IDF vectorisation + cosine similarity on movie metadata.",
    r"\b(favorite|favourite|add to)\b":
        "Click the heart icon on any movie card to add it to your Favourites. You need to be logged in first!",
    r"\b(watchlist)\b":
        "The watchlist lets you save movies for later. Click 'Add to Watchlist' on any movie detail page.",
    r"\b(rating|rate|review)\b":
        "You can rate movies 1-10 stars on the movie detail page. Your ratings improve your personalised recommendations.",
    r"\b(search|find|look for)\b":
        "Use the search bar at the top or go to Browse. You can filter by genre, minimum rating, year, and sort the results.",
    r"\b(admin|manage|panel)\b":
        "The admin panel (/admin) lets admins manage users, view usage statistics, and oversee the movie catalogue.",
    r"\b(thank|thanks|helpful)\b":
        "Happy to help! Enjoy your movie.",
    r"\b(bye|goodbye|ciao)\b":
        "Goodbye! Enjoy your film. Come back anytime for more recommendations.",
}

def chat_reply(message: str) -> str:
    msg = message.strip().lower()
    for pattern, reply in CHAT_RESPONSES.items():
        if re.search(pattern, msg, re.IGNORECASE):
            return reply
    for g in get_genres():
        if g.lower() in msg:
            movies = search_movies(genre=g)[:3]
            names  = ", ".join(m["title"] for m in movies)
            return f"Top {g} films in our library: {names}. Click any title to see details and trailers!"
    return ("I am not sure about that one, but I can help with movie recommendations, genre suggestions, "
            "director info, and how our recommendation system works. What would you like to know?")

# ─────────────────────────────────────────────────────────────────────────────
# PAGES
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    user           = current_user()
    favs, wl, hist = [], [], []
    recs           = []
    if user:
        favs = fav_ids(user["id"])
        wl   = watchlist_ids(user["id"])
        hist = history_ids(user["id"])
        recs = personalized_recommend(favs, hist, 8)
    trending  = get_trending(12)
    top_rated = get_top_rated(8)
    featured  = trending[0]
    return render_template("home.html", user=user, featured=featured,
                           trending=trending, top_rated=top_rated, recs=recs,
                           favs=favs, wl=wl, genres=get_genres(),
                           mood_questions=MOOD_QUESTIONS)

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "")
        db    = get_db()
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT u.User_id AS id, u.email, ud.username, ud.password, ud.role
                FROM users u
                JOIN user_details ud ON u.email = ud.email
                WHERE u.email = %s
            """, (email,))
            u = cursor.fetchone()
        if u and check_password_hash(u["password"], pw):
            session["user_id"]  = u["id"]
            session["username"] = u["username"]
            session["role"]     = u["role"]
            return redirect(request.args.get("next") or url_for("home"))
        error = "Invalid email or password."
    return render_template("login.html", error=error)

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip().lower()
        pw       = request.form.get("password", "")
        pw2      = request.form.get("confirm", "")
        if not all([username, email, pw]):
            error = "All fields are required."
        elif pw != pw2:
            error = "Passwords do not match."
        elif len(pw) < 6:
            error = "Password must be at least 6 characters."
        else:
            db = get_db()
            try:
                with db.cursor() as cursor:
                    cursor.execute("INSERT INTO users (email) VALUES (%s)", (email,))
                    cursor.execute(
                        "INSERT INTO user_details (email, username, password, role) VALUES (%s,%s,%s,%s)",
                        (email, username, generate_password_hash(pw), "user")
                    )
                db.commit()
                with db.cursor() as cursor:
                    cursor.execute("""
                        SELECT u.User_id AS id, ud.username, ud.role
                        FROM users u JOIN user_details ud ON u.email = ud.email
                        WHERE u.email = %s
                    """, (email,))
                    u = cursor.fetchone()
                session["user_id"]  = u["id"]
                session["username"] = u["username"]
                session["role"]     = u["role"]
                return redirect(url_for("home"))
            except pymysql.IntegrityError:
                db.rollback()
                error = "That email is already registered."
    return render_template("signup.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    user        = current_user()
    favs        = fav_ids(user["id"])
    wl          = watchlist_ids(user["id"])
    hist        = history_ids(user["id"])
    fav_movies  = [m for mid in favs     if (m := get_movie_by_id(mid))]
    wl_movies   = [m for mid in wl[:8]   if (m := get_movie_by_id(mid))]
    hist_movies = [m for mid in hist[:8] if (m := get_movie_by_id(mid))]
    recs        = personalized_recommend(favs, hist, 8)
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT Movie_id AS movie_id, score, review, Posted_at AS posted_at "
            "FROM ratings WHERE User_id=%s ORDER BY Posted_at DESC",
            (user["id"],)
        )
        rated_rows = cursor.fetchall()
    rated_movies = []
    for r in rated_rows[:6]:
        m = get_movie_by_id(r["movie_id"])
        if m:
            rated_movies.append({**m, "user_score": r["score"], "user_review": r["review"]})
    return render_template("dashboard.html", user=user,
                           fav_movies=fav_movies, wl_movies=wl_movies,
                           hist_movies=hist_movies, recs=recs,
                           rated_movies=rated_movies,
                           favs=favs, wl=wl, genres=get_genres())

@app.route("/movie/<int:mid>")
def movie_detail(mid):
    movie = get_movie_by_id(mid)
    if not movie:
        return redirect(url_for("home"))
    user       = current_user()
    favs_list  = fav_ids(user["id"]) if user else []
    wl_list    = watchlist_ids(user["id"]) if user else []
    is_fav     = mid in favs_list
    is_wl      = mid in wl_list
    user_score  = None
    user_review = None
    if user:
        db = get_db()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM watch_history WHERE User_id=%s AND Movie_id=%s",
                    (user["id"], mid)
                )
                cursor.execute(
                    "INSERT INTO watch_history (User_id, Movie_id) VALUES (%s,%s)",
                    (user["id"], mid)
                )
            db.commit()
        except:
            pass
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT score, review FROM ratings WHERE User_id=%s AND Movie_id=%s",
                (user["id"], mid)
            )
            row = cursor.fetchone()
        if row:
            user_score  = row["score"]
            user_review = row["review"]
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("""
            SELECT r.score, r.review, r.Posted_at AS posted_at, ud.username
            FROM ratings r
            JOIN users u ON r.User_id = u.User_id
            JOIN user_details ud ON u.email = ud.email
            WHERE r.Movie_id=%s AND r.review IS NOT NULL AND r.review!=''
            ORDER BY r.Posted_at DESC LIMIT 10
        """, (mid,))
        reviews = cursor.fetchall()
    for rv in reviews:
        rv["posted_at"] = fmt_date(rv.get("posted_at"))
    similar   = content_recommend(mid, 6)
    cast_list = [c.strip() for c in movie.get("cast", "").split("|") if c.strip()][:6]
    return render_template("movie.html", movie=movie, similar=similar,
                           user=user, is_fav=is_fav, is_wl=is_wl,
                           user_score=user_score, user_review=user_review,
                           reviews=reviews, cast_list=cast_list,
                           favs=favs_list, wl=wl_list, genres=get_genres())

@app.route("/search")
def search():
    query   = request.args.get("q", "").strip()
    genre   = request.args.get("genre", "").strip()
    minr    = request.args.get("min_rating", 0)
    sort    = request.args.get("sort", "rating")
    results = search_movies(query, genre, minr, sort)
    user    = current_user()
    favs    = fav_ids(user["id"]) if user else []
    wl      = watchlist_ids(user["id"]) if user else []
    return render_template("search.html", results=results, query=query,
                           genre=genre, min_rating=minr, sort_by=sort,
                           user=user, favs=favs, wl=wl,
                           genres=get_genres(), total=len(results))

@app.route("/recommend")
def recommend():
    user       = current_user()
    favs, hist = [], []
    if user:
        favs = fav_ids(user["id"])
        hist = history_ids(user["id"])
        recs = personalized_recommend(favs, hist, 12)
    else:
        recs = get_trending(12)
    wl = watchlist_ids(user["id"]) if user else []
    return render_template("recommend.html", user=user, recs=recs,
                           favs=favs, wl=wl, genres=get_genres())

# ─────────────────────────────────────────────────────────────────────────────
# ADMIN
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin")
@admin_required
def admin():
    db  = get_db()
    tab = request.args.get("tab", "overview")

    with db.cursor() as cursor:
        cursor.execute("""
            SELECT u.User_id AS id, ud.username, u.email, ud.role
            FROM users u JOIN user_details ud ON u.email = ud.email
            ORDER BY u.User_id DESC
        """)
        users = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) as c FROM favourites");    total_favs  = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM ratings");       total_rat   = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM watch_history"); total_hist  = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM mood_session");  total_moods = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM chat_log WHERE role='user'"); total_chats = cursor.fetchone()["c"]

    all_ratings    = []
    chat_logs_data = []
    mood_data      = []

    if tab == "ratings":
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT r.Rating_id AS id, r.Movie_id AS movie_id, r.score, r.review,
                       r.Posted_at AS posted_at, ud.username
                FROM ratings r
                JOIN users u ON r.User_id = u.User_id
                JOIN user_details ud ON u.email = ud.email
                ORDER BY r.Posted_at DESC LIMIT 100
            """)
            rows = cursor.fetchall()
        for row in rows:
            m = get_movie_by_id(row["movie_id"])
            all_ratings.append({
                "id":          row["id"],
                "mid":         row["movie_id"],
                "username":    row["username"],
                "movie_title": m["title"] if m else f"Movie #{row['movie_id']}",
                "score":       row["score"],
                "review":      row["review"] or "",
                "posted_at":   fmt_date(row["posted_at"])
            })

    if tab == "chatlogs":
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT cl.Chat_Log_id AS id, cl.role, cl.message, cl.ts,
                       COALESCE(ud.username, 'Guest') AS username
                FROM chat_log cl
                LEFT JOIN users u ON cl.User_id = u.User_id
                LEFT JOIN user_details ud ON u.email = ud.email
                ORDER BY cl.ts DESC LIMIT 200
            """)
            rows = cursor.fetchall()
        for row in rows:
            row["ts"] = fmt_date(row["ts"])
        chat_logs_data = rows

    if tab == "moods":
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT ms.Mood_Session_id AS id, ms.answers, ms.genre_map,
                       ms.Created_at AS created_at,
                       COALESCE(ud.username, 'Guest') AS username
                FROM mood_session ms
                LEFT JOIN users u ON ms.User_id = u.User_id
                LEFT JOIN user_details ud ON u.email = ud.email
                ORDER BY ms.Created_at DESC LIMIT 50
            """)
            mood_rows = cursor.fetchall()
        for row in mood_rows:
            try:
                answers    = json.loads(row["answers"])
                gmap       = json.loads(row["genre_map"])
                top_genres = ", ".join(k for k, v in sorted(gmap.items(), key=lambda x: -x[1])[:3])
            except:
                answers, top_genres = {}, "—"
            mood_data.append({
                "id":         row["id"],
                "username":   row["username"],
                "answers":    answers,
                "top_genres": top_genres,
                "created_at": fmt_date(row["created_at"])
            })

    return render_template("admin.html",
                           tab=tab, users=users,
                           all_movies=get_all_movies(),
                           all_ratings=all_ratings,
                           chat_logs_data=chat_logs_data,
                           mood_data=mood_data,
                           total_movies=len(get_all_movies()),
                           total_users=len(users),
                           total_favs=total_favs,
                           total_ratings=total_rat,
                           total_hist=total_hist,
                           total_moods=total_moods,
                           total_chats=total_chats,
                           genres=get_genres(),
                           user=current_user())

# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/movies")
def api_movies():
    return jsonify(get_all_movies())

@app.route("/api/movie/<int:mid>")
def api_movie(mid):
    m = get_movie_by_id(mid)
    return jsonify(m) if m else (jsonify({"error": "not found"}), 404)

@app.route("/api/search")
def api_search():
    return jsonify(search_movies(
        request.args.get("q", ""),
        request.args.get("genre", ""),
        float(request.args.get("min_rating", 0)),
        request.args.get("sort", "rating")
    ))

@app.route("/api/recommend/<int:mid>")
def api_content_rec(mid):
    return jsonify(content_recommend(mid))

@app.route("/api/recommend/refresh")
def api_refresh():
    user = current_user()
    if user:
        return jsonify(personalized_recommend(fav_ids(user["id"]), history_ids(user["id"]), 12))
    return jsonify(get_trending(12))

@app.route("/api/mood/questions")
def api_mood_questions():
    return jsonify(MOOD_QUESTIONS)

@app.route("/api/mood/recommend", methods=["POST"])
def api_mood_recommend():
    data      = request.get_json(force=True)
    answers   = data.get("answers", {})
    results   = mood_recommend(answers, 12)
    genre_map = compute_mood_genre_scores(answers)
    db  = get_db()
    uid = session.get("user_id")
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO mood_session (User_id, answers, genre_map) VALUES (%s,%s,%s)",
                (uid, json.dumps(answers), json.dumps(genre_map))
            )
        db.commit()
    except:
        pass
    return jsonify({"movies": results, "genre_scores": genre_map})

@app.route("/api/favorite", methods=["POST"])
def api_favorite():
    if "user_id" not in session:
        return jsonify({"error": "Login required"}), 401
    d   = request.get_json(force=True)
    mid = d.get("movie_id")
    uid = session["user_id"]
    db  = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT Favourite_id FROM favourites WHERE User_id=%s AND Movie_id=%s", (uid, mid)
        )
        exists = cursor.fetchone()
    if exists:
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM favourites WHERE User_id=%s AND Movie_id=%s", (uid, mid))
        db.commit()
        return jsonify({"status": "removed"})
    with db.cursor() as cursor:
        cursor.execute("INSERT IGNORE INTO favourites (User_id,Movie_id) VALUES (%s,%s)", (uid, mid))
    db.commit()
    return jsonify({"status": "added"})

@app.route("/api/watchlist", methods=["POST"])
def api_watchlist():
    if "user_id" not in session:
        return jsonify({"error": "Login required"}), 401
    d   = request.get_json(force=True)
    mid = d.get("movie_id")
    uid = session["user_id"]
    db  = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT WatchList_id FROM watchlist WHERE User_id=%s AND Movie_id=%s", (uid, mid)
        )
        exists = cursor.fetchone()
    if exists:
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM watchlist WHERE User_id=%s AND Movie_id=%s", (uid, mid))
        db.commit()
        return jsonify({"status": "removed"})
    with db.cursor() as cursor:
        cursor.execute("INSERT IGNORE INTO watchlist (User_id,Movie_id) VALUES (%s,%s)", (uid, mid))
    db.commit()
    return jsonify({"status": "added"})

@app.route("/api/rate", methods=["POST"])
def api_rate():
    if "user_id" not in session:
        return jsonify({"error": "Login required"}), 401
    d      = request.get_json(force=True)
    mid    = d.get("movie_id")
    score  = int(d.get("score", 5))
    review = d.get("review", "").strip()
    uid    = session["user_id"]
    db     = get_db()
    with db.cursor() as cursor:
        cursor.execute("""
            INSERT INTO ratings (User_id, Movie_id, score, review)
            VALUES (%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE score=%s, review=%s
        """, (uid, mid, score, review or None, score, review or None))
    db.commit()
    return jsonify({"status": "rated", "score": score})

@app.route("/api/chat", methods=["POST"])
def api_chat():
    d   = request.get_json(force=True)
    msg = d.get("message", "").strip()
    if not msg:
        return jsonify({"reply": "Please type a message."})
    reply = chat_reply(msg)
    db    = get_db()
    uid   = session.get("user_id")
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO chat_log (User_id,role,message) VALUES (%s,%s,%s)", (uid, "user", msg)
            )
            cursor.execute(
                "INSERT INTO chat_log (User_id,role,message) VALUES (%s,%s,%s)", (uid, "bot", reply)
            )
        db.commit()
    except:
        pass
    return jsonify({"reply": reply})

# ─────────────────────────────────────────────────────────────────────────────
# ADMIN API
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/admin/user/<int:uid>", methods=["DELETE"])
@admin_required
def api_delete_user(uid):
    if uid == session["user_id"]:
        return jsonify({"error": "Cannot delete yourself"}), 400
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("SELECT email FROM users WHERE User_id=%s", (uid,))
        row = cursor.fetchone()
        if row:
            email = row["email"]
            for tbl in ["favourites", "watchlist", "watch_history", "ratings", "chat_log", "mood_session"]:
                cursor.execute(f"DELETE FROM {tbl} WHERE User_id=%s", (uid,))
            cursor.execute("DELETE FROM user_details WHERE email=%s", (email,))
            cursor.execute("DELETE FROM users WHERE User_id=%s", (uid,))
    db.commit()
    return jsonify({"status": "deleted"})

@app.route("/api/admin/user/<int:uid>/role", methods=["POST"])
@admin_required
def api_toggle_role(uid):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("""
            SELECT ud.role, u.email FROM users u
            JOIN user_details ud ON u.email = ud.email
            WHERE u.User_id = %s
        """, (uid,))
        u = cursor.fetchone()
    if not u:
        return jsonify({"error": "Not found"}), 404
    new_role = "admin" if u["role"] == "user" else "user"
    with db.cursor() as cursor:
        cursor.execute("UPDATE user_details SET role=%s WHERE email=%s", (new_role, u["email"]))
    db.commit()
    return jsonify({"status": "ok", "new_role": new_role})

@app.route("/api/admin/rating/<int:rid>", methods=["DELETE"])
@admin_required
def api_delete_rating(rid):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM ratings WHERE Rating_id=%s", (rid,))
    db.commit()
    return jsonify({"status": "deleted"})

@app.route("/api/admin/chatlogs/clear", methods=["POST"])
@admin_required
def api_clear_chatlogs():
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM chat_log")
    db.commit()
    return jsonify({"status": "cleared"})

@app.route("/api/admin/movie/add", methods=["POST"])
@admin_required
def api_add_movie():
    """
    Admin adds a movie manually.
    - Checks for duplicate title+year before inserting.
    - Auto-fetches poster + trailer from TMDB.
    - Saves to MySQL + rebuilds TF-IDF.
    """
    import recommendation as rec
    data     = request.get_json(force=True)
    title    = (data.get("title") or "").strip()
    genre    = (data.get("genre") or "").strip()
    desc     = (data.get("description") or "").strip()
    director = (data.get("director") or "").strip()
    cast     = (data.get("cast") or "").strip()
    year     = int(data.get("year") or 2024)
    rating   = float(data.get("rating") or 7.0)
    runtime  = int(data.get("runtime") or 120)

    if not title or not genre or not desc:
        return jsonify({"error": "title, genre and description are required"}), 400

    #Duplicate check 
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT Movie_id FROM movie WHERE title=%s AND year=%s", (title, year)
        )
        existing = cursor.fetchone()
    if existing:
        return jsonify({"error": f'"{title} ({year})" already exists in the library.'}), 409

    # Auto-fetch poster + trailer from TMDB 
    poster_url, trailer_key = _tmdb_fetch(title, year)
    if not poster_url:
        poster_url  = data.get("poster") or "/static/images/placeholder.svg"
    if not trailer_key:
        trailer_key = data.get("trailer") or ""

    # Save to MySQL
    try:
        with db.cursor() as cursor:
            cursor.execute("""
                INSERT INTO movie (title, description, rating, year, runtime, Poster, Trailer)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (title, desc, rating, year, runtime, poster_url, trailer_key))
            new_id = cursor.lastrowid

            for g in genre.replace("|", ",").split(","):
                g = g.strip()
                if g:
                    cursor.execute(
                        "INSERT IGNORE INTO movie_genre (Movie_id, genre) VALUES (%s,%s)", (new_id, g)
                    )
            for d in director.split(","):
                d = d.strip()
                if d:
                    cursor.execute(
                        "INSERT IGNORE INTO movie_director (Movie_id, director_name) VALUES (%s,%s)", (new_id, d)
                    )
            for actor in cast.split(","):
                actor = actor.strip()
                if actor:
                    cursor.execute(
                        "INSERT IGNORE INTO movie_cast (Movie_id, actor_name) VALUES (%s,%s)", (new_id, actor)
                    )
        db.commit()
    except pymysql.Error as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

    #Add to in-memory list + rebuild TF-IDF
    movie = {
        "id":          new_id,
        "title":       title,
        "genre":       genre,
        "description": desc,
        "rating":      rating,
        "year":        year,
        "runtime":     runtime,
        "director":    director,
        "cast":        cast.replace(",", "|"),
        "poster":      poster_url,
        "trailer":     trailer_key,
        "keywords":    "",
    }
    SEED_MOVIES.append(movie)
    rec._df, rec._sim = _build_matrix(SEED_MOVIES)

    return jsonify({"status": "added", "id": new_id,
                    "poster": poster_url, "trailer": trailer_key})

@app.route("/api/admin/movie/<int:mid>", methods=["PUT"])
@admin_required
def api_edit_movie(mid):
    """
    Admin edits an existing movie's metadata.
    Optionally re-fetches poster + trailer from TMDB if title/year changed.
    """
    import recommendation as rec
    db = get_db()

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM movie WHERE Movie_id=%s", (mid,))
        existing = cursor.fetchone()
    if not existing:
        return jsonify({"error": "Movie not found"}), 404

    data     = request.get_json(force=True)
    title    = (data.get("title") or "").strip()
    genre    = (data.get("genre") or "").strip()
    desc     = (data.get("description") or "").strip()
    director = (data.get("director") or "").strip()
    cast     = (data.get("cast") or "").strip()
    year     = int(data.get("year") or existing.get("year") or 2024)
    rating   = float(data.get("rating") or existing.get("rating") or 7.0)
    runtime  = int(data.get("runtime") or existing.get("runtime") or 120)

    if not title or not genre or not desc:
        return jsonify({"error": "title, genre and description are required"}), 400

    # Re-fetch poster/trailer if title or year changed
    old_title = existing.get("title", "")
    old_year  = existing.get("year")
    poster_url  = data.get("poster", "").strip()
    trailer_key = data.get("trailer", "").strip()

    if not poster_url or not trailer_key:
        if title != old_title or year != old_year:
            fetched_poster, fetched_trailer = _tmdb_fetch(title, year)
        else:
            fetched_poster, fetched_trailer = existing.get("Poster", ""), existing.get("Trailer", "")
        if not poster_url:
            poster_url  = fetched_poster or existing.get("Poster") or "/static/images/placeholder.svg"
        if not trailer_key:
            trailer_key = fetched_trailer or existing.get("Trailer") or ""

    try:
        with db.cursor() as cursor:
            cursor.execute("""
                UPDATE movie SET title=%s, description=%s, rating=%s,
                                 year=%s, runtime=%s, Poster=%s, Trailer=%s
                WHERE Movie_id=%s
            """, (title, desc, rating, year, runtime, poster_url, trailer_key, mid))

            # Rebuild genre
            cursor.execute("DELETE FROM movie_genre WHERE Movie_id=%s", (mid,))
            for g in genre.replace("|", ",").split(","):
                g = g.strip()
                if g:
                    cursor.execute(
                        "INSERT IGNORE INTO movie_genre (Movie_id, genre) VALUES (%s,%s)", (mid, g)
                    )

            # Rebuild director
            cursor.execute("DELETE FROM movie_director WHERE Movie_id=%s", (mid,))
            for d in director.split(","):
                d = d.strip()
                if d:
                    cursor.execute(
                        "INSERT IGNORE INTO movie_director (Movie_id, director_name) VALUES (%s,%s)", (mid, d)
                    )

            # Rebuild cast
            cursor.execute("DELETE FROM movie_cast WHERE Movie_id=%s", (mid,))
            for actor in cast.split(","):
                actor = actor.strip()
                if actor:
                    cursor.execute(
                        "INSERT IGNORE INTO movie_cast (Movie_id, actor_name) VALUES (%s,%s)", (mid, actor)
                    )
        db.commit()
    except pymysql.Error as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

    # Update in-memory list + rebuild TF-IDF
    for m in rec.SEED_MOVIES:
        if m["id"] == mid:
            m.update({
                "title":       title,
                "genre":       genre,
                "description": desc,
                "rating":      rating,
                "year":        year,
                "runtime":     runtime,
                "director":    director,
                "cast":        cast.replace(",", "|"),
                "poster":      poster_url,
                "trailer":     trailer_key,
            })
            break
    if rec.SEED_MOVIES:
        rec._df, rec._sim = rec._build_matrix(rec.SEED_MOVIES)

    return jsonify({"status": "updated", "poster": poster_url, "trailer": trailer_key})
@app.route("/api/admin/movie/<int:mid>", methods=["DELETE"])
@admin_required
def api_delete_movie(mid):
    """
    Permanently deletes a movie from MySQL and from the in-memory list.
    Also removes all related data: genres, cast, directors, keywords,
    favourites, watchlist, watch_history, ratings.
    """
    import recommendation as rec
    db = get_db()
    try:
        with db.cursor() as cursor:
            # Check movie exists
            cursor.execute("SELECT Movie_id FROM movie WHERE Movie_id=%s", (mid,))
            if not cursor.fetchone():
                return jsonify({"error": "Movie not found"}), 404

            # Delete from all related tables first
            for tbl in ["movie_genre", "movie_director", "movie_cast", "movie_keywords",
                        "favourites", "watchlist", "watch_history", "ratings"]:
                cursor.execute(f"DELETE FROM {tbl} WHERE Movie_id=%s", (mid,))

            # Delete the movie itself
            cursor.execute("DELETE FROM movie WHERE Movie_id=%s", (mid,))
        db.commit()
    except pymysql.Error as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

    # Remove from in-memory list + rebuild TF-IDF
    rec.SEED_MOVIES[:] = [m for m in rec.SEED_MOVIES if m["id"] != mid]
    if rec.SEED_MOVIES:
        rec._df, rec._sim = rec._build_matrix(rec.SEED_MOVIES)

    return jsonify({"status": "deleted"})


@app.route("/api/admin/stats")
@admin_required
def api_admin_stats():
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) as c FROM users");         u  = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM favourites");    f  = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM ratings");       r  = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM watch_history"); h  = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM mood_session");  mo = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM chat_log WHERE role='user'"); ch = cursor.fetchone()["c"]
    return jsonify({
        "users": u, "favs": f, "ratings": r,
        "history": h, "moods": mo, "chats": ch,
        "movies": len(get_all_movies()),
    })


if __name__ == "__main__":
    with app.app_context():
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute("SELECT User_id FROM users LIMIT 1")
            exists = cursor.fetchone()
        if not exists:
            with db.cursor() as cursor:
                cursor.execute("INSERT INTO users (email) VALUES (%s)", ("admin@imsr.db",))
                cursor.execute(
                    "INSERT INTO user_details (email, username, password, role) VALUES (%s,%s,%s,%s)",
                    ("admin@imsr.db", "Admin", generate_password_hash("admin123"), "admin")
                )
            db.commit()
            print("Demo admin created: admin@imsr.db / admin123")
    app.run(debug=True, port=5000)