import sys
print("Script started...", flush=True)

try:
    import pymysql
    print("pymysql imported OK", flush=True)
    
    db = pymysql.connect(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="Karachi2006@",
        database="stream_db"
    )
    print("Connected!", flush=True)
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) FROM movie")
    print("Movies in DB:", cursor.fetchone()[0], flush=True)
    db.close()

except Exception as e:
    print("ERROR:", type(e).__name__, str(e), flush=True)