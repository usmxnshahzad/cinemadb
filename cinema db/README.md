# IMSR-DB — Intelligent Movie Streaming & Recommendation Database
### DBMS Project | CS-222 | Spring 2026

---

## Quick Start (Windows)

```cmd
cd your-project-folder
pip install flask werkzeug pandas scikit-learn numpy
python app.py
```
Open http://127.0.0.1:5000

**Demo admin login:** admin@imsr.db / admin123

---

## What's New (vs v1)

### Mood Finder (Navbar Button)
- 4-step interactive questionnaire (Feeling, Company, Intensity, Runtime)
- Maps answers to genre weights via a scoring matrix
- Returns 12 personalised films, shown in an overlay grid
- Saves mood sessions to the database for analytics

### CineBot (Chat Bubble, bottom-right)
- Rule-based chatbot with pattern matching across 20+ intents
- Handles: recommendations, genre queries, director info, how-AI-works, help
- Also accessible via the lightning bolt button in the navbar
- Chat history logged to `chat_logs` table

### Admin Panel (/admin)
- User management table (toggle role, delete)
- Platform statistics (users, movies, favourites, ratings, watch events)
- Access with an admin account

### Watchlist
- Separate from Favourites — save films to watch later
- Dashboard shows watchlist row

### Reviews
- Users can write a text review alongside their star rating
- Reviews shown on movie detail page with username and date

---

## Folder Structure

```
imsr-db/
├── app.py                  Flask backend + all routes
├── recommendation.py       AI engine (TF-IDF, cosine, mood mapping)
├── database.db             SQLite — auto-created on first run
│
├── templates/
│   ├── base.html           Shared layout: navbar, mood overlay, chatbot, footer
│   ├── _movie_card.html    Reusable card partial
│   ├── home.html
│   ├── login.html
│   ├── signup.html
│   ├── dashboard.html
│   ├── movie.html          Detail + trailer + reviews + similar
│   ├── search.html
│   ├── recommend.html
│   └── admin.html
│
└── static/
    ├── css/style.css       Dark cinematic theme (no emojis)
    ├── js/script.js        Mood overlay, chatbot, favourites, watchlist
    └── images/placeholder.svg
```

---

## Database Schema

```sql
users         (id, username, email, password, role, avatar, bio, created)
favorites     (id, user_id, movie_id, added_at)
watchlist     (id, user_id, movie_id, added_at)
watch_history (id, user_id, movie_id, watched_at)
ratings       (id, user_id, movie_id, score, review, posted_at)
mood_sessions (id, user_id, answers_json, genre_map_json, created_at)
chat_logs     (id, user_id, role, message, ts)
```

---

## AI Recommendation System

| Component | Method |
|---|---|
| Content similarity | TF-IDF (genre + keywords + director) + Cosine Similarity |
| Personalisation | Aggregate similarity scores across user favourites + history |
| Mood-based | 4-question flow mapped to genre weight matrix, re-ranks catalogue |
| Chatbot | Rule-based regex pattern matching, 20+ intents |

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | /api/movies | All movies JSON |
| GET | /api/movie/:id | Single movie |
| GET | /api/search?q=&genre=&min_rating=&sort= | Search |
| GET | /api/recommend/:id | Content-based similar films |
| GET | /api/recommend/refresh | Refresh personalised picks |
| POST | /api/mood/questions | Mood questions list |
| POST | /api/mood/recommend | Submit answers, get films |
| POST | /api/favorite | Toggle favourite |
| POST | /api/watchlist | Toggle watchlist |
| POST | /api/rate | Submit rating + review |
| POST | /api/chat | Chat with CineBot |
| DELETE | /api/admin/user/:id | Delete user (admin) |
| POST | /api/admin/user/:id/role | Toggle admin role |
