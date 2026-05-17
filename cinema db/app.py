"""
IMSR-DB  |  Flask Application
Intelligent Movie Streaming & Recommendation Database
"""
import os, json, sqlite3, re
from functools import wraps
from datetime import datetime
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, g)
from werkzeug.security import generate_password_hash, check_password_hash
from recommendation import (
    get_all_movies, get_movie_by_id, search_movies,
    get_trending, get_top_rated, get_genres,
    content_recommend, personalized_recommend, mood_recommend,
    MOOD_QUESTIONS, compute_mood_genre_scores
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "imsr-db-secret-2024-xK9m")
DATABASE = os.path.join(os.path.dirname(__file__), "database.db")

# ─────────────────────────────────────────────────────────────────────────────
# DB
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
    return db

@app.teardown_appcontext
def close_db(e):
    db = getattr(g, "_db", None)
    if db: db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username  TEXT NOT NULL,
            email     TEXT NOT NULL UNIQUE,
            password  TEXT NOT NULL,
            role      TEXT NOT NULL DEFAULT 'user',
            avatar    TEXT,
            bio       TEXT,
            created   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS favorites (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            movie_id  INTEGER NOT NULL,
            added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, movie_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            movie_id  INTEGER NOT NULL,
            added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, movie_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS watch_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            movie_id   INTEGER NOT NULL,
            watched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, movie_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS ratings (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            movie_id  INTEGER NOT NULL,
            score     INTEGER NOT NULL CHECK(score BETWEEN 1 AND 10),
            review    TEXT,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, movie_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS mood_sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            answers    TEXT NOT NULL,
            genre_map  TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS chat_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            role       TEXT NOT NULL,
            message    TEXT NOT NULL,
            ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        """)
        db.commit()

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
    if "user_id" not in session: return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

def fav_ids(uid):
    rows = get_db().execute("SELECT movie_id FROM favorites WHERE user_id=?", (uid,)).fetchall()
    return [r["movie_id"] for r in rows]

def watchlist_ids(uid):
    rows = get_db().execute("SELECT movie_id FROM watchlist WHERE user_id=?", (uid,)).fetchall()
    return [r["movie_id"] for r in rows]

def history_ids(uid):
    rows = get_db().execute(
        "SELECT movie_id FROM watch_history WHERE user_id=? ORDER BY watched_at DESC", (uid,)
    ).fetchall()
    return [r["movie_id"] for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# CHATBOT ENGINE  (rule-based + pattern matching, no external AI dependency)
# ─────────────────────────────────────────────────────────────────────────────
CHAT_RESPONSES = {
    r"\b(hi|hello|hey|greetings)\b":
        "Hello! I am CineBot, your personal movie guide. Ask me about films, genres, directors, or get personalised recommendations!",
    r"\b(recommend|suggest|what should i watch)\b":
        "I can help with that! Tell me your mood — are you feeling like Action, Drama, Comedy, Sci-Fi, or Thriller? Or use the Mood Finder in the navbar for a full personalised experience.",
    r"\b(top rated|best movies|highest rated)\b":
        "Our top-rated films right now: The Shawshank Redemption (9.3), The Dark Knight (9.0), Pulp Fiction (8.9), and Oppenheimer (8.9). Want details on any?",
    r"\b(action)\b":
        "Great choice! For action I recommend: The Dark Knight, Mad Max: Fury Road, Avengers: Endgame, and The Matrix. All available in our library!",
    r"\b(drama)\b":
        "For drama: The Shawshank Redemption, Forrest Gump, Whiplash, Oppenheimer, and Parasite are outstanding. Intense, emotional, memorable.",
    r"\b(sci.fi|science fiction)\b":
        "Sci-Fi picks: Inception, Interstellar, The Matrix, Arrival, Dune, and Blade Runner 2049. Each offers a unique vision of the future.",
    r"\b(comedy|funny|laugh)\b":
        "Need a laugh? Try Knives Out, Parasite (dark comedy), The Grand Budapest Hotel, or Forrest Gump — all smart, witty, and highly rated.",
    r"\b(thriller|suspense)\b":
        "Thriller fans will love: Inception, Pulp Fiction, The Silence of the Lambs, Get Out, and Joker. Edge-of-your-seat material.",
    r"\b(horror|scary|frightening)\b":
        "For horror: Get Out blends social commentary with terror, Hereditary is deeply unsettling, and The Silence of the Lambs remains a classic.",
    r"\b(romance|romantic|love)\b":
        "Romantic picks: La La Land for a bittersweet love story, Forrest Gump for timeless devotion, and Arrival for something deeper.",
    r"\b(nolan|christopher nolan)\b":
        "Christopher Nolan directed Inception, The Dark Knight, Interstellar, and Oppenheimer — all in our library. His style: complex narratives, practical effects, non-linear time.",
    r"\b(tarantino|quentin)\b":
        "Quentin Tarantino's Pulp Fiction is in our library. His signature: sharp dialogue, nonlinear storytelling, and stylised violence.",
    r"\b(oscar|academy award|award)\b":
        "Oscar highlights in our catalogue: Parasite (Best Picture 2020), Silence of the Lambs, and Forrest Gump each won multiple Academy Awards.",
    r"\b(mood|feeling)\b":
        "Use the Mood Finder button in the navbar! It asks you 4 quick questions about your feeling, company, time, and intensity — then maps those to perfect genres using our AI engine.",
    r"\b(how|work|algorithm|ai|recommendation)\b":
        "Our recommendation engine uses TF-IDF vectorisation + cosine similarity on movie metadata (genre, keywords, director). For mood-based picks, it maps your answers to genre weights and scores every film accordingly.",
    r"\b(favorite|favourite|add to)\b":
        "Click the heart icon on any movie card or on the movie detail page to add it to your Favourites. You need to be logged in first!",
    r"\b(watchlist)\b":
        "The watchlist lets you save movies for later. Click 'Add to Watchlist' on any movie detail page — find your list in the Dashboard.",
    r"\b(rating|rate|review)\b":
        "You can rate movies 1-10 stars on the movie detail page. Your ratings improve your personalised recommendations over time.",
    r"\b(search|find|look for)\b":
        "Use the search bar at the top or go to Browse. You can filter by genre, minimum rating, year, and sort the results.",
    r"\b(admin|manage|panel)\b":
        "The admin panel (/admin) lets admins manage users, view usage statistics, and oversee the movie catalogue.",
    r"\b(thank|thanks|helpful)\b":
        "Happy to help! Enjoy your movie. If you need more recommendations, I am always here.",
    r"\b(bye|goodbye|ciao)\b":
        "Goodbye! Enjoy your film. Come back anytime for more recommendations.",
}

def chat_reply(message: str) -> str:
    msg = message.strip().lower()
    for pattern, reply in CHAT_RESPONSES.items():
        if re.search(pattern, msg, re.IGNORECASE):
            return reply
    # Genre mention -> suggest a specific movie
    genres = get_genres()
    for g in genres:
        if g.lower() in msg:
            movies = search_movies(genre=g)[:3]
            names  = ", ".join(m["title"] for m in movies)
            return f"Top {g} films in our library: {names}. Click any title to see details and trailers!"
    # Fallback
    return ("I am not sure about that one, but I can help with movie recommendations, genre suggestions, "
            "director info, and how our recommendation system works. What would you like to know?")

# ─────────────────────────────────────────────────────────────────────────────
# PAGES
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    user     = current_user()
    favs, wl, hist = [], [], []
    recs = []
    if user:
        favs  = fav_ids(user["id"])
        wl    = watchlist_ids(user["id"])
        hist  = history_ids(user["id"])
        recs  = personalized_recommend(favs, hist, 8)
    trending  = get_trending(12)
    top_rated = get_top_rated(8)
    featured  = trending[0]
    genres    = get_genres()
    return render_template("home.html", user=user, featured=featured,
                           trending=trending, top_rated=top_rated, recs=recs,
                           favs=favs, wl=wl, genres=genres,
                           mood_questions=MOOD_QUESTIONS)

@app.route("/login", methods=["GET","POST"])
def login():
    if "user_id" in session: return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        pw    = request.form.get("password","")
        db    = get_db()
        u     = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if u and check_password_hash(u["password"], pw):
            session["user_id"]  = u["id"]
            session["username"] = u["username"]
            session["role"]     = u["role"]
            return redirect(request.args.get("next") or url_for("home"))
        error = "Invalid email or password."
    return render_template("login.html", error=error)

@app.route("/signup", methods=["GET","POST"])
def signup():
    if "user_id" in session: return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        username = request.form.get("username","").strip()
        email    = request.form.get("email","").strip().lower()
        pw       = request.form.get("password","")
        pw2      = request.form.get("confirm","")
        if not all([username, email, pw]):
            error = "All fields are required."
        elif pw != pw2:
            error = "Passwords do not match."
        elif len(pw) < 6:
            error = "Password must be at least 6 characters."
        else:
            db = get_db()
            try:
                db.execute("INSERT INTO users(username,email,password) VALUES(?,?,?)",
                           (username, email, generate_password_hash(pw)))
                db.commit()
                u = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                session["user_id"]  = u["id"]
                session["username"] = u["username"]
                session["role"]     = u["role"]
                return redirect(url_for("home"))
            except sqlite3.IntegrityError:
                error = "That email is already registered."
    return render_template("signup.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    user   = current_user()
    favs   = fav_ids(user["id"])
    wl     = watchlist_ids(user["id"])
    hist   = history_ids(user["id"])
    fav_movies  = [m for mid in favs   if (m := get_movie_by_id(mid))]
    wl_movies   = [m for mid in wl[:8] if (m := get_movie_by_id(mid))]
    hist_movies = [m for mid in hist[:8] if (m := get_movie_by_id(mid))]
    recs        = personalized_recommend(favs, hist, 8)
    # User ratings
    db = get_db()
    rated_rows = db.execute(
        "SELECT movie_id, score, review, posted_at FROM ratings WHERE user_id=? ORDER BY posted_at DESC",
        (user["id"],)
    ).fetchall()
    rated_movies = []
    for r in rated_rows[:6]:
        m = get_movie_by_id(r["movie_id"])
        if m: rated_movies.append({**m, "user_score": r["score"], "user_review": r["review"]})
    return render_template("dashboard.html", user=user,
                           fav_movies=fav_movies, wl_movies=wl_movies,
                           hist_movies=hist_movies, recs=recs, rated_movies=rated_movies,
                           favs=favs, wl=wl, genres=get_genres())

@app.route("/movie/<int:mid>")
def movie_detail(mid):
    movie = get_movie_by_id(mid)
    if not movie: return redirect(url_for("home"))
    user  = current_user()
    favs_list  = fav_ids(user["id"]) if user else []
    wl_list    = watchlist_ids(user["id"]) if user else []
    is_fav     = mid in favs_list
    is_wl      = mid in wl_list
    user_score = None
    user_review = None
    if user:
        db = get_db()
        try:
            # Delete then re-insert to refresh watched_at timestamp on repeat visits
            db.execute("DELETE FROM watch_history WHERE user_id=? AND movie_id=?", (user["id"], mid))
            db.execute("INSERT INTO watch_history(user_id,movie_id) VALUES(?,?)", (user["id"], mid))
            db.commit()
        except: pass
        row = db.execute("SELECT score, review FROM ratings WHERE user_id=? AND movie_id=?",
                         (user["id"], mid)).fetchone()
        if row: user_score = row["score"]; user_review = row["review"]
    # All reviews for this movie
    db = get_db()
    reviews = []
    r_rows = db.execute(
        "SELECT r.score, r.review, r.posted_at, u.username FROM ratings r "
        "JOIN users u ON r.user_id=u.id WHERE r.movie_id=? AND r.review IS NOT NULL AND r.review!='' "
        "ORDER BY r.posted_at DESC LIMIT 10", (mid,)
    ).fetchall()
    for row in r_rows: reviews.append(dict(row))
    similar  = content_recommend(mid, 6)
    cast_list = [c.strip() for c in movie.get("cast","").split("|") if c.strip()][:6]
    return render_template("movie.html", movie=movie, similar=similar,
                           user=user, is_fav=is_fav, is_wl=is_wl,
                           user_score=user_score, user_review=user_review,
                           reviews=reviews, cast_list=cast_list,
                           favs=favs_list, wl=wl_list, genres=get_genres())

@app.route("/search")
def search():
    query  = request.args.get("q","").strip()
    genre  = request.args.get("genre","").strip()
    minr   = request.args.get("min_rating",0)
    sort   = request.args.get("sort","rating")
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
    user   = current_user()
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
    db = get_db()
    tab = request.args.get("tab", "overview")
    users      = db.execute("SELECT id,username,email,role,created FROM users ORDER BY created DESC").fetchall()
    total_favs = db.execute("SELECT COUNT(*) as c FROM favorites").fetchone()["c"]
    total_rat  = db.execute("SELECT COUNT(*) as c FROM ratings").fetchone()["c"]
    total_hist = db.execute("SELECT COUNT(*) as c FROM watch_history").fetchone()["c"]
    total_moods= db.execute("SELECT COUNT(*) as c FROM mood_sessions").fetchone()["c"]
    total_chats= db.execute("SELECT COUNT(*) as c FROM chat_logs WHERE role='user'").fetchone()["c"]

    # Tab-specific data
    all_ratings = []
    chat_logs_data = []
    mood_data = []

    if tab == "ratings":
        rows = db.execute(
            "SELECT r.id, r.movie_id, r.score, r.review, r.posted_at, u.username "
            "FROM ratings r JOIN users u ON r.user_id=u.id "
            "ORDER BY r.posted_at DESC LIMIT 100"
        ).fetchall()
        for row in rows:
            m = get_movie_by_id(row["movie_id"])
            all_ratings.append({
                "id":          row["id"],
                "mid":         row["movie_id"],
                "username":    row["username"],
                "movie_title": m["title"] if m else f"Movie #{row['movie_id']}",
                "score":       row["score"],
                "review":      row["review"] or "",
                "posted_at":   row["posted_at"]
            })

    if tab == "chatlogs":
        chat_logs_data = db.execute(
            "SELECT cl.id, cl.role, cl.message, cl.ts, COALESCE(u.username,'Guest') as username "
            "FROM chat_logs cl LEFT JOIN users u ON cl.user_id=u.id "
            "ORDER BY cl.ts DESC LIMIT 200"
        ).fetchall()

    if tab == "moods":
        mood_rows = db.execute(
            "SELECT ms.id, ms.answers, ms.genre_map, ms.created_at, COALESCE(u.username,'Guest') as username "
            "FROM mood_sessions ms LEFT JOIN users u ON ms.user_id=u.id "
            "ORDER BY ms.created_at DESC LIMIT 50"
        ).fetchall()
        for row in mood_rows:
            try:
                answers  = json.loads(row["answers"])
                gmap     = json.loads(row["genre_map"])
                top_genres = ", ".join(k for k,v in sorted(gmap.items(), key=lambda x: -x[1])[:3])
            except:
                answers, top_genres = {}, "—"
            mood_data.append({
                "id": row["id"], "username": row["username"],
                "answers": answers, "top_genres": top_genres,
                "created_at": row["created_at"]
            })

    return render_template("admin.html",
                           tab=tab,
                           users=users,
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
    return jsonify(m) if m else (jsonify({"error":"not found"}), 404)

@app.route("/api/search")
def api_search():
    return jsonify(search_movies(
        request.args.get("q",""),
        request.args.get("genre",""),
        float(request.args.get("min_rating",0)),
        request.args.get("sort","rating")
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

# Mood API
@app.route("/api/mood/questions")
def api_mood_questions():
    return jsonify(MOOD_QUESTIONS)

@app.route("/api/mood/recommend", methods=["POST"])
def api_mood_recommend():
    data    = request.get_json(force=True)
    answers = data.get("answers", {})
    results = mood_recommend(answers, 12)
    genre_map = compute_mood_genre_scores(answers)
    # Save session
    db = get_db()
    uid = session.get("user_id")
    try:
        db.execute("INSERT INTO mood_sessions(user_id,answers,genre_map) VALUES(?,?,?)",
                   (uid, json.dumps(answers), json.dumps(genre_map)))
        db.commit()
    except: pass
    return jsonify({"movies": results, "genre_scores": genre_map})

# Favorite
@app.route("/api/favorite", methods=["POST"])
def api_favorite():
    if "user_id" not in session: return jsonify({"error":"Login required"}), 401
    d       = request.get_json(force=True)
    mid     = d.get("movie_id")
    uid     = session["user_id"]
    db      = get_db()
    exists  = db.execute("SELECT id FROM favorites WHERE user_id=? AND movie_id=?", (uid,mid)).fetchone()
    if exists:
        db.execute("DELETE FROM favorites WHERE user_id=? AND movie_id=?", (uid,mid)); db.commit()
        return jsonify({"status":"removed"})
    db.execute("INSERT OR IGNORE INTO favorites(user_id,movie_id) VALUES(?,?)", (uid,mid)); db.commit()
    return jsonify({"status":"added"})

# Watchlist
@app.route("/api/watchlist", methods=["POST"])
def api_watchlist():
    if "user_id" not in session: return jsonify({"error":"Login required"}), 401
    d       = request.get_json(force=True)
    mid     = d.get("movie_id")
    uid     = session["user_id"]
    db      = get_db()
    exists  = db.execute("SELECT id FROM watchlist WHERE user_id=? AND movie_id=?", (uid,mid)).fetchone()
    if exists:
        db.execute("DELETE FROM watchlist WHERE user_id=? AND movie_id=?", (uid,mid)); db.commit()
        return jsonify({"status":"removed"})
    db.execute("INSERT OR IGNORE INTO watchlist(user_id,movie_id) VALUES(?,?)", (uid,mid)); db.commit()
    return jsonify({"status":"added"})

# Rating
@app.route("/api/rate", methods=["POST"])
def api_rate():
    if "user_id" not in session: return jsonify({"error":"Login required"}), 401
    d      = request.get_json(force=True)
    mid    = d.get("movie_id")
    score  = int(d.get("score",5))
    review = d.get("review","").strip()
    uid    = session["user_id"]
    db = get_db()
    db.execute("INSERT OR REPLACE INTO ratings(user_id,movie_id,score,review) VALUES(?,?,?,?)",
               (uid, mid, score, review or None))
    db.commit()
    return jsonify({"status":"rated","score":score})

# Chatbot
@app.route("/api/chat", methods=["POST"])
def api_chat():
    d    = request.get_json(force=True)
    msg  = d.get("message","").strip()
    if not msg: return jsonify({"reply":"Please type a message."})
    reply = chat_reply(msg)
    db   = get_db()
    uid  = session.get("user_id")
    try:
        db.execute("INSERT INTO chat_logs(user_id,role,message) VALUES(?,?,?)", (uid,"user",msg))
        db.execute("INSERT INTO chat_logs(user_id,role,message) VALUES(?,?,?)", (uid,"bot",reply))
        db.commit()
    except: pass
    return jsonify({"reply": reply})

# Admin: delete user
@app.route("/api/admin/user/<int:uid>", methods=["DELETE"])
@admin_required
def api_delete_user(uid):
    if uid == session["user_id"]: return jsonify({"error":"Cannot delete yourself"}), 400
    db = get_db()
    # Manually cascade in case DB was created before ON DELETE CASCADE was added
    for tbl in ["favorites","watchlist","watch_history","ratings"]:
        db.execute(f"DELETE FROM {tbl} WHERE user_id=?", (uid,))
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    return jsonify({"status":"deleted"})

# Admin: toggle role
@app.route("/api/admin/user/<int:uid>/role", methods=["POST"])
@admin_required
def api_toggle_role(uid):
    db = get_db()
    u  = db.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
    if not u: return jsonify({"error":"Not found"}), 404
    new_role = "admin" if u["role"] == "user" else "user"
    db.execute("UPDATE users SET role=? WHERE id=?", (new_role, uid)); db.commit()
    return jsonify({"status":"ok","new_role":new_role})

# Admin: delete a rating
@app.route("/api/admin/rating/<int:rid>", methods=["DELETE"])
@admin_required
def api_delete_rating(rid):
    db = get_db()
    db.execute("DELETE FROM ratings WHERE id=?", (rid,)); db.commit()
    return jsonify({"status":"deleted"})

# Admin: clear chat logs
@app.route("/api/admin/chatlogs/clear", methods=["POST"])
@admin_required
def api_clear_chatlogs():
    db = get_db()
    db.execute("DELETE FROM chat_logs"); db.commit()
    return jsonify({"status":"cleared"})

# Admin: add a movie to the in-memory catalogue
@app.route("/api/admin/movie/add", methods=["POST"])
@admin_required
def api_add_movie():
    from recommendation import SEED_MOVIES, _build_matrix
    import recommendation as rec
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    genre = (data.get("genre") or "").strip()
    desc  = (data.get("description") or "").strip()
    if not title or not genre or not desc:
        return jsonify({"error": "title, genre and description are required"}), 400
    new_id = max(m["id"] for m in SEED_MOVIES) + 1
    movie = {
        "id":          new_id,
        "title":       title,
        "genre":       genre,
        "description": desc,
        "rating":      float(data.get("rating") or 7.0),
        "year":        int(data.get("year") or 2024),
        "runtime":     int(data.get("runtime") or 120),
        "director":    data.get("director") or "",
        "cast":        data.get("cast") or "",
        "poster":      data.get("poster") or "/static/images/placeholder.svg",
        "trailer":     data.get("trailer") or "",
        "keywords":    "",
    }
    SEED_MOVIES.append(movie)
    # Rebuild the TF-IDF matrix with the new entry
    rec._df, rec._sim = _build_matrix(SEED_MOVIES)
    return jsonify({"status": "added", "id": new_id})

# Admin: stats JSON (for live refresh)
@app.route("/api/admin/stats")
@admin_required
def api_admin_stats():
    db = get_db()
    return jsonify({
        "users":   db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"],
        "favs":    db.execute("SELECT COUNT(*) as c FROM favorites").fetchone()["c"],
        "ratings": db.execute("SELECT COUNT(*) as c FROM ratings").fetchone()["c"],
        "history": db.execute("SELECT COUNT(*) as c FROM watch_history").fetchone()["c"],
        "moods":   db.execute("SELECT COUNT(*) as c FROM mood_sessions").fetchone()["c"],
        "chats":   db.execute("SELECT COUNT(*) as c FROM chat_logs WHERE role='user'").fetchone()["c"],
        "movies":  len(get_all_movies()),
    })


if __name__ == "__main__":
    init_db()
    # Create demo admin if no users exist
    with app.app_context():
        db = get_db()
        if not db.execute("SELECT id FROM users").fetchone():
            db.execute("INSERT INTO users(username,email,password,role) VALUES(?,?,?,?)",
                       ("Admin","admin@imsr.db", generate_password_hash("admin123"), "admin"))
            db.commit()
            print("Demo admin created: admin@imsr.db / admin123")
    app.run(debug=True, port=5000)
