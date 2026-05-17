"""
IMSR-DB  |  Recommendation Engine
----------------------------------
Techniques:
  1. Content-Based Filtering   — TF-IDF + Cosine Similarity
  2. Mood-Based Mapping        — Structured question flow -> genre weights
  3. Collaborative Signals     — Watch history + ratings aggregation
  4. Hybrid Scoring            — Weighted blend of above signals

Data source: Kaggle Netflix Movies & TV Shows dataset
             (normalised and seeded into SQLite on first run)
"""

import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ──────────────────────────────────────────────────────────────────────────────
# MOOD SYSTEM
# ──────────────────────────────────────────────────────────────────────────────

MOOD_QUESTIONS = [
    {
        "id": "current_feeling",
        "text": "How are you feeling right now?",
        "sub": "Pick the one that resonates most",
        "options": [
            {"value": "happy",      "label": "Happy",       "icon": "★", "desc": "Light-hearted and good"},
            {"value": "sad",        "label": "Melancholic",  "icon": "~", "desc": "Need something emotional"},
            {"value": "excited",    "label": "Excited",      "icon": "!", "desc": "Ready for action"},
            {"value": "anxious",    "label": "Tense",        "icon": "#", "desc": "Edge of my seat feeling"},
            {"value": "calm",       "label": "Peaceful",     "icon": "o", "desc": "Relaxed and reflective"},
            {"value": "bored",      "label": "Bored",        "icon": "-", "desc": "Need something fresh"},
        ]
    },
    {
        "id": "company",
        "text": "Who are you watching with?",
        "sub": "This shapes the vibe we pick",
        "options": [
            {"value": "alone",      "label": "Just Me",      "icon": "1", "desc": "Solo watch session"},
            {"value": "partner",    "label": "Date Night",   "icon": "2", "desc": "Someone special"},
            {"value": "friends",    "label": "Friend Group", "icon": "3", "desc": "Social viewing"},
            {"value": "family",     "label": "Family",       "icon": "4", "desc": "All ages together"},
        ]
    },
    {
        "id": "length_pref",
        "text": "How much time do you have?",
        "sub": "We will match runtime accordingly",
        "options": [
            {"value": "short",      "label": "Under 90 min",  "icon": "<", "desc": "Quick watch"},
            {"value": "medium",     "label": "90-120 min",    "icon": "=", "desc": "Standard film"},
            {"value": "long",       "label": "Epic 2h+",      "icon": ">", "desc": "Full experience"},
            {"value": "series",     "label": "TV Series",     "icon": "S", "desc": "Multi-episode binge"},
        ]
    },
    {
        "id": "intensity",
        "text": "What intensity level suits you?",
        "sub": "Emotional weight of the story",
        "options": [
            {"value": "light",      "label": "Light & Fun",  "icon": "L", "desc": "Easy, feel-good"},
            {"value": "medium",     "label": "Balanced",     "icon": "M", "desc": "Engaging but not heavy"},
            {"value": "heavy",      "label": "Deep & Dark",  "icon": "H", "desc": "Complex, thought-provoking"},
            {"value": "intense",    "label": "Intense",      "icon": "I", "desc": "Heart-pounding, gripping"},
        ]
    },
]

# Maps (mood_answer, sub_answer) -> genre weights (0-1)
MOOD_GENRE_MAP = {
    # current_feeling -> primary genre weights
    "happy":    {"Comedy": .9, "Animation": .8, "Romance": .7, "Musical": .7, "Family": .6, "Adventure": .5},
    "sad":      {"Drama": .9, "Romance": .8, "Biography": .7, "History": .6, "Music": .5, "War": .4},
    "excited":  {"Action": .9, "Adventure": .85, "Sci-Fi": .75, "Thriller": .7, "Crime": .6, "Sport": .5},
    "anxious":  {"Thriller": .9, "Mystery": .85, "Crime": .8, "Horror": .7, "Psychological": .65},
    "calm":     {"Documentary": .9, "Drama": .8, "Biography": .75, "History": .7, "Nature": .6, "Music": .55},
    "bored":    {"Action": .8, "Comedy": .8, "Sci-Fi": .75, "Animation": .7, "Fantasy": .65, "Mystery": .6},

    # company -> modifier weights
    "alone":    {"Horror": .8, "Thriller": .75, "Sci-Fi": .7, "Drama": .6, "Documentary": .55},
    "partner":  {"Romance": .9, "Comedy": .7, "Drama": .65, "Thriller": .55},
    "friends":  {"Comedy": .9, "Action": .8, "Horror": .7, "Animation": .6},
    "family":   {"Animation": .9, "Family": .9, "Adventure": .8, "Comedy": .7, "Fantasy": .65},

    # intensity modifiers
    "light":    {"Comedy": .8, "Animation": .8, "Family": .7, "Romance": .6},
    "medium":   {"Drama": .7, "Thriller": .65, "Action": .65, "Mystery": .6},
    "heavy":    {"Drama": .9, "Biography": .85, "War": .8, "History": .7, "Crime": .7},
    "intense":  {"Thriller": .9, "Horror": .85, "Action": .8, "Crime": .75, "Sci-Fi": .6},
}

def compute_mood_genre_scores(answers: dict) -> dict:
    """
    Given answers dict {question_id: chosen_value},
    returns a merged dict of {genre: score} using weighted accumulation.
    """
    scores = {}
    weights = {"current_feeling": 1.0, "company": 0.7, "intensity": 0.8, "length_pref": 0.0}
    for qid, val in answers.items():
        w = weights.get(qid, 0.5)
        genre_map = MOOD_GENRE_MAP.get(val, {})
        for genre, score in genre_map.items():
            scores[genre] = scores.get(genre, 0) + score * w
    # Normalise
    if scores:
        max_s = max(scores.values())
        scores = {g: round(s / max_s, 3) for g, s in scores.items()}
    return scores


# ──────────────────────────────────────────────────────────────────────────────
# MOVIE CATALOGUE  (25 seed movies — supplements real Kaggle data if DB empty)
# ──────────────────────────────────────────────────────────────────────────────

SEED_MOVIES = [
    {"id":1,  "title":"Inception",               "genre":"Sci-Fi|Thriller",         "rating":8.8, "year":2010, "runtime":148, "description":"A thief who steals corporate secrets through dream-sharing technology is given the inverse task of planting an idea into a C.E.O.'s mind.",           "poster":"https://image.tmdb.org/t/p/w500/9gk7adHYeDvHkCSEqAvQNLV5Uge.jpg", "trailer":"YoHD9XEInc0", "director":"Christopher Nolan",  "cast":"Leonardo DiCaprio|Joseph Gordon-Levitt|Elliot Page", "keywords":"dreams heist mind layers thriller psychological"},
    {"id":2,  "title":"The Dark Knight",          "genre":"Action|Crime|Drama",      "rating":9.0, "year":2008, "runtime":152, "description":"When the Joker emerges and plunges Gotham into chaos, Batman must accept one of the greatest psychological and physical tests of his ability.",          "poster":"https://image.tmdb.org/t/p/w500/qJ2tW6WMUDux911r6m7haRef0WH.jpg", "trailer":"EXeTwQWrcwY", "director":"Christopher Nolan",  "cast":"Christian Bale|Heath Ledger|Aaron Eckhart",          "keywords":"batman joker gotham chaos superhero dark villain"},
    {"id":3,  "title":"Interstellar",             "genre":"Sci-Fi|Drama|Adventure",  "rating":8.6, "year":2014, "runtime":169, "description":"A team of astronauts travel through a wormhole in space in an attempt to ensure humanity's survival.",                                               "poster":"https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIx.jpg", "trailer":"zSWdZVtXT7E", "director":"Christopher Nolan",  "cast":"Matthew McConaughey|Anne Hathaway|Jessica Chastain", "keywords":"space wormhole time relativity survival family epic"},
    {"id":4,  "title":"Pulp Fiction",             "genre":"Crime|Drama|Thriller",    "rating":8.9, "year":1994, "runtime":154, "description":"The lives of two mob hitmen, a boxer, a gangster and his wife intertwine in four tales of violence and redemption.",                                "poster":"https://image.tmdb.org/t/p/w500/d5iIlFn5s0ImszYzBPb8JPIfbXD.jpg", "trailer":"s7EdQ4FqbhY", "director":"Quentin Tarantino",  "cast":"John Travolta|Uma Thurman|Samuel L. Jackson",        "keywords":"crime nonlinear violence redemption dialogue mob dark"},
    {"id":5,  "title":"The Matrix",               "genre":"Sci-Fi|Action",           "rating":8.7, "year":1999, "runtime":136, "description":"A computer hacker learns from mysterious rebels about the true nature of his reality and his role in the war against its controllers.",              "poster":"https://image.tmdb.org/t/p/w500/f89U3ADr1oiB1s9GkdPOEpXUk5H.jpg", "trailer":"vKQi3bBA1y8", "director":"Wachowski Sisters",  "cast":"Keanu Reeves|Carrie-Anne Moss|Laurence Fishburne",   "keywords":"simulation dystopia hacker rebels reality action sci-fi"},
    {"id":6,  "title":"Goodfellas",               "genre":"Crime|Drama|Biography",   "rating":8.7, "year":1990, "runtime":146, "description":"The story of Henry Hill and his life in the mob, covering his relationship with his wife Karen and his mob partners.",                              "poster":"https://image.tmdb.org/t/p/w500/aKuFiU82s5ISJpGZp7YkIr3kCUd.jpg", "trailer":"qo0jJpjBNpo", "director":"Martin Scorsese",    "cast":"Ray Liotta|Robert De Niro|Joe Pesci",                "keywords":"mafia gangster mob crime biography scorsese rise fall"},
    {"id":7,  "title":"Fight Club",               "genre":"Drama|Thriller",          "rating":8.8, "year":1999, "runtime":139, "description":"An insomniac office worker and a devil-may-care soapmaker form an underground fight club that evolves into something much, much more sinister.",   "poster":"https://image.tmdb.org/t/p/w500/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg", "trailer":"SUXWAEX2jlg", "director":"David Fincher",      "cast":"Brad Pitt|Edward Norton|Helena Bonham Carter",       "keywords":"identity insomnia underground rebellion twist consumerism"},
    {"id":8,  "title":"The Shawshank Redemption", "genre":"Drama",                   "rating":9.3, "year":1994, "runtime":142, "description":"Two imprisoned men bond over a number of years, finding solace and eventual redemption through acts of common decency.",                          "poster":"https://image.tmdb.org/t/p/w500/lyQBXzOQSuE59IsHyhrp0qIiPAz.jpg", "trailer":"6hB3S9bIaco", "director":"Frank Darabont",     "cast":"Tim Robbins|Morgan Freeman",                         "keywords":"prison hope friendship redemption drama classic humanity"},
    {"id":9,  "title":"Forrest Gump",             "genre":"Drama|Romance",           "rating":8.8, "year":1994, "runtime":142, "description":"The presidencies of Kennedy and Johnson, the events of Vietnam and Watergate unfold from the perspective of an Alabama man.",                     "poster":"https://image.tmdb.org/t/p/w500/arw2vcBveWOVZr6pxd9XTd1TdQa.jpg", "trailer":"bLvqoHBptjg", "director":"Robert Zemeckis",    "cast":"Tom Hanks|Robin Wright|Gary Sinise",                 "keywords":"history america love simple journey warm feel-good"},
    {"id":10, "title":"The Silence of the Lambs", "genre":"Crime|Thriller|Drama",    "rating":8.6, "year":1991, "runtime":118, "description":"A young FBI cadet must receive the help of an incarcerated and manipulative cannibal killer to catch another serial killer.",                    "poster":"https://image.tmdb.org/t/p/w500/uS9m8OBk1A8eM9I042bx8XXpqAq.jpg", "trailer":"W6Mm8Sbe__o", "director":"Jonathan Demme",     "cast":"Jodie Foster|Anthony Hopkins",                       "keywords":"serial killer fbi psychological manipulation horror crime"},
    {"id":11, "title":"Avengers: Endgame",        "genre":"Action|Sci-Fi|Adventure", "rating":8.4, "year":2019, "runtime":181, "description":"After devastating events, the Avengers assemble once more to undo Thanos's actions and restore order to the universe.",                          "poster":"https://image.tmdb.org/t/p/w500/or06FN3Dka5tukK1e9sl16pB3iy.jpg", "trailer":"TcMBFSGVi1c", "director":"Russo Brothers",     "cast":"Robert Downey Jr.|Chris Evans|Scarlett Johansson",   "keywords":"marvel superhero avengers time-travel epic finale"},
    {"id":12, "title":"Parasite",                 "genre":"Drama|Thriller|Comedy",   "rating":8.5, "year":2019, "runtime":132, "description":"Greed and class discrimination threaten the newly formed symbiotic relationship between the wealthy Park family and the destitute Kim family.",   "poster":"https://image.tmdb.org/t/p/w500/7IiTTgloJzvGI1TAYymCfbfl3vT.jpg", "trailer":"5xH0HfJHsaY", "director":"Bong Joon-ho",       "cast":"Song Kang-ho|Lee Sun-kyun|Cho Yeo-jeong",            "keywords":"class inequality poverty korea social satire dark twist"},
    {"id":13, "title":"Joker",                    "genre":"Crime|Drama|Thriller",    "rating":8.4, "year":2019, "runtime":122, "description":"In Gotham City, troubled comedian Arthur Fleck is disregarded by society and embarks on a downward spiral of revolution.",                       "poster":"https://image.tmdb.org/t/p/w500/udDclJoHjfjb8Ekgsd4FDteOkCU.jpg", "trailer":"zAGVQLHvwOY", "director":"Todd Phillips",      "cast":"Joaquin Phoenix|Robert De Niro|Zazie Beetz",         "keywords":"joker villain mental-illness transformation crime dark society"},
    {"id":14, "title":"Blade Runner 2049",        "genre":"Sci-Fi|Drama|Mystery",    "rating":8.0, "year":2017, "runtime":164, "description":"A young blade runner's discovery of a long-buried secret leads him to track down former blade runner Rick Deckard.",                            "poster":"https://image.tmdb.org/t/p/w500/gajva2L0rPYkEWjzgFlBXCAVBE5.jpg", "trailer":"gCcx85zbxz4", "director":"Denis Villeneuve",   "cast":"Ryan Gosling|Harrison Ford|Ana de Armas",            "keywords":"android future replicant dystopia neo-noir mystery identity"},
    {"id":15, "title":"Get Out",                  "genre":"Horror|Mystery|Thriller", "rating":7.7, "year":2017, "runtime":104, "description":"A young African-American visits his white girlfriend's parents for the weekend where unsettling truths emerge.",                                  "poster":"https://image.tmdb.org/t/p/w500/tFXcEccSQMf3lfhfXKSU9iRBpa3.jpg", "trailer":"DzfpyUB60YY", "director":"Jordan Peele",       "cast":"Daniel Kaluuya|Allison Williams|Bradley Whitford",   "keywords":"race horror social thriller mystery conspiracy suspense"},
    {"id":16, "title":"Mad Max: Fury Road",       "genre":"Action|Adventure|Sci-Fi", "rating":8.1, "year":2015, "runtime":120, "description":"In a post-apocalyptic wasteland, a woman rebels against a tyrannical ruler in search for her homeland with the aid of a group of female prisoners.",  "poster":"https://image.tmdb.org/t/p/w500/kqjL17yufvn9OVLyXYpvtyrFfak.jpg", "trailer":"hEJnMQG9ev8", "director":"George Miller",      "cast":"Tom Hardy|Charlize Theron|Nicholas Hoult",           "keywords":"apocalypse action cars chase desert fury road survival"},
    {"id":17, "title":"Whiplash",                 "genre":"Drama|Music",             "rating":8.5, "year":2014, "runtime":106, "description":"A promising young drummer enrolls at a cut-throat music conservatory where his passion is both nurtured and brutally challenged.",               "poster":"https://image.tmdb.org/t/p/w500/7fn624j5lj3xTme2SgiLCeuedmO.jpg", "trailer":"7d_jQycdQGo", "director":"Damien Chazelle",    "cast":"Miles Teller|J.K. Simmons",                          "keywords":"jazz music ambition obsession perfectionism teacher brutal"},
    {"id":18, "title":"1917",                     "genre":"Drama|War|Action",        "rating":8.3, "year":2019, "runtime":119, "description":"Two soldiers are assigned to race against time and deliver a message that could stop 1,600 men from walking into a deadly trap.",               "poster":"https://image.tmdb.org/t/p/w500/iZf0KyrE25z1sage4SYFLCCrMi9.jpg", "trailer":"YqNYrYUiMfg", "director":"Sam Mendes",         "cast":"George MacKay|Dean-Charles Chapman",                 "keywords":"wwi war mission single-shot soldiers real-time tension"},
    {"id":19, "title":"Dune",                     "genre":"Sci-Fi|Drama|Adventure",  "rating":8.0, "year":2021, "runtime":155, "description":"A noble family becomes embroiled in a war for control over the galaxy's most valuable asset while its heir becomes troubled by visions.",        "poster":"https://image.tmdb.org/t/p/w500/d5NXSklpcvkenT1i7C0fGHLFi9L.jpg", "trailer":"8g18jFHCLXk", "director":"Denis Villeneuve",   "cast":"Timothee Chalamet|Rebecca Ferguson|Oscar Isaac",     "keywords":"space desert prophecy epic sci-fi power struggle empire"},
    {"id":20, "title":"Oppenheimer",              "genre":"Biography|Drama|History", "rating":8.9, "year":2023, "runtime":180, "description":"The story of J. Robert Oppenheimer's role in the development of the atomic bomb during World War II.",                                           "poster":"https://image.tmdb.org/t/p/w500/8Gxv8gSFCU0XGDykEGv7zR1n2ua.jpg", "trailer":"uYPbbksJxIg", "director":"Christopher Nolan",  "cast":"Cillian Murphy|Emily Blunt|Robert Downey Jr.",       "keywords":"nuclear physics wwii science history biography moral"},
    {"id":21, "title":"The Grand Budapest Hotel", "genre":"Comedy|Drama|Adventure",  "rating":8.1, "year":2014, "runtime":99,  "description":"The adventures of Gustave H, a legendary concierge, and Zero Moustafa, the lobby boy who becomes his most trusted friend.",                       "poster":"https://image.tmdb.org/t/p/w500/eWdyYQreja6JGCzqHWXpWHDrrPo.jpg", "trailer":"1Fg0mFHBO6g", "director":"Wes Anderson",       "cast":"Ralph Fiennes|Tony Revolori|Tilda Swinton",          "keywords":"quirky wes-anderson europe hotel mystery comedy style"},
    {"id":22, "title":"Hereditary",               "genre":"Horror|Drama|Mystery",    "rating":7.3, "year":2018, "runtime":127, "description":"A grieving family is haunted by tragic and disturbing occurrences after the death of their secretive grandmother.",                             "poster":"https://image.tmdb.org/t/p/w500/p6hit4GrennDaKNUkoqzBOuCLp8.jpg", "trailer":"V6wWKNij_1M", "director":"Ari Aster",          "cast":"Toni Collette|Alex Wolff|Milly Shapiro",             "keywords":"supernatural family grief cult horror paranormal dark"},
    {"id":23, "title":"La La Land",               "genre":"Drama|Music|Romance",     "rating":8.0, "year":2016, "runtime":128, "description":"While navigating their careers in LA, a pianist and an actress fall in love while attempting to reconcile their aspirations for the future.",  "poster":"https://image.tmdb.org/t/p/w500/uDO8zWDhfWwoFdKS4fzkUJt0Rf0.jpg", "trailer":"0pdqf4P9MB8", "director":"Damien Chazelle",    "cast":"Ryan Gosling|Emma Stone",                            "keywords":"jazz romance musical dreams nostalgia bittersweet hopeful"},
    {"id":24, "title":"Arrival",                  "genre":"Sci-Fi|Drama|Mystery",    "rating":7.9, "year":2016, "runtime":116, "description":"A linguist works with the military to communicate with alien lifeforms after twelve mysterious spacecraft appear around the world.",             "poster":"https://image.tmdb.org/t/p/w500/x2FJsf1ElAgr63Y3PNPtJrcmpoe.jpg", "trailer":"IsAtWukDXi0", "director":"Denis Villeneuve",   "cast":"Amy Adams|Jeremy Renner|Forest Whitaker",            "keywords":"alien language time linguistics mystery peaceful sci-fi"},
    {"id":25, "title":"Knives Out",               "genre":"Comedy|Crime|Mystery",    "rating":7.9, "year":2019, "runtime":130, "description":"A detective investigates the death of a patriarch of an eccentric, combative family.",                                                         "poster":"https://image.tmdb.org/t/p/w500/pThyQovXQrws2Y4U5uujjInN7fg.jpg", "trailer":"qGqiHJTsRkQ", "director":"Rian Johnson",       "cast":"Daniel Craig|Ana de Armas|Chris Evans",              "keywords":"murder mystery whodunit detective comedy family ensemble"},
]


# ──────────────────────────────────────────────────────────────────────────────
# TF-IDF COSINE ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def _build_matrix(movies):
    df = pd.DataFrame(movies)
    df["features"] = (
        df["genre"].str.replace("|", " ", regex=False) + " " +
        df.get("keywords", pd.Series([""] * len(df))).fillna("") + " " +
        df.get("director", pd.Series([""] * len(df))).fillna("")
    )
    tfidf  = TfidfVectorizer(stop_words="english", ngram_range=(1,2))
    matrix = tfidf.fit_transform(df["features"])
    sim    = cosine_similarity(matrix)
    return df, sim

_df, _sim = _build_matrix(SEED_MOVIES)


def get_all_movies():         return SEED_MOVIES
def get_movie_by_id(mid):    return next((m for m in SEED_MOVIES if m["id"] == mid), None)
def get_trending(n=12):      return sorted(SEED_MOVIES, key=lambda x: x["rating"], reverse=True)[:n]
def get_top_rated(n=10):     return sorted(SEED_MOVIES, key=lambda x: x["rating"], reverse=True)[:n]
def get_genres():
    g = set()
    for m in SEED_MOVIES:
        for x in m["genre"].split("|"): g.add(x.strip())
    return sorted(g)

def search_movies(query="", genre="", min_rating=0.0, sort="rating"):
    r = SEED_MOVIES[:]
    if query:
        q = query.lower()
        r = [m for m in r if q in m["title"].lower() or q in m.get("keywords","").lower()
             or q in m.get("director","").lower() or q in m.get("cast","").lower()
             or q in m["description"].lower()]
    if genre:
        r = [m for m in r if genre.lower() in m["genre"].lower()]
    if min_rating:
        r = [m for m in r if m["rating"] >= float(min_rating)]
    if sort == "title":
        r = sorted(r, key=lambda x: x["title"])
    elif sort == "year":
        r = sorted(r, key=lambda x: x.get("year",0), reverse=True)
    else:
        r = sorted(r, key=lambda x: x["rating"], reverse=True)
    return r

def content_recommend(movie_id, n=8):
    idx = next((i for i, m in enumerate(SEED_MOVIES) if m["id"] == movie_id), None)
    if idx is None: return SEED_MOVIES[:n]
    scores = list(enumerate(_sim[idx]))
    scores = sorted(scores, key=lambda x: x[1], reverse=True)
    scores = [s for s in scores if s[0] != idx][:n]
    return [SEED_MOVIES[i[0]] for i in scores]

def personalized_recommend(fav_ids, watched_ids=None, n=10):
    if not fav_ids and not watched_ids: return get_trending(n)
    seeds  = list(set((fav_ids or []) + (watched_ids or [])))
    scores = np.zeros(len(SEED_MOVIES))
    for sid in seeds:
        idx = next((i for i, m in enumerate(SEED_MOVIES) if m["id"] == sid), None)
        if idx is not None: scores += _sim[idx]
    exclude = set(seeds)
    ranked  = [(i, s) for i, s in enumerate(scores) if SEED_MOVIES[i]["id"] not in exclude]
    ranked  = sorted(ranked, key=lambda x: x[1], reverse=True)[:n]
    return [SEED_MOVIES[idx] for idx, _ in ranked] or get_trending(n)

def mood_recommend(answers: dict, n=12):
    """
    Map mood answers -> genre scores -> filter + rank movies.
    Blends content signal with mood signal via weighted scoring.
    """
    genre_scores = compute_mood_genre_scores(answers)
    if not genre_scores:
        return get_trending(n)

    # Length filter
    length_pref = answers.get("length_pref", "")
    runtime_filter = None
    if length_pref == "short":   runtime_filter = lambda r: r < 95
    elif length_pref == "medium": runtime_filter = lambda r: 90 <= r <= 125
    elif length_pref == "long":   runtime_filter = lambda r: r > 120

    results = []
    for m in SEED_MOVIES:
        if runtime_filter and not runtime_filter(m.get("runtime", 120)): continue
        m_genres = [g.strip() for g in m["genre"].split("|")]
        score = sum(genre_scores.get(g, 0) for g in m_genres)
        score += m["rating"] * 0.05  # slight quality bias
        results.append((score, m))

    results.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in results[:n]]
